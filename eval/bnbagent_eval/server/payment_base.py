"""The payment-gate contract between the eval service and x402.

The service asks the gate for a quote bound to the exact request, returns a 402 challenge when
no payment is attached, and verifies + settles the payment before evaluating. Gates must be
safe under concurrency (a nonce can be used once) and across restarts (persistent nonce store).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Quote:
    quote_id: str
    request_hash: str  # keccak of the canonical EvalRequest the quote is for
    amount: int  # atomic units of the asset
    asset: str  # token address
    chain_id: int
    pay_to: str
    expires_at: int  # unix seconds
    judge_calls: int = 0  # the estimate the price was derived from
    methods: tuple[str, ...] = ("eip3009",)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "request_hash": self.request_hash,
            "amount": str(self.amount),
            "asset": self.asset,
            "chain_id": self.chain_id,
            "pay_to": self.pay_to,
            "expires_at": self.expires_at,
            "judge_calls": self.judge_calls,
            "methods": list(self.methods),
        }


@dataclass(frozen=True)
class PaymentReceipt:
    payer: str
    amount: int
    nonce: str
    quote_id: str
    tx_hash: str | None = None  # settlement transaction
    extras: Mapping[str, Any] = field(default_factory=dict)

    def header(self) -> str:
        """Value for the ``X-PAYMENT-RESPONSE`` header (base64 JSON, as x402)."""
        import base64
        import json

        body = {
            "success": True,
            "transaction": self.tx_hash,
            "payer": self.payer,
            "quote_id": self.quote_id,
        }
        return base64.b64encode(json.dumps(body).encode()).decode()


class PaymentError(Exception):
    """A payment is missing, invalid, already used, or could not be settled."""

    def __init__(self, message: str, *, code: str = "payment_invalid", status: int = 402):
        self.code = code
        self.status = status
        super().__init__(message)


@runtime_checkable
class PaymentGate(Protocol):
    async def quote(self, request: Mapping[str, Any], *, judge_calls: int) -> Quote | None:
        """A quote for ``request`` (the JSON body), or ``None`` when no payment is required."""
        ...

    def challenge(self, quote: Quote) -> dict[str, Any]:
        """The 402 response body (``{"x402Version": 2, "accepts": [...], ...}``)."""
        ...

    async def verify_and_settle(self, header: str, quote: Quote) -> PaymentReceipt:
        """Verify the ``X-PAYMENT`` header against ``quote``, reserve its nonce, settle."""
        ...


class NoPaymentGate:
    """Free service (development / private deployments)."""

    async def quote(self, request: Mapping[str, Any], *, judge_calls: int) -> Quote | None:
        return None

    def challenge(self, quote: Quote) -> dict[str, Any]:
        raise PaymentError("no payment required")

    async def verify_and_settle(self, header: str, quote: Quote) -> PaymentReceipt:
        raise PaymentError("no payment required")
