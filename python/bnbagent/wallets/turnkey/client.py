"""Minimal Turnkey activity client — the two signing endpoints only.

Turnkey has no official Python SDK (only a stamper utility), so this module
implements the thin slice the wallet provider needs, mirroring the wire
behavior of ``@turnkey/http``:

- ``POST /public/v1/submit/sign_raw_payload``
  (``ACTIVITY_TYPE_SIGN_RAW_PAYLOAD_V2``)
- ``POST /public/v1/submit/sign_transaction``
  (``ACTIVITY_TYPE_SIGN_TRANSACTION_V2``)
- ``POST /public/v1/query/get_activity`` (short poll for the rare
  still-pending activity; sign activities normally execute synchronously)

Every request body is stamped (see :mod:`.stamper`) over the exact bytes
sent. Free-tier reality baked into callers: every *successful* signature is
billed (25/month at 1 request/second), so callers gate everything they can
BEFORE invoking this client.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from .stamper import ApiKeyStamper

TURNKEY_API_BASE_URL_DEFAULT = "https://api.turnkey.com"

_ACTIVITY_TERMINAL_OK = "ACTIVITY_STATUS_COMPLETED"
_ACTIVITY_IN_FLIGHT = ("ACTIVITY_STATUS_CREATED", "ACTIVITY_STATUS_PENDING")
_POLL_ATTEMPTS = 10
_POLL_INTERVAL_S = 0.5


class TurnkeyApiError(RuntimeError):
    """A Turnkey API request failed (transport, HTTP or activity level)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        activity_status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.activity_status = activity_status


class TurnkeyClient:
    """Stamped HTTP client for the two Turnkey signing activities."""

    def __init__(
        self,
        *,
        api_base_url: str,
        api_public_key: str,
        api_private_key: str,
        organization_id: str,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = api_base_url.rstrip("/")
        self._organization_id = organization_id
        self._stamper = ApiKeyStamper(
            api_public_key=api_public_key, api_private_key=api_private_key
        )
        self._session = session or requests.Session()
        self._timeout = timeout

    # ── Activities ────────────────────────────────────────────────────

    def sign_raw_payload(
        self,
        *,
        sign_with: str,
        payload: str,
        encoding: str,
        hash_function: str,
    ) -> dict[str, str]:
        """Run ``SIGN_RAW_PAYLOAD_V2``; returns ``{"r", "s", "v"}``.

        ``r``/``s`` are 32-byte hex strings and ``v`` is the recovery id as
        hex (``"00"``/``"01"``) — all without a ``0x`` prefix, exactly as
        the API returns them. Callers normalize.
        """
        activity = self._submit(
            "/public/v1/submit/sign_raw_payload",
            {
                "type": "ACTIVITY_TYPE_SIGN_RAW_PAYLOAD_V2",
                "organizationId": self._organization_id,
                "parameters": {
                    "signWith": sign_with,
                    "payload": payload,
                    "encoding": encoding,
                    "hashFunction": hash_function,
                },
                "timestampMs": _timestamp_ms(),
            },
        )
        result = (activity.get("result") or {}).get("signRawPayloadResult")
        if not result:
            raise TurnkeyApiError(
                "Turnkey activity completed without a signRawPayloadResult",
                activity_status=activity.get("status"),
            )
        return {"r": result["r"], "s": result["s"], "v": result["v"]}

    def sign_transaction(self, *, sign_with: str, unsigned_transaction: str) -> str:
        """Run ``SIGN_TRANSACTION_V2``; returns the signed RLP hex (no ``0x``).

        ``unsigned_transaction`` is the serialized unsigned transaction hex
        without a ``0x`` prefix (legacy or EIP-1559 — the enclave parses
        either).
        """
        activity = self._submit(
            "/public/v1/submit/sign_transaction",
            {
                "type": "ACTIVITY_TYPE_SIGN_TRANSACTION_V2",
                "organizationId": self._organization_id,
                "parameters": {
                    "signWith": sign_with,
                    "type": "TRANSACTION_TYPE_ETHEREUM",
                    "unsignedTransaction": unsigned_transaction,
                },
                "timestampMs": _timestamp_ms(),
            },
        )
        result = (activity.get("result") or {}).get("signTransactionResult") or {}
        signed = result.get("signedTransaction")
        if not signed:
            raise TurnkeyApiError(
                "Turnkey activity completed without a signedTransaction",
                activity_status=activity.get("status"),
            )
        return str(signed)

    # ── Plumbing ──────────────────────────────────────────────────────

    def _submit(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        activity = self._post(path, body)
        for _ in range(_POLL_ATTEMPTS):
            status = activity.get("status")
            if status == _ACTIVITY_TERMINAL_OK:
                return activity
            if status not in _ACTIVITY_IN_FLIGHT:
                raise TurnkeyApiError(
                    f"Turnkey activity ended in {status}: "
                    f"{activity.get('failure') or activity.get('type', '')}".strip(),
                    activity_status=status,
                )
            time.sleep(_POLL_INTERVAL_S)
            activity = self._post(
                "/public/v1/query/get_activity",
                {
                    "organizationId": self._organization_id,
                    "activityId": activity.get("id", ""),
                },
            )
        raise TurnkeyApiError(
            "Turnkey activity still pending after "
            f"{_POLL_ATTEMPTS * _POLL_INTERVAL_S:.0f}s of polling",
            activity_status=activity.get("status"),
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # The stamp signs the exact bytes on the wire, so serialize once and
        # send that same string.
        payload = json.dumps(body, separators=(",", ":"))
        header_name, header_value = self._stamper.stamp(payload)
        response = self._session.post(
            f"{self._base_url}{path}",
            data=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                header_name: header_value,
            },
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            raise TurnkeyApiError(
                f"Turnkey API {path} failed with HTTP {response.status_code}: "
                f"{_error_message(response)}",
                status_code=response.status_code,
            )
        try:
            parsed = response.json()
        except ValueError as exc:
            raise TurnkeyApiError(f"Turnkey API {path} returned non-JSON body") from exc
        activity = parsed.get("activity")
        if not isinstance(activity, dict):
            raise TurnkeyApiError(f"Turnkey API {path} response has no activity envelope")
        return activity


def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        return str(body.get("message") or body)[:500]
    return str(body)[:500]


def _timestamp_ms() -> str:
    return str(int(time.time() * 1000))
