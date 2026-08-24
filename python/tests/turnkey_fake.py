"""A faithful in-process fake of the Turnkey signing enclave.

Implements the :class:`bnbagent.wallets.turnkey.client.TurnkeyClient`
surface with ``eth_account`` as the "enclave": signatures are real and
recoverable, and the EIP-712 path re-parses the provider's JSON payload
with an independent implementation — so a malformed payload (e.g. a missing
``EIP712Domain`` entry) fails here the same way it would fail verification
against the real service.

Never dials the network; used by the provider and conformance suites.
"""

from __future__ import annotations

import json
import re
from typing import Any

import rlp
from eth_account import Account
from eth_utils import to_checksum_address

_DECIMAL_RE = re.compile(r"^\d+$")


def _coerce_document(document: dict[str, Any]) -> dict[str, Any]:
    """Undo the provider's big-int→decimal-string JSON conversion, type-aware.

    The real enclave parses either spelling for numeric fields (proven by
    the 2026-07 probes, where ``@turnkey/viem`` sent JS bigints as
    strings); ``eth_account``'s encoder wants ints. Coercion follows the
    declared field types — a ``string`` field that happens to contain
    digits (e.g. ``version: "1"``) must stay a string.
    """
    types: dict[str, list[dict[str, str]]] = document.get("types", {})

    def coerce_value(field_type: str, value: Any) -> Any:
        if field_type.endswith("]"):
            base = field_type[: field_type.rindex("[")]
            return [coerce_value(base, item) for item in value]
        if field_type in types:
            return coerce_struct(field_type, value)
        if (
            field_type.startswith(("uint", "int"))
            and isinstance(value, str)
            and _DECIMAL_RE.match(value)
        ):
            return int(value)
        return value

    def coerce_struct(struct_name: str, value: dict[str, Any]) -> dict[str, Any]:
        fields = {f["name"]: f["type"] for f in types.get(struct_name, [])}
        return {key: coerce_value(fields.get(key, ""), item) for key, item in value.items()}

    return {
        **document,
        "domain": coerce_struct("EIP712Domain", document.get("domain", {})),
        "message": coerce_struct(str(document.get("primaryType")), document.get("message", {})),
    }


def _int_of(field: bytes) -> int:
    return int.from_bytes(field, "big") if field else 0


def _decode_unsigned_transaction(raw: bytes) -> dict[str, Any]:
    """Decode the provider's unsigned RLP back into an eth_account tx dict."""
    if raw[:1] == b"\x02":
        fields = rlp.decode(raw[1:])
        assert len(fields) == 9, f"unexpected 1559 unsigned shape: {len(fields)}"
        tx: dict[str, Any] = {
            "type": 2,
            "chainId": _int_of(fields[0]),
            "nonce": _int_of(fields[1]),
            "maxPriorityFeePerGas": _int_of(fields[2]),
            "maxFeePerGas": _int_of(fields[3]),
            "gas": _int_of(fields[4]),
            "value": _int_of(fields[6]),
            "data": "0x" + fields[7].hex(),
            "accessList": [],
        }
        if fields[5]:
            tx["to"] = to_checksum_address("0x" + fields[5].hex())
        return tx
    fields = rlp.decode(raw)
    assert len(fields) == 9, f"unexpected legacy unsigned shape: {len(fields)}"
    assert _int_of(fields[7]) == 0 and _int_of(fields[8]) == 0, (
        "legacy unsigned payload must end with the EIP-155 (chainId, 0, 0) triplet"
    )
    tx = {
        "nonce": _int_of(fields[0]),
        "gasPrice": _int_of(fields[1]),
        "gas": _int_of(fields[2]),
        "value": _int_of(fields[4]),
        "data": "0x" + fields[5].hex(),
        "chainId": _int_of(fields[6]),
    }
    if fields[3]:
        tx["to"] = to_checksum_address("0x" + fields[3].hex())
    return tx


class FakeTurnkeyClient:
    """Signs like the enclave, records like a probe.

    Attributes:
        raw_payload_calls: Every ``sign_raw_payload`` invocation's kwargs.
        transaction_calls: Every ``sign_transaction`` unsigned hex.
        failure: When set, the next call raises it (vendor-error testing).
    """

    def __init__(self, private_key: str) -> None:
        self._account = Account.from_key(private_key)
        self.raw_payload_calls: list[dict[str, Any]] = []
        self.transaction_calls: list[str] = []
        self.failure: Exception | None = None

    @property
    def address(self) -> str:
        return self._account.address

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure

    def sign_raw_payload(
        self, *, sign_with: str, payload: str, encoding: str, hash_function: str
    ) -> dict[str, str]:
        self._maybe_fail()
        self.raw_payload_calls.append(
            {
                "sign_with": sign_with,
                "payload": payload,
                "encoding": encoding,
                "hash_function": hash_function,
            }
        )
        assert hash_function == "HASH_FUNCTION_NO_OP", hash_function
        if encoding == "PAYLOAD_ENCODING_HEXADECIMAL":
            digest = bytes.fromhex(payload[2:] if payload.startswith("0x") else payload)
            signed = self._account.unsafe_sign_hash(digest)
        elif encoding == "PAYLOAD_ENCODING_EIP712":
            document = json.loads(payload)
            assert "EIP712Domain" in document.get("types", {}), (
                "typed-data payload reached the enclave WITHOUT an "
                "EIP712Domain entry — the 0.14.x stripping trap"
            )
            signed = self._account.sign_typed_data(full_message=_coerce_document(document))
        else:  # pragma: no cover - guards fake misuse
            raise AssertionError(f"unexpected encoding {encoding!r}")
        return {
            "r": format(signed.r, "064x"),
            "s": format(signed.s, "064x"),
            # The API returns the recovery id, not 27/28.
            "v": format(signed.v - 27, "02x"),
        }

    def sign_transaction(self, *, sign_with: str, unsigned_transaction: str) -> str:
        self._maybe_fail()
        self.transaction_calls.append(unsigned_transaction)
        tx = _decode_unsigned_transaction(bytes.fromhex(unsigned_transaction))
        signed = self._account.sign_transaction(tx)
        return bytes(signed.raw_transaction).hex()
