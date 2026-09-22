"""SRC-1661: reject jobs before starting provider work, including retries."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from bnbagent.erc8183.job_ops import funded_job_watcher
from bnbagent.erc8183.types import JobStatus
from tests.test_erc8183_job_ops import OTHER, _inject_client, _job, _make_ops


@pytest.mark.parametrize(
    "job_kwargs,unsigned,error",
    [
        ({"budget": 1}, False, "quote_invalid"),
        ({"budget": 1}, True, "budget_too_low"),
        ({"provider": OTHER}, True, "not_assigned"),
        ({"expired_at": 1}, True, "job_expired"),
        ({"status": JobStatus.SUBMITTED}, True, "wrong_status"),
    ],
)
async def test_invalid_jobs_never_start_work(job_kwargs, unsigned, error):
    ops = _make_ops(service_price=1000, allow_unsigned_jobs=unsigned)
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.get_job.return_value = _job(**job_kwargs)
    assert (await ops.verify_job(1))["error_code"] == error
    job = await ops.get_job(1)
    ops.get_pending_jobs = AsyncMock(return_value={"success": True, "jobs": [job]})
    work = AsyncMock()
    stop = asyncio.Event()
    stop.set()

    await funded_job_watcher(ops, work, interval=0.01, stop=stop)

    work.assert_not_awaited()


async def test_callback_receives_the_verified_job_snapshot():
    ops = _make_ops()
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.get_job.return_value = _job(description="current terms")
    ops.get_pending_jobs = AsyncMock(
        return_value={"success": True, "jobs": [{"jobId": 1, "description": "stale terms"}]}
    )
    work = AsyncMock()
    stop = asyncio.Event()
    stop.set()

    await funded_job_watcher(ops, work, interval=0.01, stop=stop)

    work.assert_awaited_once()
    assert work.call_args.args[0]["description"] == "current terms"


@pytest.mark.parametrize("first", [{"valid": False, "retryable": True}, OSError("RPC down")])
async def test_transient_verification_failure_retries_without_early_work(first):
    ops = _make_ops()
    fresh = {
        "success": True,
        "jobId": 1,
        "status": JobStatus.FUNDED,
        "expiredAt": int(time.time()) + 3600,
    }
    polls = 0
    stop = asyncio.Event()

    async def pending():
        nonlocal polls
        polls += 1
        if polls == 2:
            stop.set()
        return {"success": True, "jobs": [fresh] if polls == 1 else []}

    ops.get_pending_jobs = pending
    ops.get_job = AsyncMock(return_value=fresh)
    ops.verify_job = AsyncMock(side_effect=[first, {"valid": True, "job": fresh}])
    work = AsyncMock()

    await asyncio.wait_for(funded_job_watcher(ops, work, interval=0.001, stop=stop), 1)

    assert ops.verify_job.await_count == 2
    work.assert_awaited_once_with(fresh)


async def test_callback_retry_is_rejected_if_verification_no_longer_passes():
    ops = _make_ops()
    fresh = {
        "success": True,
        "jobId": 1,
        "status": JobStatus.FUNDED,
        "expiredAt": int(time.time()) + 3600,
    }
    polls = 0
    stop = asyncio.Event()

    async def pending():
        nonlocal polls
        polls += 1
        if polls == 3:
            stop.set()
        return {"success": True, "jobs": [fresh]}

    ops.get_pending_jobs = pending
    ops.get_job = AsyncMock(return_value=fresh)
    ops.verify_job = AsyncMock(
        side_effect=[
            {"valid": True, "job": fresh},
            {"valid": False, "error_code": "budget_too_low", "retryable": False},
        ]
    )
    work = AsyncMock(return_value=False)

    await asyncio.wait_for(funded_job_watcher(ops, work, interval=0.001, stop=stop), 1)

    assert ops.verify_job.await_count == 2
    work.assert_awaited_once()
