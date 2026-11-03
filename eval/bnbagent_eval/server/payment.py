"""``X402Gate``: the server side of x402.

The bnbagent SDK is client-only, so this module verifies exactly what its clients produce:

* **eip3009** (``bnbagent.x402.X402Signer`` + ``examples/x402/buyer_demo.py``): ``X-PAYMENT`` is
  ``base64(json({x402Version, scheme: "exact", network: "eip155:<id>", payload: {signature,
  authorization: {from, to, value, validAfter, validBefore, nonce}}}))`` with numbers as
  decimal strings and ``nonce`` a 0x bytes32. The signature is EIP-712
  ``TransferWithAuthorization`` under the token domain ``{name, version, chainId,
  verifyingContract: asset}`` (name / version from the catalogue, sent as ``extra``).
* **permit2-exact** (the b402 / x402 v2 dialect normalised by the SDK's
  ``normalizeX402PaymentHeader``): ``payload: {signature, permit2Authorization: {permitted:
  {token, amount}, from, spender, nonce, deadline, witness: {to, validAfter, extra}}}``
  (legacy ``payload.permit`` + ``payload.from`` is accepted too), signed as Permit2
  ``PermitWitnessTransferFrom`` with the ``Witness(address to,uint256 validAfter,bytes extra)``
  witness under the Permit2 domain ``{name: "Permit2", chainId, verifyingContract: permit2}``.

Nonces are reserved atomically in sqlite (``UNIQUE (asset, payer, nonce)``), so replays fail
sequentially, concurrently and across restarts. This module imports neither ``bnbagent`` nor
``web3``; ``eth_account`` is loaded lazily (``[evm]`` extra).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from .._optional import require
from ..hashing import hash_obj
from ..logging_utils import get_logger
from ..ports import AssetCatalogPort, AssetSpec, SettlementPort
from .payment_base import PaymentError, PaymentGate, PaymentReceipt, Quote

logger = get_logger(__name__)

PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"  # canonical Uniswap Permit2
MAX_HEADER_BYTES = 16_384

EIP712_DOMAIN_FIELDS = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
TRANSFER_WITH_AUTHORIZATION_FIELDS = [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]
PERMIT2_DOMAIN_FIELDS = [
    {"name": "name", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]
PERMIT2_WITNESS_TYPES = {
    "PermitWitnessTransferFrom": [
        {"name": "permitted", "type": "TokenPermissions"},
        {"name": "spender", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "witness", "type": "Witness"},
    ],
    "TokenPermissions": [
        {"name": "token", "type": "address"},
        {"name": "amount", "type": "uint256"},
    ],
    "Witness": [
        {"name": "to", "type": "address"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "extra", "type": "bytes"},
    ],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    amount TEXT NOT NULL,
    asset TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    pay_to TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    judge_calls INTEGER NOT NULL,
    methods TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nonces (
    asset TEXT NOT NULL,
    payer TEXT NOT NULL,
    nonce TEXT NOT NULL,
    reservation TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    status TEXT NOT NULL,
    tx_hash TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (asset, payer, nonce)
);
"""


def default_price_fn(
    base_fee: int = 10_000, per_call_fee: int = 1_000
) -> Callable[[Mapping[str, Any], int], int]:
    """``base_fee + judge_calls × per_call_fee`` (atomic units of the quote asset)."""

    def price(request: Mapping[str, Any], judge_calls: int) -> int:
        return base_fee + max(0, int(judge_calls)) * per_call_fee

    return price


