"""Tests for APEXJobOps — async job lifecycle operations."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bnbagent.apex.client import APEXStatus
from bnbagent.apex.server.job_ops import APEXJobOps, run_job_loop
from tests.conftest import FAKE_ADDRESS, FAKE_PRIVATE_KEY, FAKE_TX_HASH


def _make_job_ops(storage=None, service_price=0, payment_token_decimals=18):
    """Create APEXJobOps with mocked Web3/APEXClient."""
    ops = APEXJobOps(
        rpc_url="https://fake-rpc.example.com",
        erc8183_address="0x" + "ab" * 20,
        private_key=FAKE_PRIVATE_KEY,
        storage_provider=storage,
        chain_id=97,
        service_price=service_price,
        payment_token_decimals=payment_token_decimals,
    )
    return ops


def _mock_client(ops, job_data=None):
    """Inject a mock APEXClient into ops."""
    client = MagicMock()
    client._account = FAKE_ADDRESS

    if job_data is None:
        job_data = {
            "jobId": 1,
            "client": "0x" + "cc" * 20,
            "provider": FAKE_ADDRESS,
            "evaluator": "0x" + "ee" * 20,
            "hook": "0x" + "00" * 20,
            "budget": 1000,
            "expiredAt": int(time.time()) + 3600,
            "status": APEXStatus.FUNDED,
            "deliverable": b"\x00" * 32,
            "description": "test job",
        }

    client.get_job.return_value = job_data
    client.get_job_status.return_value = APEXStatus.FUNDED
    client.payment_token.return_value = "0xTokenAddr"
    client.get_budget_set_events.return_value = []
    client.submit.return_value = {
        "transactionHash": FAKE_TX_HASH,
        "status": 1,
        "receipt": {},
    }
    client.w3.eth.block_number = 1000
    client.get_job_funded_events.return_value = []

    ops._client = client
    return client


class TestInit:
    def test_normalizes_private_key(self):
        ops = APEXJobOps(
            rpc_url="https://rpc.example.com",
            erc8183_address="0x" + "ab" * 20,
            private_key="cd" * 32,  # No 0x prefix
        )
        assert ops._private_key.startswith("0x")

    def test_lazy_client_creation(self):
        ops = _make_job_ops()
        assert ops._client is None

    def test_agent_address_property(self):
        ops = _make_job_ops()
        _mock_client(ops)
        assert ops.agent_address == FAKE_ADDRESS


class TestSubmitResult:
    @pytest.mark.asyncio
    async def test_with_storage(self):
        storage = AsyncMock()
        storage.upload.return_value = "file:///tmp/job-1.json"
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        result = await ops.submit_result(1, "agent response")
        assert result["success"] is True
        assert result["txHash"] == FAKE_TX_HASH
        assert result["dataUrl"] == "file:///tmp/job-1.json"
        storage.upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_without_storage(self):
        ops = _make_job_ops()
        _mock_client(ops)

        result = await ops.submit_result(1, "agent response")
        assert result["success"] is True
        assert result["dataUrl"] == ""

    @pytest.mark.asyncio
    async def test_verification_fails(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        # Make job have wrong status
        job_data = client.get_job.return_value.copy()
        job_data["status"] = APEXStatus.COMPLETED
        client.get_job.return_value = job_data

        result = await ops.submit_result(1, "response")
        assert result["success"] is False
        assert "verification failed" in result["error"].lower() or "COMPLETED" in result["error"]

    @pytest.mark.asyncio
    async def test_includes_job_context(self):
        storage = AsyncMock()
        storage.upload.return_value = "file:///tmp/test.json"
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        await ops.submit_result(1, "response", include_job_context=True)
        call_args = storage.upload.call_args[0][0]
        assert "job" in call_args

    @pytest.mark.asyncio
    async def test_includes_negotiation_history(self):
        storage = AsyncMock()
        storage.upload.return_value = "file:///tmp/test.json"
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        await ops.submit_result(1, "response", include_negotiation_history=True)
        call_args = storage.upload.call_args[0][0]
        assert "negotiation" in call_args

    @pytest.mark.asyncio
    async def test_chain_error(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.submit.side_effect = Exception("execution reverted")

        result = await ops.submit_result(1, "response")
        assert result["success"] is False
        assert "execution reverted" in result["error"]

    @pytest.mark.asyncio
    async def test_storage_error(self):
        storage = AsyncMock()
        storage.upload.side_effect = Exception("upload failed")
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        result = await ops.submit_result(1, "response")
        assert result["success"] is False


class TestGetJob:
    @pytest.mark.asyncio
    async def test_success(self):
        ops = _make_job_ops()
        _mock_client(ops)
        result = await ops.get_job(1)
        assert result["success"] is True
        assert result["jobId"] == 1

    @pytest.mark.asyncio
    async def test_failure(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.get_job.side_effect = Exception("not found")
        result = await ops.get_job(999)
        assert result["success"] is False


class TestGetResponse:
    @pytest.mark.asyncio
    async def test_from_cache(self):
        """get_response returns data when URL is in the in-memory cache."""
        storage = AsyncMock()
        stored_data = {"response": "agent output", "job": {"id": 1}}
        storage.download.return_value = stored_data
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        # Simulate submit_result populating the cache
        ops._deliverable_urls[1] = "file:///tmp/job-1.json"

        result = await ops.get_response(1)
        assert result["success"] is True
        assert result["response"] == "agent output"
        storage.download.assert_called_once_with("file:///tmp/job-1.json")

    @pytest.mark.asyncio
    async def test_from_local_file(self, tmp_path):
        """get_response falls back to reading job-{id}.json from local storage base dir."""
        from bnbagent.storage.local_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        # Write a job file directly
        job_file = tmp_path / "job-42.json"
        job_file.write_text('{"response":"from file","job":{"id":42}}')

        result = await ops.get_response(42)
        assert result["success"] is True
        assert result["response"] == "from file"

    @pytest.mark.asyncio
    async def test_not_found(self):
        """get_response returns error when no stored response exists."""
        storage = AsyncMock()
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        result = await ops.get_response(999)
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_storage(self):
        """get_response returns error when storage is not configured."""
        ops = _make_job_ops(storage=None)
        _mock_client(ops)

        result = await ops.get_response(1)
        assert result["success"] is False
        assert "storage" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_submit_populates_cache(self):
        """submit_result stores the data URL in _deliverable_urls."""
        storage = AsyncMock()
        storage.upload.return_value = "ipfs://QmTest123"
        ops = _make_job_ops(storage=storage)
        _mock_client(ops)

        await ops.submit_result(5, "response text")
        assert ops._deliverable_urls[5] == "ipfs://QmTest123"


class TestGetJobStatus:
    @pytest.mark.asyncio
    async def test_success(self):
        ops = _make_job_ops()
        _mock_client(ops)
        result = await ops.get_job_status(1)
        assert result["success"] is True
        assert result["status"] == APEXStatus.FUNDED

    @pytest.mark.asyncio
    async def test_failure(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.get_job_status.side_effect = Exception("rpc error")
        result = await ops.get_job_status(1)
        assert result["success"] is False


class TestGetPendingJobs:
    """Tests for the progressive event scanning path (post-startup)."""

    @pytest.mark.asyncio
    async def test_funded_jobs_for_provider(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500
        client.get_job_funded_events.return_value = [
            {
                "jobId": 1,
                "client": "0xabc",
                "amount": 100,
                "blockNumber": 50,
                "transactionHash": "0xhash",
            },
        ]
        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert len(result["jobs"]) == 1

    @pytest.mark.asyncio
    async def test_skips_non_funded(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500
        job_data = client.get_job.return_value.copy()
        job_data["status"] = APEXStatus.COMPLETED
        client.get_job.return_value = job_data
        client.get_job_funded_events.return_value = [
            {
                "jobId": 1,
                "client": "0xabc",
                "amount": 100,
                "blockNumber": 50,
                "transactionHash": "0xhash",
            },
        ]
        result = await ops.get_pending_jobs()
        assert len(result["jobs"]) == 0

    @pytest.mark.asyncio
    async def test_skips_other_providers(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500
        job_data = client.get_job.return_value.copy()
        job_data["provider"] = "0x" + "ff" * 20  # Different provider
        client.get_job.return_value = job_data
        client.get_job_funded_events.return_value = [
            {
                "jobId": 1,
                "client": "0xabc",
                "amount": 100,
                "blockNumber": 50,
                "transactionHash": "0xhash",
            },
        ]
        result = await ops.get_pending_jobs()
        assert len(result["jobs"]) == 0

    @pytest.mark.asyncio
    async def test_auto_from_block(self):
        """Progressive scanning uses _last_scanned_block - 5 as from_block."""
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500
        client.w3.eth.block_number = 1000
        client.get_job_funded_events.return_value = []
        await ops.get_pending_jobs()
        call_args = client.get_job_funded_events.call_args[0]
        assert call_args[0] == 500 - 5  # _last_scanned_block - reorg overlap

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500
        client.get_job_funded_events.side_effect = Exception("rpc error")
        result = await ops.get_pending_jobs()
        assert result["success"] is False
        assert result["jobs"] == []


class TestStartupScan:
    """Tests for the one-time Multicall3 startup scan."""

    @pytest.mark.asyncio
    async def test_finds_funded_jobs(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.next_job_id.return_value = 5

        future_ts = int(time.time()) + 3600
        jobs_batch = [
            None,  # job 0 failed
            {"jobId": 1, "provider": FAKE_ADDRESS, "status": APEXStatus.FUNDED,
             "expiredAt": future_ts, "description": "job 1"},
            {"jobId": 2, "provider": "0x" + "ff" * 20, "status": APEXStatus.FUNDED,
             "expiredAt": future_ts, "description": "job 2"},  # wrong provider
            {"jobId": 3, "provider": FAKE_ADDRESS, "status": APEXStatus.COMPLETED,
             "expiredAt": future_ts, "description": "job 3"},  # not funded
            {"jobId": 4, "provider": FAKE_ADDRESS, "status": APEXStatus.FUNDED,
             "expiredAt": future_ts, "description": "job 4"},
        ]
        client.get_jobs_batch.return_value = jobs_batch

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert len(result["jobs"]) == 2
        job_ids = {j["jobId"] for j in result["jobs"]}
        assert job_ids == {1, 4}

    @pytest.mark.asyncio
    async def test_empty_contract(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.next_job_id.return_value = 0

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []
        assert ops._startup_scan_done is True

    @pytest.mark.asyncio
    async def test_filters_wrong_provider(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.next_job_id.return_value = 1

        future_ts = int(time.time()) + 3600
        client.get_jobs_batch.return_value = [
            {"jobId": 0, "provider": "0x" + "ff" * 20, "status": APEXStatus.FUNDED,
             "expiredAt": future_ts, "description": "other agent's job"},
        ]

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []

    @pytest.mark.asyncio
    async def test_filters_non_funded(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.next_job_id.return_value = 1

        future_ts = int(time.time()) + 3600
        client.get_jobs_batch.return_value = [
            {"jobId": 0, "provider": FAKE_ADDRESS, "status": APEXStatus.COMPLETED,
             "expiredAt": future_ts, "description": "completed job"},
        ]

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []

    @pytest.mark.asyncio
    async def test_filters_expired(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.next_job_id.return_value = 1

        past_ts = int(time.time()) - 100
        client.get_jobs_batch.return_value = [
            {"jobId": 0, "provider": FAKE_ADDRESS, "status": APEXStatus.FUNDED,
             "expiredAt": past_ts, "description": "expired job"},
        ]

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []

    @pytest.mark.asyncio
    async def test_sets_last_scanned_block(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.w3.eth.block_number = 12345
        client.next_job_id.return_value = 0

        await ops.get_pending_jobs()
        assert ops._last_scanned_block == 12345
        assert ops._startup_scan_done is True

    @pytest.mark.asyncio
    async def test_multicall_fallback(self):
        """When multicall fails, falls back to event scan."""
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.w3.eth.block_number = 50000
        client.next_job_id.side_effect = Exception("multicall revert")
        client.get_job_funded_events.return_value = [
            {"jobId": 1, "client": "0xabc", "amount": 100,
             "blockNumber": 50, "transactionHash": "0xhash"},
        ]

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert len(result["jobs"]) == 1
        assert ops._startup_scan_done is True
        assert ops._last_scanned_block == 50000


class TestProgressiveScanning:
    """Tests for the progressive (incremental) event scanning after startup."""

    @pytest.mark.asyncio
    async def test_scans_from_last_block(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.w3.eth.block_number = 1010
        client.get_job_funded_events.return_value = []

        await ops.get_pending_jobs()
        call_args = client.get_job_funded_events.call_args[0]
        assert call_args[0] == 1000 - 5  # 5-block reorg overlap

    @pytest.mark.asyncio
    async def test_no_new_blocks(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.w3.eth.block_number = 1004  # scan_from (995) < 1004, so scan proceeds

        client.get_job_funded_events.return_value = []
        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []

    @pytest.mark.asyncio
    async def test_no_new_blocks_exact(self):
        """When scan_from >= latest_block, skip event query."""
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.w3.eth.block_number = 995  # scan_from=995, latest=995

        result = await ops.get_pending_jobs()
        assert result["success"] is True
        assert result["jobs"] == []
        client.get_job_funded_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_last_scanned(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.w3.eth.block_number = 1010
        client.get_job_funded_events.return_value = []

        await ops.get_pending_jobs()
        assert ops._last_scanned_block == 1010

    @pytest.mark.asyncio
    async def test_explicit_from_block_honored(self):
        """Caller passes from_block → used directly for event scan."""
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.get_job_funded_events.return_value = []

        await ops.get_pending_jobs(from_block=500)
        call_args = client.get_job_funded_events.call_args[0]
        assert call_args[0] == 500

    @pytest.mark.asyncio
    async def test_explicit_from_block_no_state_update(self):
        """Explicit from_block does NOT update _last_scanned_block."""
        ops = _make_job_ops()
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 1000
        client.get_job_funded_events.return_value = []

        await ops.get_pending_jobs(from_block=500)
        assert ops._last_scanned_block == 1000  # unchanged


class TestVerifyJob:
    @pytest.mark.asyncio
    async def test_valid_funded(self):
        ops = _make_job_ops()
        _mock_client(ops)
        result = await ops.verify_job(1)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_wrong_status(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["status"] = APEXStatus.COMPLETED
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 409

    @pytest.mark.asyncio
    async def test_wrong_provider(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["provider"] = "0x" + "ff" * 20
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 403

    @pytest.mark.asyncio
    async def test_expired(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["expiredAt"] = int(time.time()) - 100  # In the past
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 408

    @pytest.mark.asyncio
    async def test_evaluator_equals_client_warning(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["evaluator"] = job_data["client"]  # Same as client
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is True
        assert result["warnings"] is not None
        assert any("CLIENT_AS_EVALUATOR" in w["code"] for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_budget_below_service_price(self):
        ops = _make_job_ops(service_price=10**18)
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 5 * 10**17  # 0.5 tokens < 1 token
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 402
        assert result["service_price"] == str(10**18)
        assert result["decimals"] == 18

    @pytest.mark.asyncio
    async def test_budget_sufficient(self):
        ops = _make_job_ops(service_price=10**18)
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 2 * 10**18  # 2 tokens >= 1 token
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_budget_check_skipped_when_service_price_zero(self):
        """service_price=0 (default) → no budget check, backward compatible."""
        ops = _make_job_ops(service_price=0)
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 0
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_budget_check_custom_decimals(self):
        ops = _make_job_ops(service_price=10**6, payment_token_decimals=6)
        client = _mock_client(ops)
        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 5 * 10**5  # 0.5 tokens < 1 token
        client.get_job.return_value = job_data
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["decimals"] == 6


class TestRunJobLoop:
    @pytest.mark.asyncio
    async def test_skipped_jobs_not_re_verified(self):
        """Same job should only be verified once after being skipped."""
        ops = _make_job_ops(service_price=10**18)
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500

        # Job with insufficient budget
        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 100  # way below service_price
        client.get_job.return_value = job_data

        # get_pending_jobs returns same job twice (two polling cycles)
        client.get_job_funded_events.return_value = [
            {"jobId": 1, "client": "0xabc", "amount": 100,
             "blockNumber": 50, "transactionHash": "0xhash"},
        ]

        poll_count = 0

        async def _patched_get_pending(from_block=None, to_block="latest", max_block_range=45000):
            nonlocal poll_count
            poll_count += 1
            if poll_count > 2:
                raise KeyboardInterrupt  # stop loop
            return await ops._original_get_pending()

        # Save original and patch
        ops._original_get_pending = ops.get_pending_jobs
        ops.get_pending_jobs = _patched_get_pending

        import asyncio

        with pytest.raises(KeyboardInterrupt):
            await run_job_loop(
                job_ops=ops,
                on_job=lambda job: "result",
                poll_interval=0,
            )

        # verify_job called only once (skipped on second poll)
        assert client.get_job.call_count <= 3  # get_pending calls get_job, then verify calls get_job once

    @pytest.mark.asyncio
    async def test_on_job_skipped_sync_callback(self):
        """Sync on_job_skipped callback is called with job and reason."""
        ops = _make_job_ops(service_price=10**18)
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500

        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 100
        client.get_job.return_value = job_data
        client.get_job_funded_events.return_value = [
            {"jobId": 1, "client": "0xabc", "amount": 100,
             "blockNumber": 50, "transactionHash": "0xhash"},
        ]

        callback_calls = []

        def on_skipped(job, reason):
            callback_calls.append((job, reason))

        poll_count = 0

        async def _patched_get_pending(from_block=None, to_block="latest", max_block_range=45000):
            nonlocal poll_count
            poll_count += 1
            if poll_count > 1:
                raise KeyboardInterrupt
            return await ops._original_get_pending()

        ops._original_get_pending = ops.get_pending_jobs
        ops.get_pending_jobs = _patched_get_pending

        with pytest.raises(KeyboardInterrupt):
            await run_job_loop(
                job_ops=ops,
                on_job=lambda job: "result",
                poll_interval=0,
                on_job_skipped=on_skipped,
            )

        assert len(callback_calls) == 1
        assert "budget" in callback_calls[0][1].lower() or "service price" in callback_calls[0][1].lower()

    @pytest.mark.asyncio
    async def test_on_job_skipped_async_callback(self):
        """Async on_job_skipped callback is awaited."""
        ops = _make_job_ops(service_price=10**18)
        client = _mock_client(ops)
        ops._startup_scan_done = True
        ops._last_scanned_block = 500

        job_data = client.get_job.return_value.copy()
        job_data["budget"] = 100
        client.get_job.return_value = job_data
        client.get_job_funded_events.return_value = [
            {"jobId": 1, "client": "0xabc", "amount": 100,
             "blockNumber": 50, "transactionHash": "0xhash"},
        ]

        callback_calls = []

        async def on_skipped(job, reason):
            callback_calls.append((job, reason))

        poll_count = 0

        async def _patched_get_pending(from_block=None, to_block="latest", max_block_range=45000):
            nonlocal poll_count
            poll_count += 1
            if poll_count > 1:
                raise KeyboardInterrupt
            return await ops._original_get_pending()

        ops._original_get_pending = ops.get_pending_jobs
        ops.get_pending_jobs = _patched_get_pending

        with pytest.raises(KeyboardInterrupt):
            await run_job_loop(
                job_ops=ops,
                on_job=lambda job: "result",
                poll_interval=0,
                on_job_skipped=on_skipped,
            )

        assert len(callback_calls) == 1
