"""SRC-1623: bound work behind repeated and concurrent public lookups."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from bnbagent.erc8183.client import ERC8183Client
from bnbagent.erc8183.job_ops import ERC8183JobOps
from bnbagent.erc8183.types import JobStatus
from tests.test_erc8183_job_ops import ME, _inject_client


def make_ops(**kwargs):
    storage = MagicMock(spec=["download", "upload"])
    storage.download = AsyncMock(return_value={"response": "ready"})
    ops = ERC8183JobOps(provider_address=ME, storage_provider=storage, **kwargs)
    client = _inject_client(ops)
    client.get_deliverable_url.return_value = None
    ops.get_job = AsyncMock(return_value={"success": True, "status": JobStatus.SUBMITTED})
    return ops, client


async def test_repeated_miss_only_resolves_once():
    ops, client = make_ops()
    for _ in range(3):
        assert (await ops.get_response(1))["error_code"] == "chain_unavailable"
    client.get_deliverable_url.assert_called_once_with(1)


async def test_concurrent_queries_share_one_resolution():
    ops, client = make_ops()
    results = await asyncio.gather(*(ops.get_response(1) for _ in range(20)))
    assert all(r["error_code"] == "chain_unavailable" for r in results)
    client.get_deliverable_url.assert_called_once_with(1)


async def test_unsubmitted_job_does_not_scan_logs():
    ops, client = make_ops()
    ops.get_job.return_value = {"success": True, "status": JobStatus.FUNDED}
    assert (await ops.get_response(1))["error_code"] == "not_found"
    client.get_deliverable_url.assert_not_called()


async def test_cache_expiry_reveals_new_submission():
    ops, client = make_ops(response_cache_ttl=0.01)
    await ops.get_response(1)
    client.get_deliverable_url.return_value = "ipfs://new"
    await asyncio.sleep(0.03)
    assert (await ops.get_response(1))["success"]
    assert client.get_deliverable_url.call_count == 2


async def test_local_submission_bypasses_prior_negative_cache():
    ops, client = make_ops()
    await ops.get_response(1)
    ops._deliverable_urls[1] = "ipfs://locally-submitted"
    assert (await ops.get_response(1))["success"]
    client.get_deliverable_url.assert_called_once()


async def test_cache_capacity_evicts_old_misses():
    ops, client = make_ops(response_cache_max_entries=2)
    for job_id in (1, 2, 3, 1):
        await ops.get_response(job_id)
    assert client.get_deliverable_url.call_count == 4


async def test_cancelled_caller_does_not_cancel_shared_lookup():
    ops, client = make_ops()
    entered, release = asyncio.Event(), asyncio.Event()

    async def status(_):
        entered.set()
        await release.wait()
        return {"success": True, "status": JobStatus.SUBMITTED}

    ops.get_job = status
    first = asyncio.create_task(ops.get_response(1))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(ops.get_response(1))
        release.set()
        await asyncio.wait_for(second, 1)
        client.get_deliverable_url.assert_called_once()
    finally:
        release.set()


async def test_different_jobs_cannot_exceed_inflight_limit():
    ops, client = make_ops(response_max_inflight=2)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def status(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
        await release.wait()
        return {"success": True, "status": JobStatus.SUBMITTED}

    ops.get_job = status
    running = [asyncio.create_task(ops.get_response(i)) for i in (1, 2)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(ops.get_response(3), 0.2)
        assert result["error_code"] == "chain_unavailable"
        assert result["retryable"]
        assert calls == 2
    finally:
        release.set()
        await asyncio.gather(*running)


@pytest.mark.parametrize("job_id", [0, -1, 2**256, True])
async def test_invalid_job_ids_do_not_reach_rpc(job_id):
    ops, client = make_ops()
    assert not (await ops.get_response(job_id))["success"]
    client.get_deliverable_url.assert_not_called()
    ops.get_job.assert_not_awaited()


def test_scan_respects_exact_block_budget():
    client = object.__new__(ERC8183Client)
    client.commerce = MagicMock()
    client.commerce.w3.eth.block_number = 100000
    logs = client.commerce.contract.events.JobSubmitted.return_value.get_logs
    logs.return_value = []
    assert client._resolve_submit_block(1) is None
    assert logs.call_count == 50
    ranges = [(c.kwargs["from_block"], c.kwargs["to_block"]) for c in logs.call_args_list]
    assert sum(end - start + 1 for start, end in ranges) == 50000


def test_missing_height_does_not_fall_back_to_genesis():
    from bnbagent.erc8183.policy import PolicyClient
    from bnbagent.exceptions import RpcRangeLimitError

    w3 = MagicMock()
    policy = PolicyClient(w3, "0x" + "f8" * 20, abi=[])
    with patch.object(
        type(w3.eth), "block_number", new_callable=PropertyMock, create=True
    ) as height:
        height.side_effect = OSError("RPC unavailable")
        with pytest.raises(RpcRangeLimitError):
            policy.get_deliverable_url(1)
    policy.contract.events.JobInitialised.assert_not_called()
