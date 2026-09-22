"""The public response route must enforce both limits before SDK/RPC work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import erc8183_server as server
from fastapi.testclient import TestClient

from bnbagent.utils import RateLimitExceeded, SlidingWindowLimiter


def make_app(monkeypatch, **limiters):
    ops = SimpleNamespace(
        get_response=AsyncMock(
            return_value={
                "success": False,
                "error_code": "not_found",
            }
        )
    )
    monkeypatch.setattr(server, "create_erc8183_state", lambda _: SimpleNamespace(job_ops=ops))
    return server.create_erc8183_app(**limiters), ops


def test_per_client_limit_rejects_before_lookup(monkeypatch):
    app, ops = make_app(monkeypatch, response_limiter=SlidingWindowLimiter(1, 60))
    with TestClient(app) as client:
        assert client.get("/erc8183/job/1/response").status_code == 404
        assert client.get("/erc8183/job/2/response").status_code == 429
    ops.get_response.assert_awaited_once_with(1)


def test_async_shared_global_limiter_is_enforced(monkeypatch):
    class Deny:
        async def check(self, key):
            assert key == "global"
            raise RateLimitExceeded()

    app, ops = make_app(monkeypatch, global_response_limiter=Deny())
    with TestClient(app) as client:
        assert client.get("/erc8183/job/1/response").status_code == 429
    ops.get_response.assert_not_awaited()


def test_forwarded_header_does_not_override_client_identity(monkeypatch):
    app, ops = make_app(monkeypatch, response_limiter=SlidingWindowLimiter(1, 60))
    with TestClient(app) as client:
        client.get("/erc8183/job/1/response", headers={"X-Forwarded-For": "1.1.1.1"})
        result = client.get("/erc8183/job/2/response", headers={"X-Forwarded-For": "8.8.8.8"})
        assert result.status_code == 429
    ops.get_response.assert_awaited_once()


def test_default_response_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("ERC8183_RESPONSE_RATE_LIMIT", "1")
    app, ops = make_app(monkeypatch)
    with TestClient(app) as client:
        client.get("/erc8183/job/1/response")
        assert client.get("/erc8183/job/2/response").status_code == 429
    ops.get_response.assert_awaited_once()
