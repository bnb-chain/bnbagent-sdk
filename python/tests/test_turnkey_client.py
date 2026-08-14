"""Unit tests for the stamped Turnkey HTTP activity client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from bnbagent.wallets.turnkey.client import TurnkeyApiError, TurnkeyClient


def _activity(status: str, **extra) -> dict:
    return {"id": "activity-1", "status": status, **extra}


def _client() -> tuple[TurnkeyClient, MagicMock, MagicMock]:
    session = MagicMock(spec=requests.Session)
    stamper = MagicMock()
    with patch("bnbagent.wallets.turnkey.client.ApiKeyStamper", return_value=stamper):
        client = TurnkeyClient(
            api_base_url="https://api.turnkey.example/",
            api_public_key="public",
            api_private_key="private",
            organization_id="org-1",
            session=session,
            timeout=2.0,
        )
    return client, session, stamper


class TestSubmit:
    def test_returns_immediately_when_submission_is_completed(self):
        client, _, _ = _client()
        completed = _activity("ACTIVITY_STATUS_COMPLETED", result={"ok": True})
        client._post = MagicMock(return_value=completed)

        with patch("bnbagent.wallets.turnkey.client.time.sleep") as sleep:
            assert client._submit("/submit", {}) is completed

        client._post.assert_called_once_with("/submit", {})
        sleep.assert_not_called()

    def test_checks_completion_returned_by_last_poll(self):
        client, _, _ = _client()
        created = _activity("ACTIVITY_STATUS_CREATED")
        completed = _activity("ACTIVITY_STATUS_COMPLETED", result={"ok": True})
        client._post = MagicMock(side_effect=[created, *([created] * 9), completed])

        with patch("bnbagent.wallets.turnkey.client.time.sleep") as sleep:
            assert client._submit("/submit", {}) is completed

        assert client._post.call_count == 11
        assert sleep.call_count == 10

    def test_checks_failure_returned_by_last_poll(self):
        client, _, _ = _client()
        created = _activity("ACTIVITY_STATUS_CREATED")
        failed = _activity("ACTIVITY_STATUS_FAILED", failure="policy rejected")
        client._post = MagicMock(side_effect=[created, *([created] * 9), failed])

        with (
            patch("bnbagent.wallets.turnkey.client.time.sleep"),
            pytest.raises(TurnkeyApiError, match="ended in ACTIVITY_STATUS_FAILED") as excinfo,
        ):
            client._submit("/submit", {})

        assert excinfo.value.activity_status == "ACTIVITY_STATUS_FAILED"
        assert client._post.call_count == 11

    def test_raises_after_poll_timeout(self):
        client, _, _ = _client()
        created = _activity("ACTIVITY_STATUS_CREATED")
        client._post = MagicMock(return_value=created)

        with (
            patch("bnbagent.wallets.turnkey.client.time.sleep") as sleep,
            pytest.raises(TurnkeyApiError, match="still pending after 5s") as excinfo,
        ):
            client._submit("/submit", {})

        assert excinfo.value.activity_status == "ACTIVITY_STATUS_CREATED"
        assert client._post.call_count == 11
        assert sleep.call_count == 10


class TestPost:
    def test_stamp_matches_exact_wire_body(self):
        client, session, stamper = _client()
        body = {"organizationId": "org-1", "parameters": {"probe": True}}
        payload = json.dumps(body, separators=(",", ":"))
        stamper.stamp.return_value = ("X-Stamp", "stamp-value")
        response = session.post.return_value
        response.status_code = 200
        response.json.return_value = {"activity": _activity("ACTIVITY_STATUS_COMPLETED")}

        client._post("/public/v1/submit/probe", body)

        stamper.stamp.assert_called_once_with(payload)
        session.post.assert_called_once_with(
            "https://api.turnkey.example/public/v1/submit/probe",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Stamp": "stamp-value"},
            timeout=2.0,
        )

    @pytest.mark.parametrize(
        ("response_body", "expected"),
        [
            ({"message": "invalid stamp"}, "invalid stamp"),
            (["first error", "second error"], "first error"),
        ],
    )
    def test_maps_http_error_with_json_body(self, response_body, expected):
        client, session, stamper = _client()
        stamper.stamp.return_value = ("X-Stamp", "stamp-value")
        response = session.post.return_value
        response.status_code = 400
        response.json.return_value = response_body

        with pytest.raises(TurnkeyApiError, match=expected) as excinfo:
            client._post("/public/v1/submit/probe", {})

        assert excinfo.value.status_code == 400

    def test_maps_http_error_with_text_body(self):
        client, session, stamper = _client()
        stamper.stamp.return_value = ("X-Stamp", "stamp-value")
        response = session.post.return_value
        response.status_code = 500
        response.json.side_effect = ValueError
        response.text = "upstream unavailable"

        with pytest.raises(TurnkeyApiError, match="upstream unavailable") as excinfo:
            client._post("/public/v1/submit/probe", {})

        assert excinfo.value.status_code == 500

    def test_rejects_non_json_success_body(self):
        client, session, stamper = _client()
        stamper.stamp.return_value = ("X-Stamp", "stamp-value")
        response = session.post.return_value
        response.status_code = 200
        response.json.side_effect = ValueError

        with pytest.raises(TurnkeyApiError, match="returned non-JSON body"):
            client._post("/public/v1/submit/probe", {})

    def test_rejects_success_body_without_activity_envelope(self):
        client, session, stamper = _client()
        stamper.stamp.return_value = ("X-Stamp", "stamp-value")
        response = session.post.return_value
        response.status_code = 200
        response.json.return_value = {"notActivity": {}}

        with pytest.raises(TurnkeyApiError, match="has no activity envelope"):
            client._post("/public/v1/submit/probe", {})
