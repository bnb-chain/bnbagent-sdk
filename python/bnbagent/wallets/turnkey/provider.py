"""Turnkey Wallet Provider — remote signing, keys in AWS Nitro Enclaves.

A pure signer over Turnkey's hosted key-management API
(https://docs.turnkey.com): the private key is generated and held inside
Turnkey's enclave and never leaves it; every sign call is an authenticated
HTTPS round-trip stamped by a locally held P-256 API key pair. No key
material ever exists on this machine.

Operational constraints that shaped this implementation (mirrors the
TypeScript ``TurnkeyWalletProvider``):

- **Every successful signature is billed** (free tier: 25/month at
  1 request/second; pay-as-you-go $0.10/signature). All client-side guards
  (SigningPolicy, chain-id pinning, input validation) run BEFORE the API
  call so a refusal never costs quota.
- **Root API keys bypass ALL Turnkey server-side policies** (root quorum).
  Production deployments must use a non-root API user plus an explicit
  ALLOW policy; this cannot be detected client-side.
- **Broadcast is not included** (managed broadcast is a paid feature). The
  provider only signs; the default ``LocalExecutor`` broadcasts over the
  SDK's own RPC, and the MegaFuel paymaster path works unchanged.
- **EIP-712 payloads are built here, domain included.** The full typed-data
  JSON goes to the enclave (``PAYLOAD_ENCODING_EIP712``), where server-side
  policies can filter on domain / primary type / message. Unlike the
  TypeScript path through ``@turnkey/viem`` (which silently serializes a
  missing ``EIP712Domain`` type as ``{}``), this module constructs the
  payload itself and always includes the full ``EIP712Domain`` entry — the
  same trap, immunized by construction and pinned by tests.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import rlp
from eth_account.messages import defunct_hash_message, encode_typed_data
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes

from ...signing import SigningPolicy, infer_primary_type
from ...signing import check as _policy_check
from ..capabilities import CALLS_ARBITRARY, PAYMASTER_SPONSOR
from ..wallet_provider import WalletProvider
from .client import TURNKEY_API_BASE_URL_DEFAULT, TurnkeyClient

# JSON numbers above 2**53-1 lose precision in double-based parsers, so big
# uint256 values are serialized as decimal strings (the enclave accepts
# both; ``@turnkey/viem`` does the same for JS bigints).
_JSON_SAFE_INT_MAX = 2**53 - 1

# EIP-712 canonical domain field order; only fields present in the domain
# are included (mirrors viem's ``getTypesForEIP712Domain``).
_EIP712_DOMAIN_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "string"),
    ("version", "string"),
    ("chainId", "uint256"),
    ("verifyingContract", "address"),
    ("salt", "bytes32"),
)


def _map_vendor_error(op: str, error: Exception) -> Exception:
    """Rewrite recognizable Turnkey API failures into actionable errors.

    Detection is string/shape-based (same heuristics as the TypeScript
    provider); unrecognized errors pass through untouched.
    """
    message = str(error)
    lowered = message.lower()
    status = getattr(error, "status_code", None)
    if "quota" in lowered:
        return RuntimeError(
            f"Turnkey signature quota exhausted during {op} (free tier: 25 "
            "billed signatures/month; pay-as-you-go $0.10/signature) — check "
            "the Turnkey billing dashboard."
        )
    if status == 429 or re.search(r"rate.?limit", message, re.IGNORECASE):
        return RuntimeError(
            f"Turnkey rate limit hit during {op} (free tier allows 1 "
            "request/second) — pace calls or upgrade the plan."
        )
    if "policy" in lowered and ("denied" in lowered or "reject" in lowered):
        return RuntimeError(
            f"Turnkey server-side policy denied {op} — verify the API user "
            "has an explicit ALLOW policy covering this operation (non-root "
            "users are default-deny)."
        )
    return error


def _json_safe(value: Any) -> Any:
    """Make a typed-data value JSON-serializable without changing its hash.

    Ints beyond the double-precision range become decimal strings; bytes
    become 0x-hex strings (EIP-712 encoders treat all three spellings
    identically).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -_JSON_SAFE_INT_MAX <= value <= _JSON_SAFE_INT_MAX else str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _eip712_domain_types(domain: dict[str, Any]) -> list[dict[str, str]]:
    """Field descriptors for the ``EIP712Domain`` struct (present keys only)."""
    return [
        {"name": name, "type": type_}
        for name, type_ in _EIP712_DOMAIN_FIELDS
        if domain.get(name) is not None
    ]


