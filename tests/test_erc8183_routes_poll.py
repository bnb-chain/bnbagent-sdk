"""Funded poll loop retry behavior — transient failures retry, permanent don't.

``get_pending_jobs`` reports each FUNDED job only once (cursor-based), so a
job whose execution fails transiently (5xx / exception) must be re-attempted
from the loop's retry queue; permanent failures (4xx) must not retry.
"""

import time
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from bnbagent.erc8183.server.routes import create_erc8183_app


def _fake_state(job_ops):
    state = MagicMock()
    state.job_ops = job_ops
    state.payment_token = ""
    state.payment_token_decimals = 18
    return state


def _job_ops(verify_results, pending_jobs):
    """JobOps stub: one funded poll returning ``pending_jobs``, then empty."""
    polls = [{"success": True, "jobs": list(pending_jobs)}]

    async def get_pending_jobs():
        return polls.pop(0) if polls else {"success": True, "jobs": []}

    ops = MagicMock()
    ops.agent_address = "0x" + "aa" * 20
    ops.get_pending_jobs = get_pending_jobs
    ops.verify_job = AsyncMock(side_effect=verify_results)
    ops.submit_result = AsyncMock(
        return_value={"success": True, "txHash": "0x" + "de" * 32}
    )
    return ops


def _run_app(ops, on_job, seconds=1.0, until=None):
    app = create_erc8183_app(
        config=MagicMock(),
        on_job=on_job,
        funded_poll_interval=0.02,
    )
    with TestClient(app):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if until is not None and until():
                break
            time.sleep(0.01)


def _valid(job_id=1):
    return {
        "valid": True,
        "job": {"jobId": job_id, "description": "", "budget": 1},
        "warnings": None,
    }


class TestFundedPollRetry:
    def test_transient_verify_failure_retries_then_succeeds(self, monkeypatch):
        transient = {"valid": False, "error": "Temporary chain/RPC error", "error_code": 503}
        ops = _job_ops(
            verify_results=[transient, _valid()],
            pending_jobs=[{"jobId": 1}],
        )
        monkeypatch.setattr(
            "bnbagent.erc8183.server.routes.create_erc8183_state",
            lambda config: _fake_state(ops),
        )
        _run_app(
            ops,
            on_job=lambda job: "answer",
            until=lambda: ops.submit_result.await_count >= 1,
        )
        assert ops.verify_job.await_count == 2
        assert ops.submit_result.await_count == 1

    def test_permanent_failure_does_not_retry(self, monkeypatch):
        permanent = {"valid": False, "error": "This agent is not the provider", "error_code": 403}
        ops = _job_ops(
            verify_results=lambda job_id: permanent,
            pending_jobs=[{"jobId": 1}],
        )
        monkeypatch.setattr(
            "bnbagent.erc8183.server.routes.create_erc8183_state",
            lambda config: _fake_state(ops),
        )
        _run_app(ops, on_job=lambda job: "answer", seconds=0.3)
        assert ops.verify_job.await_count == 1
        ops.submit_result.assert_not_called()

    def test_retries_are_capped(self, monkeypatch):
        transient = {"valid": False, "error": "Temporary chain/RPC error", "error_code": 503}
        ops = _job_ops(
            verify_results=lambda job_id: transient,
            pending_jobs=[{"jobId": 1}],
        )
        monkeypatch.setattr(
            "bnbagent.erc8183.server.routes.create_erc8183_state",
            lambda config: _fake_state(ops),
        )
        _run_app(ops, on_job=lambda job: "answer", seconds=0.8)
        # _MAX_JOB_ATTEMPTS = 5: initial attempt + 4 retries, then give up.
        assert ops.verify_job.await_count == 5
        ops.submit_result.assert_not_called()


class TestResponseRoute:
    """/response forwards get_response's error_code (503 vs 404), BUG-06."""

    def _http(self, get_response_result, monkeypatch):
        ops = MagicMock()
        ops.agent_address = "0x" + "aa" * 20
        ops.get_response = AsyncMock(return_value=get_response_result)
        monkeypatch.setattr(
            "bnbagent.erc8183.server.routes.create_erc8183_state",
            lambda config: _fake_state(ops),
        )
        return TestClient(create_erc8183_app(config=MagicMock()))

    def test_unresolvable_deliverable_forwards_503(self, monkeypatch):
        http = self._http(
            {"success": False, "error": "temporarily unresolvable", "error_code": 503},
            monkeypatch,
        )
        assert http.get("/erc8183/job/1/response").status_code == 503

    def test_genuine_not_found_is_404(self, monkeypatch):
        http = self._http(
            {"success": False, "error": "Response not found", "error_code": 404},
            monkeypatch,
        )
        assert http.get("/erc8183/job/1/response").status_code == 404

    def test_missing_error_code_defaults_to_404(self, monkeypatch):
        http = self._http(
            {"success": False, "error": "No storage configured"}, monkeypatch
        )
        assert http.get("/erc8183/job/1/response").status_code == 404
