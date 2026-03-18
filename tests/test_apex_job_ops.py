"""Tests for APEXJobOps — async job lifecycle operations."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bnbagent.apex.client import APEXStatus
from bnbagent.apex.server.job_ops import APEXJobOps
from tests.conftest import FAKE_ADDRESS, FAKE_PRIVATE_KEY, FAKE_TX_HASH


def _make_job_ops(storage=None):
    """Create APEXJobOps with mocked Web3/APEXClient."""
    ops = APEXJobOps(
        rpc_url="https://fake-rpc.example.com",
        erc8183_address="0x" + "ab" * 20,
        private_key=FAKE_PRIVATE_KEY,
        storage_provider=storage,
        chain_id=97,
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
    @pytest.mark.asyncio
    async def test_funded_jobs_for_provider(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
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
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.w3.eth.block_number = 50000
        client.get_job_funded_events.return_value = []
        await ops.get_pending_jobs()
        call_args = client.get_job_funded_events.call_args[0]
        assert call_args[0] == 50000 - 45000  # max_block_range default

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        ops = _make_job_ops()
        client = _mock_client(ops)
        client.get_job_funded_events.side_effect = Exception("rpc error")
        result = await ops.get_pending_jobs()
        assert result["success"] is False
        assert result["jobs"] == []


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