# ── Unsigned-transaction serialization (legacy + EIP-1559) ────────────────
#
# Hand-rolled over the ``rlp`` package (a stable eth-account transitive
# dependency) instead of eth_account's private ``_utils`` modules, whose
# paths shift between releases. Only the two shapes the SDK produces are
# supported; correctness is pinned by round-trip unit tests and the live
# E2E (the enclave parses these bytes).


def _int_field(tx: dict[str, Any], key: str, default: int | None = None) -> int:
    value = tx.get(key, default)
    if value is None:
        raise ValueError(f"transaction is missing required field {key!r}")
    return int(value)


def _address_bytes(value: Any) -> bytes:
    if value in (None, "", "0x"):
        return b""  # contract creation
    return bytes.fromhex(str(value)[2:] if str(value).startswith("0x") else str(value))


def _data_bytes(value: Any) -> bytes:
    if value in (None, "", "0x"):
        return b""
    text = str(value)
    return bytes.fromhex(text[2:] if text.startswith("0x") else text)


def _serialize_unsigned_transaction(tx: dict[str, Any]) -> bytes:
    """Serialize a legacy or EIP-1559 transaction dict to unsigned bytes.

    Type selection mirrors viem: presence of ``maxFeePerGas`` /
    ``maxPriorityFeePerGas`` selects EIP-1559, otherwise legacy
    (``gasPrice``). Access lists and blob fields are not supported.
    """
    chain_id = _int_field(tx, "chainId")
    nonce = _int_field(tx, "nonce")
    gas = _int_field(tx, "gas")
    to = _address_bytes(tx.get("to"))
    value = _int_field(tx, "value", 0)
    data = _data_bytes(tx.get("data"))
    if tx.get("accessList"):
        raise ValueError("the turnkey provider does not support accessList transactions")

    if "maxFeePerGas" in tx or "maxPriorityFeePerGas" in tx:
        max_priority = _int_field(tx, "maxPriorityFeePerGas")
        max_fee = _int_field(tx, "maxFeePerGas")
        return b"\x02" + rlp.encode(
            [chain_id, nonce, max_priority, max_fee, gas, to, value, data, []]
        )

    gas_price = _int_field(tx, "gasPrice")
    # Unsigned EIP-155 payload: the chain id takes the signature slots.
    return rlp.encode([nonce, gas_price, gas, to, value, data, chain_id, 0, 0])


def _decode_int(value: bytes) -> int:
    return int.from_bytes(value, "big") if value else 0


def _parse_signed_transaction(raw: bytes) -> dict[str, int]:
    """Extract ``r``/``s``/``v`` from a signed legacy or EIP-1559 RLP.

    ``v`` follows eth-account semantics: the EIP-155 value for legacy
    transactions, the y-parity bit (0/1) for typed transactions.
    """
    if raw[:1] == b"\x02":
        fields = rlp.decode(raw[1:])
        if len(fields) != 12:
            raise ValueError(f"unexpected EIP-1559 transaction shape ({len(fields)} fields)")
        return {
            "v": _decode_int(fields[9]),
            "r": _decode_int(fields[10]),
            "s": _decode_int(fields[11]),
        }
    fields = rlp.decode(raw)
    if len(fields) != 9:
        raise ValueError(f"unexpected legacy transaction shape ({len(fields)} fields)")
    return {
        "v": _decode_int(fields[6]),
        "r": _decode_int(fields[7]),
        "s": _decode_int(fields[8]),
    }


