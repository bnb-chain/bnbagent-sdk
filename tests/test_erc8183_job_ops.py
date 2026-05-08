"""Tests for ``ERC8183JobOps`` — async provider-side lifecycle ops (ERC-8183).

Focus areas:
- ``verify_job`` — status / provider / expiry / budget gating.
- ``submit_result`` — manifest construction, upload, and on-chain submit.
"""

import time
from unittest.mock import MagicMock

import pytest

from bnbagent.erc8183.server.job_ops import ERC8183JobOps
from bnbagent.erc8183.types import Job, JobStatus

ME = "0x" + "aa" * 20
OTHER = "0x" + "bb" * 20
CLIENT = "0x" + "cc" * 20


def _make_wallet(address=ME):
    wp = MagicMock()
    wp.address = address
    return wp


def _make_ops(storage=None, service_price=0, wallet=None, agent_url=None):
    ops = ERC8183JobOps(
        wallet or _make_wallet(),
        storage_provider=storage,
        service_price=service_price,
        agent_url=agent_url,
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
            ERC8183JobOps(None)  # type: ignore[arg-type]


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
    async def test_rejects_malformed_description_fail_closed(self):
        import json as _json

        bad = _json.dumps(
            {
                "version": 1,
                "negotiated_at": 1_700_000_000,
                "task": "x",
                "terms": {"deliverables": "y", "quality_standards": "z"},
                "price": "1",
                "currency": "0x" + "00" * 20,
                # type-confused: string instead of int
                "quote_expires_at": "not-an-int",
            }
        )
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(description=bad)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 410
        assert "Malformed" in result["error"]

    @pytest.mark.asyncio
    async def test_rejects_expired_quote(self):
        import json as _json

        past = int(time.time()) - 1
        good = _json.dumps(
            {
                "version": 1,
                "negotiated_at": past - 60,
                "task": "x",
                "terms": {"deliverables": "y", "quality_standards": "z"},
                "price": "1",
                "currency": "0x" + "00" * 20,
                "quote_expires_at": past,
            }
        )
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(description=good)
        result = await ops.verify_job(1)
        assert result["valid"] is False
        assert result["error_code"] == 410
        assert "expired" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_accepts_equal_or_higher_budget(self):
        ops = _make_ops(service_price=1000)
        client = _inject_client(ops)
        client.get_job.return_value = _job(budget=1000)
        result = await ops.verify_job(1)
        assert result["valid"] is True


class TestSubmitResult:
    @pytest.mark.asyncio
    async def test_submit_uploads_and_returns_deliverable(self, tmp_path):
        from bnbagent.storage.local_storage_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_ops(storage=storage, agent_url="http://agent.example/erc8183")
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.submit.return_value = {"transactionHash": "0xaa"}
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "hello")
        assert result["success"] is True
        assert "deliverable" in result
        assert "deliverableUrl" in result

    @pytest.mark.asyncio
    async def test_submit_blocked_on_failed_verify(self):
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.OPEN)
        result = await ops.submit_result(1, "x")
        assert result["success"] is False
        client.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_response_content_size_cap_enforced(self, monkeypatch):
        monkeypatch.setenv("ERC8183_MAX_RESPONSE_BYTES", "1024")
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        result = await ops.submit_result(1, "x" * 1025)
        assert result["success"] is False
        assert result["error_code"] == 413
        assert "response_content size" in result["error"]
        client.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_metadata_size_cap_enforced(self, monkeypatch):
        monkeypatch.setenv("ERC8183_MAX_METADATA_BYTES", "256")
        ops = _make_ops()
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        result = await ops.submit_result(1, "ok", metadata={"k": "v" * 400})
        assert result["success"] is False
        assert result["error_code"] == 413
        assert "metadata size" in result["error"]
        client.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_within_caps_proceeds(self, tmp_path):
        from bnbagent.storage.local_storage_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_ops(storage=storage, agent_url="http://agent.example/erc8183")
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.submit.return_value = {"transactionHash": "0xaa"}
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "ok", metadata={"small": "value"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_file_url_rewritten_to_agent_endpoint(self, tmp_path):
        from bnbagent.storage.local_storage_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_ops(storage=storage, agent_url="http://myagent.example/erc8183")
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.submit.return_value = {"transactionHash": "0xab"}
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "payload")
        assert result["success"] is True
        assert result["deliverableUrl"] == "http://myagent.example/erc8183/job/1/response"
        # chain submit received the agent endpoint URL, not the file:// URL
        call_kwargs = client.submit.call_args
        opt_params = call_kwargs[0][2]
        assert opt_params["deliverable_url"] == "http://myagent.example/erc8183/job/1/response"
        # internal cache still holds the raw file:// URL
        assert ops._deliverable_urls[1].startswith("file://")

    @pytest.mark.asyncio
    async def test_ipfs_url_passed_through_unchanged(self, tmp_path):
        from unittest.mock import AsyncMock

        mock_storage = MagicMock()
        mock_storage.upload = AsyncMock(return_value="ipfs://QmFakeHash1234")
        ops = _make_ops(storage=mock_storage)  # no agent_url needed for ipfs
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.submit.return_value = {"transactionHash": "0xac"}
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "payload")
        assert result["success"] is True
        assert result["deliverableUrl"] == "ipfs://QmFakeHash1234"

    @pytest.mark.asyncio
    async def test_file_url_without_agent_url_raises(self, tmp_path):
        from bnbagent.storage.local_storage_provider import LocalStorageProvider

        storage = LocalStorageProvider(str(tmp_path))
        ops = _make_ops(storage=storage, agent_url=None)
        client = _inject_client(ops)
        client.get_job.return_value = _job(status=JobStatus.FUNDED)
        client.commerce.address = "0x" + "11" * 20
        client.router.address = "0x" + "22" * 20
        client.policy.address = "0x" + "33" * 20
        client.commerce.w3.eth.chain_id = 97

        result = await ops.submit_result(1, "payload")
        assert result["success"] is False
        assert "ERC8183_AGENT_URL" in result["error"]


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
