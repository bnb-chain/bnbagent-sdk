"""Test doubles for the eval service: a scripted PaymentGate, a fake DNS, request builders."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

from bnbagent_eval.hashing import hash_obj
from bnbagent_eval.server.payment_base import PaymentError, PaymentReceipt, Quote

PAYER = "0x" + "11" * 20
PAY_TO = "0x" + "bb" * 20
ASSET = "0x" + "aa" * 20
JUDGE_REPLY = {"reasoning": "reads well", "score": "Good"}

DNS = {
    "agent.example.com": ["93.184.215.14"],
    "data.example.com": ["93.184.215.15"],
    "evil.example.com": ["10.0.0.5"],
    "rebind.example.com": ["93.184.215.14", "127.0.0.1"],
}


async def fake_resolver(host: str, port: int) -> list[str]:
    if host not in DNS:
        raise OSError(f"unknown host {host}")
    return DNS[host]


class FakeGate:
    """Price = base + per_call × judge calls; ``X-PAYMENT`` is JSON {nonce, payer, quote_id}."""

    def __init__(self, base: int = 1000, per_call: int = 10):
        self.base, self.per_call = base, per_call
        self.settled: list[PaymentReceipt] = []
        self.used: set[str] = set()

    async def quote(self, request: Mapping[str, Any], *, judge_calls: int) -> Quote | None:
        request_hash = hash_obj(dict(request))
        return Quote(
            quote_id="q-" + request_hash[2:14],
            request_hash=request_hash,
            amount=self.base + self.per_call * judge_calls,
            asset=ASSET,
            chain_id=97,
            pay_to=PAY_TO,
            expires_at=int(time.time()) + 600,
            judge_calls=judge_calls,
        )

    def challenge(self, quote: Quote) -> dict[str, Any]:
        return {
            "x402Version": 2,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": f"eip155:{quote.chain_id}",
                    "maxAmountRequired": str(quote.amount),
                    "payTo": quote.pay_to,
                    "asset": quote.asset,
                    "extra": {"quote_id": quote.quote_id},
                }
            ],
        }

    async def identify(self, header: str) -> str | None:
        """The (fake-)signed payer, as ``X402Gate.identify`` (scopes Idempotency-Key)."""
        try:
            payer = json.loads(header).get("payer", PAYER)
        except (ValueError, AttributeError):
            return None
        return str(payer).lower()

    async def verify_and_settle(self, header: str, quote: Quote) -> PaymentReceipt:
        try:
            payment = json.loads(header)
        except ValueError as exc:
            raise PaymentError("malformed X-PAYMENT") from exc
        if payment.get("quote_id") != quote.quote_id:
            raise PaymentError("payment is for another quote", code="quote_mismatch")
        if payment["nonce"] in self.used:
            raise PaymentError("nonce already used", code="nonce_replay", status=409)
        self.used.add(payment["nonce"])
        receipt = PaymentReceipt(
            payer=payment.get("payer", PAYER),
            amount=quote.amount,
            nonce=payment["nonce"],
            quote_id=quote.quote_id,
            tx_hash="0x" + "cd" * 32,
        )
        self.settled.append(receipt)
        return receipt


def payment_for(quote: Mapping[str, Any], nonce: str | None = None) -> str:
    return json.dumps(
        {"nonce": nonce or uuid.uuid4().hex, "payer": PAYER, "quote_id": quote["quote_id"]}
    )


def request_body(
    *,
    stage: str = "dev",
    metrics: list[dict[str, Any]] | None = None,
    cases: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "suite": {
            "name": "svc",
            "version": "1",
            "metrics": metrics or [{"id": "text.exact_match"}],
        },
        "cases": cases if cases is not None else [{"id": "1", "output": "a", "expected": "a"}],
        **extra,
    }


JUDGE_METRICS = [{"id": "text.exact_match"}, {"id": "quality.fluency"}]