class TurnkeyWalletProvider(WalletProvider):
    """Wallet provider backed by Turnkey's remote enclave signing service.

    A pure signer: implements the three ``sign_*`` methods (capabilities
    derive automatically) and inherits the default ``LocalExecutor`` path,
    so ERC-8004 / ERC-8183 writes, x402 payments (via ``X402Signer``) and
    MegaFuel sponsorship all work without Turnkey-specific wiring.

    Construction is cheap and offline — the HTTP client (and its optional
    ``cryptography`` dependency) is built lazily on the first sign call.

    Args:
        organization_id: Turnkey organization id (dashboard → settings).
        sign_with: The wallet account to sign with — MUST be the account's
            Ethereum address (``0x`` + 40 hex chars), not a Turnkey wallet
            id or private-key id.
        api_public_key: Compressed P-256 API public key (dashboard → API keys).
        api_private_key: P-256 API private key hex. A client credential —
            never leaves this process.
        api_base_url: API host override (default ``https://api.turnkey.com``).
        expected_chain_id: When set, :meth:`sign_transaction` refuses any
            transaction whose ``chainId`` differs — fail-closed BEFORE the
            billable API call.
        signing_policy: Policy applied to every :meth:`sign_typed_data`
            call, BEFORE the billable API call. Defaults to
            :meth:`SigningPolicy.strict_default`. This client-side gate is
            the first of two layers — Turnkey's server-side policy engine is
            the second (and is bypassed entirely for root API users).
        client: Test seam — a pre-built object with the
            :class:`~bnbagent.wallets.turnkey.client.TurnkeyClient` surface.
    """

    kind = "turnkey"
    # Arbitrary mechanical contract calls via LocalExecutor; sponsored
    # broadcast via the MegaFuel paymaster (gasPrice=0 legacy signing
    # verified against the enclave, probe 2026-07-27). sign.* derive
    # automatically since all three sign methods below are overridden.
    _extra_capabilities = frozenset({CALLS_ARBITRARY, PAYMASTER_SPONSOR})

    def __init__(
        self,
        *,
        organization_id: str,
        sign_with: str,
        api_public_key: str,
        api_private_key: str,
        api_base_url: str | None = None,
        expected_chain_id: int | None = None,
        signing_policy: SigningPolicy | None = None,
        client: TurnkeyClient | None = None,
    ) -> None:
        for name, value in (
            ("organization_id", organization_id),
            ("sign_with", sign_with),
            ("api_public_key", api_public_key),
            ("api_private_key", api_private_key),
        ):
            if not value:
                raise ValueError(f"TurnkeyWalletProvider: {name!r} is required")
        if len(sign_with) != 42 or not sign_with.startswith("0x"):
            raise ValueError(
                "TURNKEY_SIGN_WITH must be the wallet account's Ethereum "
                "address (0x + 40 hex chars), not a Turnkey wallet id or "
                "private-key id — copy the address from the Turnkey dashboard "
                f"wallet-account view. Got: {sign_with!r}"
            )
        try:
            self._address = to_checksum_address(sign_with)
        except ValueError as exc:
            raise ValueError(
                f"TURNKEY_SIGN_WITH is not a valid Ethereum address: {sign_with!r}"
            ) from exc
        self._organization_id = organization_id
        self._api_public_key = api_public_key
        self._api_private_key = api_private_key
        self._api_base_url = api_base_url or TURNKEY_API_BASE_URL_DEFAULT
        self._expected_chain_id = expected_chain_id
        self._signing_policy = signing_policy or SigningPolicy.strict_default()
        self._client = client

    @classmethod
    def from_env(
        cls,
        *,
        expected_chain_id: int | None = None,
        signing_policy: SigningPolicy | None = None,
    ) -> TurnkeyWalletProvider:
        """Build a provider from the ``TURNKEY_*`` environment variables.

        Required: ``TURNKEY_API_PUBLIC_KEY``, ``TURNKEY_API_PRIVATE_KEY``,
        ``TURNKEY_ORG_ID``, ``TURNKEY_SIGN_WITH``. Optional:
        ``TURNKEY_API_BASE_URL``.
        """
        values = {
            key: (os.environ.get(key) or "").strip()
            for key in (
                "TURNKEY_API_PUBLIC_KEY",
                "TURNKEY_API_PRIVATE_KEY",
                "TURNKEY_ORG_ID",
                "TURNKEY_SIGN_WITH",
            )
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError(
                "TurnkeyWalletProvider.from_env: missing required env vars: "
                f"{', '.join(missing)}. The values come from the Turnkey "
                "dashboard (API keys, organization settings, wallet account "
                "address)."
            )
        return cls(
            api_public_key=values["TURNKEY_API_PUBLIC_KEY"],
            api_private_key=values["TURNKEY_API_PRIVATE_KEY"],
            organization_id=values["TURNKEY_ORG_ID"],
            sign_with=values["TURNKEY_SIGN_WITH"],
            api_base_url=os.environ.get("TURNKEY_API_BASE_URL") or None,
            expected_chain_id=expected_chain_id,
            signing_policy=signing_policy,
        )

    # ── Introspection ─────────────────────────────────────────────────

    @property
    def address(self) -> str:
        return self._address

    @property
    def key_location(self) -> str:
        return (
            f"remote:turnkey ({self._api_base_url}; key held in AWS Nitro enclave, never leaves)"
        )

    @property
    def signing_policy(self) -> SigningPolicy:
        """The SigningPolicy currently enforcing sign_typed_data calls."""
        return self._signing_policy

    @property
    def expected_chain_id(self) -> int | None:
        """The chain id this provider is pinned to, if any."""
        return self._expected_chain_id

    # ── Signing ───────────────────────────────────────────────────────

    def _get_client(self) -> TurnkeyClient:
        if self._client is None:
            self._client = TurnkeyClient(
                api_base_url=self._api_base_url,
                api_public_key=self._api_public_key,
                api_private_key=self._api_private_key,
                organization_id=self._organization_id,
            )
        return self._client

    def _sign_digest_blind(self, op: str, digest: bytes) -> dict[str, Any]:
        """Blind-sign a 32-byte digest (``HEXADECIMAL`` + ``NO_OP``)."""
        return self._raw_payload_signature(
            op,
            payload="0x" + digest.hex(),
            encoding="PAYLOAD_ENCODING_HEXADECIMAL",
        )

    def _raw_payload_signature(self, op: str, *, payload: str, encoding: str) -> dict[str, Any]:
        client = self._get_client()
        try:
            result = client.sign_raw_payload(
                sign_with=self._address,
                payload=payload,
                encoding=encoding,
                hash_function="HASH_FUNCTION_NO_OP",
            )
        except Exception as exc:  # noqa: BLE001 - mapped and re-raised
            raise _map_vendor_error(op, exc) from exc
        r = int(result["r"], 16)
        s = int(result["s"], 16)
        # The API returns the recovery id ("00"/"01"); normalize to 27/28.
        v = int(result["v"], 16) + 27
        signature = HexBytes(r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([v]))
        return {"r": r, "s": s, "v": v, "signature": signature}

    def sign_message(self, message: str) -> dict[str, Any]:
        """Sign a message using EIP-191 personal sign.

        The digest is hashed locally and blind-signed by the enclave, so
        Turnkey's server-side policies cannot see the message content —
        content-level control lives in this SDK's client-side policy layer.
        """
        digest = defunct_hash_message(text=message)
        signed = self._sign_digest_blind("sign_message", bytes(digest))
        return {"messageHash": HexBytes(digest), **signed}

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign EIP-712 typed data after passing the configured SigningPolicy.

        The policy check runs BEFORE the billable API call, so a refusal
        costs no quota. The full typed-data document (domain included) goes
        to the enclave, where server-side policies can filter on
        ``eth.eip_712.domain`` / ``primary_type`` / ``message``.
        """
        _policy_check(self._signing_policy, domain, types, message)
        message_types = {k: v for k, v in types.items() if k != "EIP712Domain"}
        primary_type = infer_primary_type(types)

        # Local digest for the SignatureResult (and signature verification).
        signable = encode_typed_data(
            domain_data=domain, message_types=message_types, message_data=message
        )
        digest = keccak(b"\x19" + signable.version + signable.header + signable.body)

        payload = json.dumps(
            {
                "types": {
                    "EIP712Domain": _eip712_domain_types(domain),
                    **_json_safe(message_types),
                },
                "domain": _json_safe(domain),
                "primaryType": primary_type,
                "message": _json_safe(message),
            },
            separators=(",", ":"),
        )
        signed = self._raw_payload_signature(
            "sign_typed_data",
            payload=payload,
            encoding="PAYLOAD_ENCODING_EIP712",
        )
        return {"messageHash": HexBytes(digest), **signed}

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """Sign a legacy or EIP-1559 transaction via the enclave.

        When the provider was constructed with ``expected_chain_id``, a
        mismatching ``chainId`` is refused before the billable API call.
        """
        chain_id = transaction.get("chainId")
        if self._expected_chain_id is not None and chain_id != self._expected_chain_id:
            raise ValueError(
                f"Refusing to sign for chainId={chain_id}: this Turnkey "
                f"provider is pinned to chainId={self._expected_chain_id} "
                "(every Turnkey signature is billed, so the mismatch fails "
                "closed before the API call)."
            )
        unsigned = _serialize_unsigned_transaction(transaction)
        client = self._get_client()
        try:
            signed_hex = client.sign_transaction(
                sign_with=self._address,
                unsigned_transaction=unsigned.hex(),
            )
        except Exception as exc:  # noqa: BLE001 - mapped and re-raised
            raise _map_vendor_error("sign_transaction", exc) from exc
        raw = HexBytes(signed_hex)
        parsed = _parse_signed_transaction(bytes(raw))
        return {
            "rawTransaction": raw,
            "hash": HexBytes(keccak(bytes(raw))),
            "r": parsed["r"],
            "s": parsed["s"],
            "v": parsed["v"],
        }