class _Store:
    """sqlite quote / nonce store; one short-lived connection per operation."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def put_quote(self, q: Quote, now: int) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    q.quote_id,
                    q.request_hash,
                    str(q.amount),
                    q.asset,
                    q.chain_id,
                    q.pay_to,
                    q.expires_at,
                    q.judge_calls,
                    json.dumps(list(q.methods)),
                    now,
                ),
            )

    def get_quote(self, quote_id: str) -> Quote | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT quote_id, request_hash, amount, asset, chain_id, pay_to, expires_at,"
                " judge_calls, methods FROM quotes WHERE quote_id = ?",
                (quote_id,),
            ).fetchone()
        if row is None:
            return None
        return Quote(
            quote_id=row[0],
            request_hash=row[1],
            amount=int(row[2]),
            asset=row[3],
            chain_id=int(row[4]),
            pay_to=row[5],
            expires_at=int(row[6]),
            judge_calls=int(row[7]),
            methods=tuple(json.loads(row[8])),
        )

    def reserve(self, asset: str, payer: str, nonce: str, quote_id: str, now: int) -> str | None:
        """Insert the nonce inside a transaction; ``None`` if it already exists."""
        reservation = secrets.token_hex(16)
        with closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO nonces (asset, payer, nonce, reservation, quote_id, status,"
                    " created_at) VALUES (?,?,?,?,?,'reserved',?)",
                    (asset, payer, nonce, reservation, quote_id, now),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return None
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return reservation

    def mark(self, reservation: str, status: str, tx_hash: str | None = None) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE nonces SET status = ?, tx_hash = ? WHERE reservation = ?",
                (status, tx_hash, reservation),
            )

    def release(self, reservation: str) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "DELETE FROM nonces WHERE reservation = ? AND status = 'reserved'",
                (reservation,),
            )


def _fail(code: str, message: str, status: int = 402) -> PaymentError:
    return PaymentError(message, code=code, status=status)


def _uint(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise _fail("malformed", f"{what} must be an unsigned integer")
    if isinstance(value, int):
        out = value
    elif isinstance(value, str) and value.isdigit() and value.isascii():
        out = int(value)
    else:
        raise _fail("malformed", f"{what} must be an unsigned integer")
    if out < 0 or out >= 2**256:
        raise _fail("malformed", f"{what} out of uint256 range")
    return out


def _hex_bytes(value: Any, what: str, *, length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise _fail("malformed", f"{what} must be 0x-hex")
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError as exc:
        raise _fail("malformed", f"{what} must be 0x-hex") from exc
    if length is not None and len(raw) != length:
        raise _fail("malformed", f"{what} must be {length} bytes")
    return raw


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("malformed", f"{what} must be an object")
    return dict(value)


class X402Gate(PaymentGate):
    """x402 v2 gate for eip3009 and permit2-exact payments.

    ``price_fn`` returns atomic units of the quote asset (``asset``, or the first catalogue
    asset); other catalogue assets are offered at the same nominal price scaled by decimals.
    permit2-exact routes are offered only when ``permit2_spender`` (the x402 Permit2 proxy that
    pulls funds and enforces ``witness.to``) is configured.
    """

    def __init__(
        self,
        *,
        pay_to: str,
        chain_id: int,
        assets: AssetCatalogPort,
        settlement: SettlementPort,
        store_path: str | Path,
        price_fn: Callable[[Mapping[str, Any], int], int] | None = None,
        quote_ttl_s: int = 300,
        network_name: str | None = None,
        clock: Callable[[], float] = time.time,
        asset: str | None = None,
        permit2_spender: str | None = None,
        permit2_address: str = PERMIT2_ADDRESS,
        resource: str = "/v1/evaluate",
        max_timeout_s: int = 300,
        base_fee: int = 10_000,
        per_call_fee: int = 1_000,
    ):
        self._eth_utils = require("eth_utils", "evm")
        self.pay_to = self._checksum(pay_to)
        self.chain_id = int(chain_id)
        self.assets = assets
        self.settlement = settlement
        self.price_fn = price_fn or default_price_fn(base_fee, per_call_fee)
        self.quote_ttl_s = int(quote_ttl_s)
        self.network = network_name or f"eip155:{self.chain_id}"
        self._networks = {self.network, f"eip155:{self.chain_id}"}
        self.clock = clock
        self.asset = self._checksum(asset) if asset else None
        self.permit2_spender = self._checksum(permit2_spender) if permit2_spender else None
        self.permit2_address = self._checksum(permit2_address)
        self.resource = resource
        self.max_timeout_s = int(max_timeout_s)
        self._store = _Store(store_path)

    # ------------------------------------------------------------------ helpers

    def _checksum(self, address: str) -> str:
        if not isinstance(address, str) or not self._eth_utils.is_address(address):
            raise ValueError(f"not an address: {address!r}")
        return str(self._eth_utils.to_checksum_address(address))

    def _address(self, value: Any, what: str) -> str:
        if not isinstance(value, str) or not self._eth_utils.is_hex_address(value):
            raise _fail("malformed", f"{what} must be an address")
        return str(self._eth_utils.to_checksum_address(value))

    def _now(self) -> int:
        return int(self.clock())

    def _methods(self, spec: AssetSpec) -> list[str]:
        out = []
        for m in spec.methods:
            if m == "eip3009" and spec.eip712_domain:
                out.append(m)
            elif m == "permit2-exact" and self.permit2_spender:
                out.append(m)
        return out

    def _offered(self) -> list[AssetSpec]:
        specs = [s for s in self.assets.assets(self.chain_id) if self._methods(s)]
        if self.asset is not None:
            specs = [s for s in specs if s.address.lower() == self.asset.lower()]
        return specs

    def _spec(self, address: str) -> AssetSpec | None:
        return next((s for s in self._offered() if s.address.lower() == address.lower()), None)

    def _amount_for(self, quote: Quote, spec: AssetSpec) -> int:
        base = next(
            (
                s
                for s in self.assets.assets(quote.chain_id)
                if s.address.lower() == quote.asset.lower()
            ),
            None,
        )
        if base is None or base.decimals == spec.decimals:
            return quote.amount
        if spec.decimals > base.decimals:
            return quote.amount * 10 ** (spec.decimals - base.decimals)
        div = 10 ** (base.decimals - spec.decimals)
        return -(-quote.amount // div)  # round up: never undercharge

    def load_quote(self, quote_id: str) -> Quote | None:
        """The persisted quote with this ID (``None`` if unknown)."""
        return self._store.get_quote(quote_id)

    # ------------------------------------------------------------------ PaymentGate

    async def quote(self, request: Mapping[str, Any], *, judge_calls: int) -> Quote | None:
        amount = int(self.price_fn(request, judge_calls))
        if amount <= 0:
            return None
        offered = self._offered()
        if not offered:
            raise ValueError(f"no payable asset for chain_id={self.chain_id}")
        primary = offered[0]
        methods = tuple(dict.fromkeys(m for s in offered for m in self._methods(s)))
        now = self._now()
        q = Quote(
            quote_id=secrets.token_urlsafe(18),
            request_hash=hash_obj(dict(request)),
            amount=amount,
            asset=self._checksum(primary.address),
            chain_id=self.chain_id,
            pay_to=self.pay_to,
            expires_at=now + self.quote_ttl_s,
            judge_calls=int(judge_calls),
            methods=methods,
        )
        await asyncio.to_thread(self._store.put_quote, q, now)
        return q

    def _accept(self, quote: Quote, spec: AssetSpec, method: str) -> dict[str, Any]:
        domain = dict(spec.eip712_domain or {})
        amount = str(self._amount_for(quote, spec))
        extra: dict[str, Any] = {
            "name": domain.get("name", spec.symbol),
            "version": domain.get("version", "1"),
            "assetTransferMethod": method,
            "quoteId": quote.quote_id,
        }
        entry: dict[str, Any] = {
            "scheme": "exact",
            "network": self.network,
            "amount": amount,
            "asset": self._checksum(spec.address),
            "payTo": quote.pay_to,
            "resource": self.resource,
            "mimeType": "application/json",
            "maxTimeoutSeconds": self.max_timeout_s,
            "transferMethod": method,
            "extra": extra,
        }
        if method == "eip3009":
            # v1 spelling; the SDK's TS permit2 matcher rejects entries carrying both.
            entry["maxAmountRequired"] = amount
        else:
            extra["spenderAddress"] = self.permit2_spender
            extra["permit2Address"] = self.permit2_address
        return entry

    def challenge(self, quote: Quote) -> dict[str, Any]:
        accepts = [
            self._accept(quote, spec, method)
            for spec in self._offered()
            for method in self._methods(spec)
        ]
        return {
            "x402Version": 2,
            "error": "payment required",
            "resource": {
                "url": self.resource,
                "description": "bnbagent-eval evaluation",
                "mimeType": "application/json",
            },
            "accepts": accepts,
            "quote": quote.to_dict(),
        }

    # ------------------------------------------------------------------ verification

    def _decode(self, header: str) -> dict[str, Any]:
        if not isinstance(header, str) or not header or len(header) > MAX_HEADER_BYTES:
            raise _fail("malformed", "X-PAYMENT header missing or too large")
        text = header.strip()
        raw: bytes | None = None
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(text + "=" * (-len(text) % 4))
                break
            except (binascii.Error, ValueError):
                continue
        if raw is None:
            raise _fail("malformed", "X-PAYMENT is not base64")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise _fail("malformed", "X-PAYMENT is not base64 JSON") from exc
        if not isinstance(envelope, dict):
            raise _fail("malformed", "X-PAYMENT must be a JSON object")
        return envelope

    async def _check_quote(self, envelope: Mapping[str, Any], quote: Quote, now: int) -> None:
        if quote.expires_at <= now:
            raise _fail("expired", "quote expired")
        if quote.chain_id != self.chain_id or quote.pay_to.lower() != self.pay_to.lower():
            raise _fail("quote_mismatch", "quote is not for this gate")
        stored = await asyncio.to_thread(self._store.get_quote, quote.quote_id)
        if stored is None or (
            stored.request_hash,
            stored.amount,
            stored.asset.lower(),
            stored.pay_to.lower(),
            stored.chain_id,
        ) != (
            quote.request_hash,
            quote.amount,
            quote.asset.lower(),
            quote.pay_to.lower(),
            quote.chain_id,
        ):
            raise _fail("quote_mismatch", "quote unknown or altered")
        accepted = envelope.get("accepted")
        ref = envelope.get("quoteId")
        if ref is None and isinstance(accepted, Mapping):
            extra = accepted.get("extra")
            if isinstance(extra, Mapping):
                ref = extra.get("quoteId")
        if ref is None or ref == quote.quote_id:
            return
        if not isinstance(ref, str):
            raise _fail("malformed", "quoteId must be a string")
        paid_for = await asyncio.to_thread(self._store.get_quote, ref)
        if paid_for is None or paid_for.request_hash != quote.request_hash:
            raise _fail("quote_mismatch", "payment is for a different request")
        if paid_for.expires_at <= now:
            raise _fail("expired", "quote expired")

    def _recover(self, typed: dict[str, Any], signature: bytes) -> str:
        account = require("eth_account", "evm").Account
        messages = require("eth_account.messages", "evm")
        try:
            signable = messages.encode_typed_data(full_message=typed)
            return str(account.recover_message(signable, signature=signature))
        except Exception as exc:  # eth_account raises assorted errors on bad input
            raise _fail("bad_signature", "signature does not recover") from exc

    def _signature(self, payload: Mapping[str, Any]) -> bytes:
        sig = _hex_bytes(payload.get("signature"), "signature")
        if len(sig) != 65:
            raise _fail("bad_signature", "only 65-byte EOA signatures are supported")
        return sig

    def _explicit_asset(self, envelope: Mapping[str, Any]) -> str | None:
        accepted = envelope.get("accepted")
        candidates = [envelope.get("asset")]
        if isinstance(accepted, Mapping):
            candidates.append(accepted.get("asset"))
        for value in candidates:
            if value is not None:
                return self._address(value, "asset")
        return None

    def _verify_eip3009(
        self, envelope: Mapping[str, Any], payload: Mapping[str, Any], quote: Quote, now: int
    ) -> dict[str, Any]:
        auth = _mapping(payload.get("authorization"), "payload.authorization")
        payer = self._address(auth.get("from"), "authorization.from")
        to = self._address(auth.get("to"), "authorization.to")
        value = _uint(auth.get("value"), "authorization.value")
        valid_after = _uint(auth.get("validAfter"), "authorization.validAfter")
        valid_before = _uint(auth.get("validBefore"), "authorization.validBefore")
        nonce = _hex_bytes(auth.get("nonce"), "authorization.nonce", length=32)
        signature = self._signature(payload)

        explicit = self._explicit_asset(envelope)
        offered = [s for s in self._offered() if "eip3009" in self._methods(s)]
        if explicit is not None:
            offered = [s for s in offered if s.address.lower() == explicit.lower()]
            if not offered:
                raise _fail("wrong_asset", f"asset {explicit} is not offered for eip3009")
        if not offered:
            raise _fail("wrong_asset", "no eip3009 asset is offered")
        if to.lower() != quote.pay_to.lower():
            raise _fail("wrong_recipient", "authorization.to is not the quoted payTo")
        if now <= valid_after:
            raise _fail("not_yet_valid", "authorization is not valid yet")
        if now >= valid_before:
            raise _fail("expired", "authorization expired")

        message = {
            "from": payer,
            "to": to,
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        }
        spec = None
        for candidate in offered:  # the envelope may omit the asset (SDK buyer_demo)
            domain = dict(candidate.eip712_domain or {})
            typed = {
                "types": {
                    "EIP712Domain": EIP712_DOMAIN_FIELDS,
                    "TransferWithAuthorization": TRANSFER_WITH_AUTHORIZATION_FIELDS,
                },
                "primaryType": "TransferWithAuthorization",
                "domain": {
                    "name": domain.get("name"),
                    "version": domain.get("version"),
                    "chainId": self.chain_id,
                    "verifyingContract": self._checksum(candidate.address),
                },
                "message": message,
            }
            if self._recover(typed, signature).lower() == payer.lower():
                spec = candidate
                break
        if spec is None:
            raise _fail("bad_signature", "signer is not authorization.from")
        if value < self._amount_for(quote, spec):
            raise _fail("insufficient_amount", "authorization.value is below the quote")
        return {
            "method": "eip3009",
            "asset": self._checksum(spec.address),
            "payer": payer,
            "value": value,
            "nonce": "0x" + nonce.hex(),
            "authorization": dict(auth),
            "spec": spec,
        }

    def _verify_permit2(
        self, envelope: Mapping[str, Any], payload: Mapping[str, Any], quote: Quote, now: int
    ) -> dict[str, Any]:
        p2 = payload.get("permit2Authorization")
        if p2 is None:  # legacy dialect: payload.permit + payload.from
            p2 = {**_mapping(payload.get("permit"), "payload.permit"), "from": payload.get("from")}
        p2 = _mapping(p2, "payload.permit2Authorization")
        permitted = _mapping(p2.get("permitted"), "permitted")
        witness = _mapping(p2.get("witness"), "witness")
        token = self._address(permitted.get("token"), "permitted.token")
        amount = _uint(permitted.get("amount"), "permitted.amount")
        payer = self._address(p2.get("from"), "from")
        spender = self._address(p2.get("spender"), "spender")
        nonce = _uint(p2.get("nonce"), "nonce")
        deadline = _uint(p2.get("deadline"), "deadline")
        to = self._address(witness.get("to"), "witness.to")
        valid_after = _uint(witness.get("validAfter"), "witness.validAfter")
        extra = _hex_bytes(witness.get("extra", "0x"), "witness.extra")
        signature = self._signature(payload)

        explicit = self._explicit_asset(envelope)
        spec = self._spec(token)
        if (
            spec is None
            or "permit2-exact" not in self._methods(spec)
            or (explicit is not None and explicit.lower() != token.lower())
        ):
            raise _fail("wrong_asset", f"asset {token} is not offered for permit2-exact")
        if to.lower() != quote.pay_to.lower():
            raise _fail("wrong_recipient", "witness.to is not the quoted payTo")
        if self.permit2_spender is None or spender.lower() != self.permit2_spender.lower():
            raise _fail("wrong_recipient", "spender is not the configured Permit2 proxy")
        if now < valid_after:
            raise _fail("not_yet_valid", "permit is not valid yet")
        if now > deadline:
            raise _fail("expired", "permit deadline passed")

        typed = {
            "types": {"EIP712Domain": PERMIT2_DOMAIN_FIELDS, **PERMIT2_WITNESS_TYPES},
            "primaryType": "PermitWitnessTransferFrom",
            "domain": {
                "name": "Permit2",
                "chainId": self.chain_id,
                "verifyingContract": self.permit2_address,
            },
            "message": {
                "permitted": {"token": token, "amount": amount},
                "spender": spender,
                "nonce": nonce,
                "deadline": deadline,
                "witness": {"to": to, "validAfter": valid_after, "extra": extra},
            },
        }
        if self._recover(typed, signature).lower() != payer.lower():
            raise _fail("bad_signature", "signer is not permit2Authorization.from")
        if amount < self._amount_for(quote, spec):
            raise _fail("insufficient_amount", "permitted.amount is below the quote")
        key = f"0x{nonce:064x}"
        return {
            "method": "permit2-exact",
            "asset": self._checksum(spec.address),
            "payer": payer,
            "value": amount,
            "nonce": key,
            # Neutral nonce key (``FakeSettlement`` and adapters read ``authorization``).
            "authorization": {"from": payer, "nonce": key},
            "permit2Authorization": dict(p2),
            "spec": spec,
        }

    async def identify(self, header: str) -> str | None:
        """The payer whose signature the header carries (verified, nothing reserved or settled).

        Used to scope ``Idempotency-Key`` to a payer; ``None`` when the header does not verify.
        """
        try:
            envelope = self._decode(header)
            payload = _mapping(envelope.get("payload"), "payload")
            probe = Quote(
                quote_id="",
                request_hash="",
                amount=0,
                asset=self.asset or PERMIT2_ADDRESS,
                chain_id=self.chain_id,
                pay_to=self.pay_to,
                expires_at=0,
            )
            now = self._now()
            if "authorization" in payload:
                checked = self._verify_eip3009(envelope, payload, probe, now)
            elif "permit2Authorization" in payload or "permit" in payload:
                checked = self._verify_permit2(envelope, payload, probe, now)
            else:
                return None
        except (PaymentError, ValueError, TypeError):
            return None
        return str(checked["payer"]).lower()

    async def verify_and_settle(self, header: str, quote: Quote) -> PaymentReceipt:
        envelope = self._decode(header)
        version = envelope.get("x402Version", 2)
        if version not in (1, 2) or isinstance(version, bool):
            raise _fail("malformed", f"unsupported x402Version {version!r}")
        if envelope.get("scheme") != "exact":
            raise _fail("wrong_scheme", f"scheme {envelope.get('scheme')!r} is not 'exact'")
        if envelope.get("network") not in self._networks:
            raise _fail("wrong_network", f"network {envelope.get('network')!r} is not accepted")
        payload = _mapping(envelope.get("payload"), "payload")
        now = self._now()
        await self._check_quote(envelope, quote, now)

        if "authorization" in payload:
            checked = self._verify_eip3009(envelope, payload, quote, now)
        elif "permit2Authorization" in payload or "permit" in payload:
            checked = self._verify_permit2(envelope, payload, quote, now)
        else:
            raise _fail("malformed", "payload has neither authorization nor permit2Authorization")

        asset_key, payer_key, nonce_key = (
            checked["asset"].lower(),
            checked["payer"].lower(),
            checked["nonce"].lower(),
        )
        reservation = await asyncio.to_thread(
            self._store.reserve, asset_key, payer_key, nonce_key, quote.quote_id, now
        )
        if reservation is None:
            raise _fail("nonce_reused", "payment nonce already used")

        try:
            used = await self.settlement.authorization_state(
                checked["asset"], checked["payer"], checked["nonce"]
            )
        except Exception as exc:
            await asyncio.to_thread(self._store.release, reservation)
            raise _fail("settlement_failed", "could not read authorization state", 502) from exc
        if used:
            await asyncio.to_thread(self._store.mark, reservation, "used_onchain")
            raise _fail("nonce_reused", "authorization nonce already used on-chain")

        spec: AssetSpec = checked.pop("spec")
        requirement = self._accept(quote, spec, checked["method"])
        payment = {
            **checked,
            "scheme": "exact",
            "network": self.network,
            "chain_id": self.chain_id,
            "pay_to": quote.pay_to,
            "amount": self._amount_for(quote, spec),
            "signature": payload["signature"],
            "quote_id": quote.quote_id,
            "paymentPayload": envelope,
            "paymentRequirements": requirement,
        }
        try:
            tx_hash = await self.settlement.settle(payment)
        except Exception as exc:
            try:
                used = await self.settlement.authorization_state(
                    checked["asset"], checked["payer"], checked["nonce"]
                )
            except Exception:
                used = True  # unknown: keep the nonce reserved (fail closed)
            if used:
                await asyncio.to_thread(self._store.mark, reservation, "settle_unknown")
            else:
                await asyncio.to_thread(self._store.release, reservation)
            logger.warning("x402 settlement failed: %s", type(exc).__name__)
            raise _fail("settlement_failed", "payment settlement failed", 502) from exc
        await asyncio.to_thread(self._store.mark, reservation, "settled", tx_hash)
        return PaymentReceipt(
            payer=checked["payer"],
            amount=checked["value"],
            nonce=checked["nonce"],
            quote_id=quote.quote_id,
            tx_hash=tx_hash,
            extras={
                "asset": checked["asset"],
                "method": checked["method"],
                "network": self.network,
                "chain_id": self.chain_id,
            },
        )


__all__ = [
    "PERMIT2_ADDRESS",
    "X402Gate",
    "default_price_fn",
]
