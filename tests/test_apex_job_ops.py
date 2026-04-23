"""Tests for ``APEXJobOps`` — async provider-side lifecycle ops (APEX v1).

Focus areas:
- ``verify_job`` — status / provider / expiry / budget gating.
- ``auto_settle_once`` — pulls verdict, calls ``router.settle`` only on
  non-PENDING verdicts, prunes foreign / terminal-state jobs.
"""

import time
from unittest.mock import MagicMock

import pytest

from bnbagent.apex.server.job_ops import APEXJobOps
from bnbagent.apex.types import Job, JobStatus, Verdict

ME = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
CLIENT = "0x" + "cc" * 20


def _make_wallet(address=ME):
    wp = MagicMock()
    wp.address = address
    return wp


def _make_ops(storage=None, service_price=0, wallet=None):
    ops = APEXJobOps(
        wallet or _make_wallet(),
        storage_provider=storage,
        service_price=service_price,
    )
    return ops


def _inject_client(ops):
    client = MagicMock()
    client.address = ME
    ops._client = client
    return client


def _job(status=JobStatus.FUNDED, provider=ME, expired_at=None, budget=1000, description=""):
    return Job(
        id=1,
        client=CLIENT,
        provider=provider,
        evaluator="0x" + "ee" * 20,
        description=description,
        budget=budget,
        expired_at=expired_at if expired_at is not None else int(time.time()) + 3600,
        status=status,
        hook="0x" + "ee" * 20,
    )


class TestAgentAddress:
    def test_uses_wallet_address(self):
        ops = _make_ops()
        assert ops.agent_address == ME

    def test_requires_wallet_provider(self):
        with pytest.raises(ValueError, match="wallet_provider is required"):
            APEXJobOps(None)  # type: ignore[arg-type]


class TestVerifyJob:
    @pytest.mark.asyncio
    async def test_valid_funded_job(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job()
        result = await ops.verify_job(1)
        assert result["valid"] is True
        assert result["job"]["jobId"] == 1

    @pytest.mark.asyncio
    async def test_rejects_non_funded(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.OPEN)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert "FUNDED" in result["error"]
        assert result["error_code"] == 409

    @pytest.mark.asyncio
    async def test_rejects_foreign_provider(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(provider=OTHER)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 403

    @pytest.mark.asyncio
    async def test_rejects_expired(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(expired_at=int(time.time()) - 100)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 408

    @pytest.mark.asyncio
    async def test_rejects_under_priced(self):
        ops = _make_ops(service_price=5000)
        client = _inject_client(ops)
        client.get_job.return_value = _job(budget=1000)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 402
        assert result["service_price"] == "5000"

    @pytest.mark.asyncio
    async def test_accepts_equal_or_higher_budget(self):
        ops = _make_ops(service_price=1000)
        client = _inject_client(ops)
        client.get_job.return_value = _job(budget=1000)
        result = await ops.verify_job(1)
        assert result["valid"] is True


class TestAutoSettleOnce:
    @pytest.mark.asyncio
    async def test_noop_when_no_tracked_jobs(self):
        ops = _make_ops()
        _inject_client(ops)
        result = await ops.auto_settle_once()
        assert result == {"success": True, "settled": [], "skipped": []}

    @pytest.mark.asyncio
    async def test_settles_on_approve_verdict(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.SUBMITTED)
        client.get_verdict.return_value = (Verdict.APPROVE, b"\x00" * 32)
        client.settle.return_value = {"transactionHash": "0xabc"}
        result = await ops.auto_settle_once()
        assert result["settled"] == [1]
        assert result["skipped"] == []
        client.settle.assert_called_once_with(1)
        assert 1 not in ops._submitted_ids

    @pytest.mark.asyncio
    async def test_settles_on_reject_verdict(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.SUBMITTED)
        client.get_verdict.return_value = (Verdict.REJECT, b"\x00" * 32)
        client.settle.return_value = {"transactionHash": "0xabc"}
        result = await ops.auto_settle_once()
        assert result["settled"] == [1]

    @pytest.mark.asyncio
    async def test_skips_on_pending_verdict(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.SUBMITTED)
        client.get_verdict.return_value = (Verdict.PENDING, b"\x00" * 32)
        result = await ops.auto_settle_once()
        assert result["skipped"] == [1]
        assert result["settled"] == []
        client.settle.assert_not_called()
        assert 1 in ops._submitted_ids

    @pytest.mark.asyncio
    async def test_drops_foreign_jobs(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.SUBMITTED, provider=OTHER)
        result = await ops.auto_settle_once()
        assert 1 not in ops._submitted_ids
        assert result["settled"] == []
        client.get_verdict.assert_not_called()

    @pytest.mark.asyncio
    async def test_drops_terminal_state_jobs(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.COMPLETED)
        result = await ops.auto_settle_once()
        assert 1 not in ops._submitted_ids
        assert result["settled"] == []
        client.get_verdict.assert_not_called()

    @pytest.mark.asyncio
    async def test_settle_tx_failure_retained_for_retry(self):
        ops = _make_ops()
        client = _inject_client(ops)
        ops.track_for_settle(1)
        client.get_job.return_value = _job(status=JobStatus.SUBMITTED)
        client.get_verdict.return_value = (Verdict.APPROVE, b"\x00" * 32)
        client.settle.side_effect = RuntimeError("already settled")
        result = await ops.auto_settle_once()
        assert result["settled"] == []
        assert result["errors"]
        assert result["errors"][0][0] == 1
        # A race is not a permanent state — next pass will re-check.
        assert 1 in ops._submitted_ids


class TestSubmitResult:
    @pytest.mark.asyncio
    async def test_submit_tracks_job_for_settle(self, tmp_path):
        from bnbagent.storage.local_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_ops(storage=storage)
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.submit.return_value = {"transactionHash": "0xaa"}
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "hello")
        assert result["success"] is True
        assert 1 in ops._submitted_ids
        assert "contentHash" in result
        assert "deliverableUrl" in result

    @pytest.mark.asyncio
    async def test_submit_blocked_on_failed_verify(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.OPEN)
        result = await ops.submit_result(1, "x")
        assert result["success"] is False
        client.submit.assert_not_called()


class TestGetPendingJobs:
    @pytest.mark.asyncio
    async def test_startup_scan_zero_counter(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.commerce.job_counter.return_value = 0
        result = await ops.get_pending_jobs()
        assert result == {"success": True, "jobs": []}
        assert ops._startup_scan_done

    @pytest.mark.asyncio
    async def test_startup_scan_filters_to_funded_owned(self):
        from dataclasses import replace

        ops = _make_ops()
        client = _inject_client(ops)
        client.commerce.job_counter.return_value = 3

        mine_funded = replace(_job(status=JobStatus.FUNDED, provider=ME), id=1)
        other_funded = replace(_job(status=JobStatus.FUNDED, provider=OTHER), id=2)
        mine_completed = replace(_job(status=JobStatus.COMPLETED, provider=ME), id=3)
        client.commerce.get_jobs_batch.return_value = [
            mine_funded, other_funded, mine_completed
        ]

        result = await ops.get_pending_jobs()
        assert result["success"]
        ids = [j["jobId"] for j in result["jobs"]]
        assert ids == [1]
