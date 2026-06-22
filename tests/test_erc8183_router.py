from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from bnbagent.erc8183.router import RouterClient
from bnbagent.erc8183.types import JobStatus, Verdict


@pytest.fixture
def router_client(mock_web3):
    wallet = MagicMock()
    wallet.address = "0x" + "aa" * 20
    with patch("bnbagent.erc8183.router._load_abi", return_value=[{"type": "function", "name": "settle"}]):
        client = RouterClient(mock_web3, "0x" + "11" * 20, wallet)
        client._send_tx = MagicMock()
        client._call_with_retry = MagicMock()
        yield client


class TestRouterClient:
    def test_register_job(self, router_client):
        router_client._send_tx.return_value = {"status": 1}
        assert router_client.register_job(1, "0x" + "22" * 20) == {"status": 1}

    def test_settle(self, router_client):
        router_client._send_tx.return_value = {"status": 1}
        assert router_client.settle(1, b"evidence") == {"status": 1}

    def test_mark_expired(self, router_client):
        router_client._send_tx.return_value = {"status": 1}
        assert router_client.mark_expired(1) == {"status": 1}

    def test_commerce(self, router_client):
        router_client._call_with_retry.return_value = "0x" + "bb" * 20
        assert router_client.commerce() == "0x" + "bb" * 20

    def test_inflight_job_count(self, router_client):
        router_client._call_with_retry.return_value = 5
        assert router_client.inflight_job_count() == 5

    def test_job_policy(self, router_client):
        router_client._call_with_retry.return_value = "0x" + "cc" * 20
        assert router_client.job_policy(1) == "0x" + "cc" * 20

    def test_policy_whitelist(self, router_client):
        router_client._call_with_retry.return_value = True
        assert router_client.policy_whitelist("0x" + "cc" * 20) is True

    def test_paused(self, router_client):
        router_client._call_with_retry.return_value = False
        assert router_client.paused() is False

    def test_get_job_registered_events(self, router_client):
        router_client.contract.events.JobRegistered = MagicMock()
        router_client.contract.events.JobRegistered().get_logs.return_value = [
            {"args": {"jobId": 1, "policy": "0xcc", "client": "0xaa"}, "blockNumber": 100, "transactionHash": b"hash"}
        ]
        logs = router_client.get_job_registered_events(0, client="0x" + "aa" * 20)
        assert len(logs) == 1
        assert logs[0]["jobId"] == 1

    def test_get_job_settled_events(self, router_client):
        router_client.contract.events.JobSettled = MagicMock()
        router_client.contract.events.JobSettled().get_logs.return_value = [
            {"args": {"jobId": 1, "verdict": 1, "reason": b"reason"}, "blockNumber": 100, "transactionHash": b"hash"}
        ]
        logs = router_client.get_job_settled_events(0, verdict=Verdict.APPROVE)
        assert len(logs) == 1
        assert logs[0]["verdict"] == Verdict.APPROVE

    def test_get_job_finalised_events(self, router_client):
        router_client.contract.events.JobFinalised = MagicMock()
        router_client.contract.events.JobFinalised().get_logs.return_value = [
            {"args": {"jobId": 1, "status": 3}, "blockNumber": 100, "transactionHash": b"hash"}
        ]
        logs = router_client.get_job_finalised_events(0, status=JobStatus.COMPLETED)
        assert len(logs) == 1
        assert logs[0]["status"] == JobStatus.COMPLETED
