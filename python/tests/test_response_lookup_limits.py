"""SRC-1623: bound work behind repeated and concurrent public lookups."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from web3 import Web3

from bnbagent.erc8183.client import ERC8183Client
from bnbagent.erc8183.job_ops import ERC8183JobOps
from bnbagent.erc8183.schema import DeliverableManifest
from bnbagent.erc8183.types import JobStatus
from tests.test_erc8183_job_ops import COMMERCE, ME, _inject_client


def make_ops(**kwargs):
    storage = MagicMock(spec=["download", "upload"])
    ops = ERC8183JobOps(provider_address=ME, storage_provider=storage, **kwargs)
    client = _inject_client(ops)
    client.get_deliverable_url.return_value = None

    async def status(job_id):
        data = DeliverableManifest(
            version=1,
            job_id=job_id,
            chain_id=97,
            contracts={"commerce": COMMERCE},
            response={"content": "ready"},
        ).to_dict()
        storage.download.return_value = data
        return {
            "success": True,
            "status": JobStatus.SUBMITTED,
            "provider": ME,
            "deliverable": Web3.to_hex(DeliverableManifest.from_dict(data).manifest_hash()),
        }

    storage.download = AsyncMock()
    ops.get_job = AsyncMock(side_effect=status)
    return ops, client


async def test_repeated_miss_only_resolves_once():
    ops, client = make_ops()
    for _ in range(3):
        assert (await ops.get_response(1))["error_code"] == "chain_unavailable"
    client.get_deliverable_url.assert_called_once_with(1)


async def test_foreign_provider_is_rejected_before_url_lookup_or_download():
    ops, client = make_ops()
    ops.get_job = AsyncMock(return_value={"success": True, "provider": "0x" + "22" * 20})
    assert (await ops.get_response(1))["error_code"] == "not_found"
    client.get_deliverable_url.assert_not_called()
    ops._storage.download.assert_not_awaited()


async def test_success_cache_is_bounded_and_returns_independent_data():
    ops, client = make_ops(response_cache_max_entries=2)
    client.get_deliverable_url.return_value = "ipfs://result"
    first = await ops.get_response(1)
    first["response"]["content"] = "mutated"
    assert (await ops.get_response(1))["response"]["content"] == "ready"
    ops._storage.download.assert_awaited_once()
    for job_id in (2, 3, 1):
        await ops.get_response(job_id)
    assert ops._storage.download.await_count == 4
    assert len(ops._response_successes) == 2
    assert ops._response_success_bytes <= 16 * 1024 * 1024


async def test_success_cache_evicts_by_bytes_below_the_entry_limit():
    ops, client = make_ops()
    client.get_deliverable_url.return_value = "ipfs://large-result"
    base_status = ops.get_job.side_effect

    async def large_status(job_id):
        await base_status(job_id)
        data = ops._storage.download.return_value
        data["response"]["content"] = "x" * (6 * 1024 * 1024)
        return {
            "success": True,
            "status": JobStatus.SUBMITTED,
            "provider": ME,
            "deliverable": Web3.to_hex(DeliverableManifest.from_dict(data).manifest_hash()),
        }

    ops.get_job = AsyncMock(side_effect=large_status)
    for job_id in (1, 2, 3, 1):
        assert (await ops.get_response(job_id))["success"]
    assert ops._storage.download.await_count == 4
    assert ops._response_success_bytes <= 16 * 1024 * 1024


async def test_concurrent_queries_share_one_resolution():
    ops, client = make_ops()
    results = await asyncio.gather(*(ops.get_response(1) for _ in range(20)))
    assert all(r["error_code"] == "chain_unavailable" for r in results)
    client.get_deliverable_url.assert_called_once_with(1)


async def test_unsubmitted_job_does_not_scan_logs():
    ops, client = make_ops()
    ops.get_job = AsyncMock(return_value={"success": True, "status": JobStatus.FUNDED})
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
        return {
            "success": True,
            "status": JobStatus.SUBMITTED,
            "provider": ME,
            "deliverable": "0x" + "00" * 32,
        }

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
        return {
            "success": True,
            "status": JobStatus.SUBMITTED,
            "provider": ME,
            "deliverable": "0x" + "00" * 32,
        }

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


def test_submission_lookup_reads_a_bounded_number_of_blocks():
    # Two seconds a block: the submission is in block 50,000 of 100,000, found
    # in at most ceil(log2(100,001)) = 17 header reads and no log scan.
    client = object.__new__(ERC8183Client)
    client.commerce = MagicMock()
    client.w3 = MagicMock()
    client.w3.eth.block_number = 100000
    client.w3.eth.get_block.side_effect = lambda n: {"timestamp": n * 2}
    client.get_job = MagicMock(return_value=SimpleNamespace(submitted_at=100000))
    assert client._resolve_submit_block(1) == 50000
    assert client.w3.eth.get_block.call_count <= 17
    client.commerce.contract.events.JobSubmitted.assert_not_called()


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
