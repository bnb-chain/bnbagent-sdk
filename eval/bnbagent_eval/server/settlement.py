"""Settlement adapters for ``X402Gate`` (ports-level; no SDK imports).

``FacilitatorSettlement`` talks to an x402 facilitator: ``POST /verify`` then ``POST /settle``,
each with ``{x402Version, paymentPayload, paymentRequirements}``. Direct settlement
(``transferWithAuthorization`` / Permit2 proxy from a settler wallet) belongs to the bnbagent
adapter in ``integrations``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class FacilitatorError(Exception):
    """The facilitator rejected or failed to settle a payment."""

    def __init__(self, message: str, *, reason: str | None = None):
        self.reason = reason
        super().__init__(message)


class FacilitatorSettlement:
    """``SettlementPort`` over the x402 facilitator HTTP API."""

    def __init__(
        self,
        url: str,
        client: httpx.AsyncClient | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 30.0,
    ):
        self.url = url.rstrip("/")
        self._client = client
        self._headers = dict(headers or {})
        self._timeout = timeout_s

    async def authorization_state(self, asset: str, payer: str, nonce: str) -> bool:
        """The facilitator API has no nonce query; ``/verify`` rejects used nonces instead."""
        return False

    async def _post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if self._client is not None:
            resp = await self._client.post(self.url + path, json=body, headers=self._headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self.url + path, json=body, headers=self._headers)
        if resp.status_code >= 400:
            raise FacilitatorError(f"facilitator {path} returned HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            raise FacilitatorError(f"facilitator {path} returned a non-object")
        return data

    async def settle(self, payment: Mapping[str, Any]) -> str:
        envelope = payment.get("paymentPayload")
        requirements = payment.get("paymentRequirements")
        if not isinstance(envelope, Mapping) or not isinstance(requirements, Mapping):
            raise ValueError("payment needs paymentPayload and paymentRequirements")
        body = {
            "x402Version": envelope.get("x402Version", 2),
            "paymentPayload": dict(envelope),
            "paymentRequirements": dict(requirements),
        }
        verified = await self._post("/verify", body)
        if not verified.get("isValid"):
            reason = verified.get("invalidReason")
            raise FacilitatorError(f"facilitator rejected payment: {reason}", reason=reason)
        settled = await self._post("/settle", body)
        tx = settled.get("transaction")
        if not settled.get("success") or not isinstance(tx, str) or not tx:
            reason = settled.get("errorReason")
            raise FacilitatorError(f"facilitator settlement failed: {reason}", reason=reason)
        return tx


__all__ = ["FacilitatorError", "FacilitatorSettlement"]
