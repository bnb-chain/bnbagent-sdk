"""The public response route must enforce both limits before SDK/RPC work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import erc8183_server as server
import pytest
from fastapi.testclient import TestClient

from bnbagent.erc8183.client import ERC8183Client
from bnbagent.erc8183.job_ops import ERC8183JobOps
from bnbagent.erc8183.policy import PolicyClient
from bnbagent.erc8183.types import Job, JobStatus
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


def test_rejected_client_does_not_consume_global_budget(monkeypatch):
    global_limiter = MagicMock()
    app, _ = make_app(
        monkeypatch,
        response_limiter=SlidingWindowLimiter(1, 60),
        global_response_limiter=global_limiter,
    )
    with TestClient(app) as client:
        client.get("/erc8183/job/1/response")
        for _ in range(10):
            assert client.get("/erc8183/job/2/response").status_code == 429
    global_limiter.check.assert_called_once_with("global")


def test_verification_shares_the_response_rate_budget(monkeypatch):
    app, ops = make_app(monkeypatch, response_limiter=SlidingWindowLimiter(1, 60))
    ops.verify_job = AsyncMock(return_value={"valid": False, "error_code": "quote_invalid"})
    with TestClient(app) as client:
        assert client.get("/erc8183/job/1/verify").status_code == 400
        assert client.get("/erc8183/job/1/verify").status_code == 429
        assert client.get("/erc8183/job/1/response").status_code == 429
    ops.verify_job.assert_awaited_once()
    ops.get_response.assert_not_awaited()


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


def make_resolver_app(monkeypatch, status=JobStatus.SUBMITTED, **limiters):
    """Exercise the HTTP route and real resolver, stubbing only chain/storage I/O."""
    address = "0x" + "11" * 20
    w3 = MagicMock()
    w3.eth.block_number = 100000
    sdk = object.__new__(ERC8183Client)
    sdk.commerce = MagicMock()
    sdk.commerce.w3 = w3
    sdk.commerce.get_job.return_value = Job(
        id=1,
        client=address,
        provider=address,
        evaluator=address,
        description="",
        budget=1,
        expired_at=9999999999,
        status=status,
        hook=address,
    )
    sdk.policy = PolicyClient(w3, address, abi=[])
    submission_logs = sdk.commerce.contract.events.JobSubmitted.return_value.get_logs
    policy_logs = sdk.policy.contract.events.JobInitialised.return_value.get_logs
    submission_logs.return_value = []
    policy_logs.return_value = []
    storage = MagicMock(spec=["download", "upload"])
    storage.download = AsyncMock()
    ops = ERC8183JobOps(provider_address=address, storage_provider=storage)
    ops._client = sdk
    monkeypatch.setattr(server, "create_erc8183_state", lambda _: SimpleNamespace(job_ops=ops))
    return (
        server.create_erc8183_app(**limiters),
        submission_logs,
        policy_logs,
        sdk.commerce.get_job,
    )


def test_http_repeated_miss_shares_bounded_real_log_resolution(monkeypatch):
    app, submission_logs, policy_logs, get_job = make_resolver_app(monkeypatch)
    with TestClient(app) as client:
        for _ in range(3):
            result = client.get("/erc8183/job/1000000001/response")
            assert result.status_code == 503
            assert result.json()["error_code"] == "chain_unavailable"
    assert submission_logs.call_count == 50
    assert policy_logs.call_count == 1
    assert get_job.call_count == 1
    assert (
        sum(
            call.kwargs["to_block"] - call.kwargs["from_block"] + 1
            for call in submission_logs.call_args_list
        )
        == 50000
    )


@pytest.mark.parametrize("status", [JobStatus.OPEN, JobStatus.FUNDED])
def test_http_unsubmitted_jobs_never_scan_logs(monkeypatch, status):
    app, submission_logs, policy_logs, get_job = make_resolver_app(monkeypatch, status)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/erc8183/job/1000000001/response").status_code == 404
    submission_logs.assert_not_called()
    policy_logs.assert_not_called()
    get_job.assert_called_once()


def test_http_distinct_ids_are_throttled_before_real_resolution(monkeypatch):
    app, submission_logs, policy_logs, get_job = make_resolver_app(
        monkeypatch, global_response_limiter=SlidingWindowLimiter(1, 60)
    )
    with TestClient(app) as client:
        assert client.get("/erc8183/job/1000000001/response").status_code == 503
        assert client.get("/erc8183/job/1000000002/response").status_code == 429
    assert submission_logs.call_count == 50
    assert policy_logs.call_count == 1
    get_job.assert_called_once()
