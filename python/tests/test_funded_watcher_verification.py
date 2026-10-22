"""SRC-1661: reject jobs before starting provider work, including retries."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from bnbagent.erc8183.job_ops import funded_job_watcher
from bnbagent.erc8183.types import JobStatus
from tests.test_erc8183_job_ops import (
    COMMERCE,
    OTHER,
    _inject_client,
    _job,
    _make_ops,
    _make_wallet,
    _signed_description,
)


@pytest.mark.parametrize("async_callback", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "quote_mode,budget,error",
    [
        ("legacy", 1, "budget_too_low"),
        ("unsigned", 1, "quote_invalid"),
        ("signed", 1, "budget_too_low"),
        ("signed", 10**18, None),
    ],
    ids=["underpriced-legacy", "unsigned-default", "underfunded-signed", "valid-signed"],
)
async def test_discovered_jobs_are_verified_before_callback(
    async_callback, quote_mode, budget, error
):
    """Exercise real discovery and verification, simulating only contract reads."""
    description = "Perform the provider workload"
    wallet = _make_wallet()
    negotiated_at = int(time.time()) - 60
    if quote_mode == "signed":
        description, provider = _signed_description(negotiated_at, service_price=str(10**18))
        wallet = _make_wallet(provider)

    ops = _make_ops(
        wallet=wallet,
        service_price=10**18,
        allow_unsigned_jobs=quote_mode == "legacy",
    )
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.get_job_funded_block.return_value = 123
    client.commerce.address = COMMERCE
    client.w3.eth.chain_id = 97
    client.w3.eth.get_block.return_value = {"timestamp": negotiated_at + 30}
    client.w3.eth.get_code.return_value = b""
    job = _job(provider=wallet.address, description=description, budget=budget)
    client.get_job.return_value = job
    client.commerce.job_counter.return_value = 1
    client.commerce.get_jobs_batch.return_value = [job]

    verdict = await ops.verify_job(1)
    assert verdict["valid"] is (error is None)
    assert verdict.get("error_code") == error
    ops.verify_job = AsyncMock(wraps=ops.verify_job)
    callbacks = []

    def work(verified_job):
        callbacks.append((ops.verify_job.await_count, verified_job))

    async def async_work(verified_job):
        work(verified_job)

    stop = asyncio.Event()
    stop.set()  # Run one discovery iteration, then stop without a timing race.
    await funded_job_watcher(ops, async_work if async_callback else work, interval=0.01, stop=stop)

    client.commerce.get_jobs_batch.assert_called_once_with([1])
    ops.verify_job.assert_awaited_once_with(1)
    assert callbacks == ([] if error else [(1, verdict["job"])]), (
        "Only verified jobs may reach provider work"
    )


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


async def test_invalid_signature_is_permanent_and_funding_scan_is_cached():
    now = int(time.time()) - 60
    description, provider = _signed_description(now)
    envelope = json.loads(description)
    envelope["provider_sig"] = "0x" + "00" * 65
    ops = _make_ops(wallet=_make_wallet(provider), allow_unsigned_jobs=False)
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.commerce.address = COMMERCE
    client.get_job_funded_block.return_value = 123
    client.w3.eth.chain_id = 97
    client.w3.eth.get_block.return_value = {"timestamp": now + 30}
    client.w3.eth.get_code.return_value = b""
    client.get_job.return_value = _job(provider=provider, description=json.dumps(envelope))
    for _ in range(2):
        verdict = await ops.verify_job(1)
        assert verdict["error_code"] == "quote_invalid"
        assert not verdict.get("retryable")
    client.get_job_funded_block.assert_called_once()


async def test_underfunded_signed_job_never_scans_funding_history():
    description, provider = _signed_description(int(time.time()) - 60)
    ops = _make_ops(wallet=_make_wallet(provider), allow_unsigned_jobs=False)
    client = _inject_client(ops)
    client.policy.dispute_window.return_value = 0
    client.get_job.return_value = _job(provider=provider, budget=1, description=description)
    assert (await ops.verify_job(1))["error_code"] == "budget_too_low"
    client.get_job_funded_block.assert_not_called()


@pytest.mark.parametrize("failed_read", [False, True, "throws"])
async def test_watcher_caps_all_attempts_including_failed_retry_reads(failed_read):
    ops = _make_ops()
    fresh = {
        "success": True,
        "jobId": 1,
        "status": JobStatus.FUNDED,
        "expiredAt": int(time.time()) + 3600,
    }
    stop = asyncio.Event()
    polls = 0

    async def pending():
        nonlocal polls
        polls += 1
        if polls == 8:
            stop.set()
        return {"success": True, "jobs": [fresh]}

    ops.get_pending_jobs = pending
    ops.get_job = AsyncMock(return_value={"success": False} if failed_read else fresh)
    if failed_read == "throws":
        ops.get_job.side_effect = TimeoutError("RPC down")
    ops.verify_job = AsyncMock(return_value={"valid": False, "retryable": True})
    work = AsyncMock()
    await funded_job_watcher(ops, work, interval=0.001, stop=stop, max_attempts=3)
    assert ops.verify_job.await_count == (1 if failed_read else 3)
    assert ops.get_job.await_count == 2
    work.assert_not_awaited()


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
