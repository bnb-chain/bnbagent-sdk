from unittest.mock import MagicMock, patch

import pytest

from bnbagent.erc8183.policy import PolicyClient
from bnbagent.erc8183.types import Verdict
from bnbagent.exceptions import RpcRangeLimitError


@pytest.fixture
def policy_client(mock_web3):
    wallet = MagicMock()
    wallet.address = "0x" + "aa" * 20
    # Mocking the _load_abi to avoid file I/O errors if ABI isn't found
    with patch("bnbagent.erc8183.policy._load_abi", return_value=[{"type": "function", "name": "dispute"}]):
        client = PolicyClient(mock_web3, "0x" + "11" * 20, wallet)
        client._send_tx = MagicMock()
        client._execute_intent = MagicMock()
        client._call_with_retry = MagicMock()
        yield client


class TestPolicyClient:
    def test_dispute(self, policy_client):
        policy_client._execute_intent.return_value = {"status": 1}
        res = policy_client.dispute(1)
        assert res == {"status": 1}
        policy_client._execute_intent.assert_called_once()

    def test_vote_reject(self, policy_client):
        policy_client._execute_intent.return_value = {"status": 1}
        res = policy_client.vote_reject(1)
        assert res == {"status": 1}
        policy_client._execute_intent.assert_called_once()

    def test_check(self, policy_client):
        policy_client._call_with_retry.return_value = (1, b"reason")
        verdict, reason = policy_client.check(1)
        assert verdict == Verdict.APPROVE
        assert reason == b"reason"

    def test_submitted_at(self, policy_client):
        policy_client._call_with_retry.return_value = 1000
        assert policy_client.submitted_at(1) == 1000

    def test_disputed(self, policy_client):
        policy_client._call_with_retry.return_value = True
        assert policy_client.disputed(1) is True

    def test_reject_votes(self, policy_client):
        policy_client._call_with_retry.return_value = 5
        assert policy_client.reject_votes(1) == 5

    def test_has_voted(self, policy_client):
        policy_client._call_with_retry.return_value = True
        assert policy_client.has_voted(1, "0x" + "22" * 20) is True

    def test_is_voter(self, policy_client):
        policy_client._call_with_retry.return_value = True
        assert policy_client.is_voter("0x" + "22" * 20) is True

    def test_dispute_window(self, policy_client):
        policy_client._call_with_retry.return_value = 86400
        assert policy_client.dispute_window() == 86400

    def test_vote_quorum(self, policy_client):
        policy_client._call_with_retry.return_value = 3
        assert policy_client.vote_quorum() == 3

    def test_dispute_quorum_snapshot(self, policy_client):
        policy_client._call_with_retry.return_value = 2
        assert policy_client.dispute_quorum_snapshot(1) == 2

    def test_active_voter_count(self, policy_client):
        policy_client._call_with_retry.return_value = 10
        assert policy_client.active_voter_count() == 10

    def test_admin(self, policy_client):
        policy_client._call_with_retry.return_value = "0x" + "aa" * 20
        assert policy_client.admin() == "0x" + "aa" * 20

    def test_commerce(self, policy_client):
        policy_client._call_with_retry.return_value = "0x" + "bb" * 20
        assert policy_client.commerce() == "0x" + "bb" * 20

    def test_router(self, policy_client):
        policy_client._call_with_retry.return_value = "0x" + "cc" * 20
        assert policy_client.router() == "0x" + "cc" * 20

    def test_add_voter(self, policy_client):
        policy_client._send_tx.return_value = {"status": 1}
        assert policy_client.add_voter("0x" + "dd" * 20) == {"status": 1}

    def test_remove_voter(self, policy_client):
        policy_client._send_tx.return_value = {"status": 1}
        assert policy_client.remove_voter("0x" + "dd" * 20) == {"status": 1}

    def test_set_quorum(self, policy_client):
        policy_client._send_tx.return_value = {"status": 1}
        assert policy_client.set_quorum(5) == {"status": 1}

    class TestGetDeliverableUrl:
        def test_rpc_range_error(self, policy_client):
            policy_client.contract.events.JobInitialised = MagicMock()
            policy_client.contract.events.JobInitialised().get_logs.side_effect = Exception("limit exceeded")
            with pytest.raises(RpcRangeLimitError):
                policy_client.get_deliverable_url(1, hint_block=100)

        def test_no_logs(self, policy_client):
            policy_client.contract.events.JobInitialised = MagicMock()
            policy_client.contract.events.JobInitialised().get_logs.return_value = []
            assert policy_client.get_deliverable_url(1, hint_block=100) is None

        def test_success(self, policy_client):
            policy_client.contract.events.JobInitialised = MagicMock()
            policy_client.contract.events.JobInitialised().get_logs.return_value = [
                {"args": {"optParams": b'{"deliverable_url": "http://test"}'}}
            ]
            assert policy_client.get_deliverable_url(1, hint_block=100) == "http://test"
