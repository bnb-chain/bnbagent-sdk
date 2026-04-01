"""Tests for APEXEvaluatorClient — UMA OOv3 evaluator interaction."""

from unittest.mock import MagicMock, patch

from web3 import Web3

from bnbagent.apex.evaluator_client import (
    APEXEvaluatorClient,
    AssertionInfo,
)
from tests.conftest import (
    FAKE_ADDRESS,
    FAKE_CONTRACT_ADDRESS,
    FAKE_PRIVATE_KEY,
    FAKE_TX_HASH,
)


class TestAssertionInfo:
    def test_fields(self):
        info = AssertionInfo(
            assertion_id=b"\x01" * 32,
            initiated=True,
            disputed=False,
            liveness_end=1000,
            settleable=True,
        )
        assert info.assertion_id == b"\x01" * 32
        assert info.initiated is True
        assert info.disputed is False
        assert info.liveness_end == 1000
        assert info.settleable is True

    def test_manual_construction(self):
        info = AssertionInfo(
            assertion_id=b"\x00" * 32,
            initiated=False,
            disputed=False,
            liveness_end=0,
            settleable=False,
        )
        assert info.initiated is False


class TestInit:
    def test_with_private_key(self, mock_web3, fake_abi):
        client = APEXEvaluatorClient(mock_web3, FAKE_CONTRACT_ADDRESS, FAKE_PRIVATE_KEY, fake_abi)
        assert client._private_key == FAKE_PRIVATE_KEY
        assert client._account == FAKE_ADDRESS

    def test_without_private_key(self, mock_web3, fake_abi):
        client = APEXEvaluatorClient(mock_web3, FAKE_CONTRACT_ADDRESS, abi=fake_abi)
        assert client._private_key is None
        assert client._account is None

    def test_checksums_address(self, mock_web3, fake_abi):
        lower = FAKE_CONTRACT_ADDRESS.lower()
        client = APEXEvaluatorClient(mock_web3, lower, abi=fake_abi)
        assert client.address == Web3.to_checksum_address(lower)


class TestReadMethods:
    def test_get_assertion_info(self, evaluator_client):
        raw = (b"\xab" * 32, True, False, 9999, True)
        evaluator_client.contract.functions.getAssertionInfo.return_value.call.return_value = raw
        info = evaluator_client.get_assertion_info(1)
        assert isinstance(info, AssertionInfo)
        assert info.assertion_id == b"\xab" * 32
        assert info.initiated is True
        assert info.disputed is False
        assert info.liveness_end == 9999
        assert info.settleable is True

    def test_get_liveness_end(self, evaluator_client):
        evaluator_client.contract.functions.getLivenessEnd.return_value.call.return_value = 12345
        assert evaluator_client.get_liveness_end(1) == 12345

    def test_is_settleable(self, evaluator_client):
        evaluator_client.contract.functions.isSettleable.return_value.call.return_value = True
        assert evaluator_client.is_settleable(1) is True

    def test_get_minimum_bond(self, evaluator_client):
        evaluator_client.contract.functions.getMinimumBond.return_value.call.return_value = 10**18
        assert evaluator_client.get_minimum_bond() == 10**18

    def test_get_liveness(self, evaluator_client):
        evaluator_client.contract.functions.liveness.return_value.call.return_value = 1800
        assert evaluator_client.get_liveness() == 1800

    def test_job_assertion_initiated(self, evaluator_client):
        (
            evaluator_client.contract.functions.jobAssertionInitiated.return_value.call.return_value
        ) = True
        assert evaluator_client.job_assertion_initiated(1) is True

    def test_job_disputed(self, evaluator_client):
        evaluator_client.contract.functions.jobDisputed.return_value.call.return_value = False
        assert evaluator_client.job_disputed(1) is False

    def test_job_to_assertion(self, evaluator_client):
        aid = b"\xcc" * 32
        evaluator_client.contract.functions.jobToAssertion.return_value.call.return_value = aid
        assert evaluator_client.job_to_assertion(1) == aid

    def test_assertion_to_job(self, evaluator_client):
        fn_mock = MagicMock()
        fn_mock.call.return_value = 7
        # "assertionToJob" clashes with Mock's assert_* pattern; set via __dict__
        evaluator_client.contract.functions.__dict__["assertionToJob"] = MagicMock(
            return_value=fn_mock
        )
        assert evaluator_client.assertion_to_job(b"\xcc" * 32) == 7

    def test_get_erc8183_address(self, evaluator_client):
        evaluator_client.contract.functions.erc8183.return_value.call.return_value = FAKE_ADDRESS
        assert evaluator_client.get_erc8183_address() == FAKE_ADDRESS

    def test_get_oov3_address(self, evaluator_client):
        evaluator_client.contract.functions.oov3.return_value.call.return_value = FAKE_ADDRESS
        assert evaluator_client.get_oov3_address() == FAKE_ADDRESS

    def test_get_bond_token_address(self, evaluator_client):
        evaluator_client.contract.functions.bondToken.return_value.call.return_value = FAKE_ADDRESS
        assert evaluator_client.get_bond_token_address() == FAKE_ADDRESS

    def test_is_settleable_false(self, evaluator_client):
        evaluator_client.contract.functions.isSettleable.return_value.call.return_value = False
        assert evaluator_client.is_settleable(1) is False


class TestWriteMethods:
    def _mock_send_tx(self, client):
        client._send_tx = MagicMock(
            return_value={
                "transactionHash": FAKE_TX_HASH,
                "status": 1,
                "receipt": {},
            }
        )

    def test_settle_job(self, evaluator_client):
        self._mock_send_tx(evaluator_client)
        result = evaluator_client.settle_job(1)
        evaluator_client.contract.functions.settleJob.assert_called_once_with(1)
        assert result["status"] == 1

    def test_initiate_assertion(self, evaluator_client):
        self._mock_send_tx(evaluator_client)
        result = evaluator_client.initiate_assertion(1)
        evaluator_client.contract.functions.initiateAssertion.assert_called_once_with(1)
        assert result["status"] == 1

    def test_deposit_bond(self, evaluator_client):
        self._mock_send_tx(evaluator_client)
        result = evaluator_client.deposit_bond(1000)
        evaluator_client.contract.functions.depositBond.assert_called_once_with(1000)
        assert result["status"] == 1

    def test_withdraw_bond(self, evaluator_client):
        self._mock_send_tx(evaluator_client)
        result = evaluator_client.withdraw_bond(500)
        evaluator_client.contract.functions.withdrawBond.assert_called_once_with(500)
        assert result["status"] == 1

    def test_set_bond_token(self, evaluator_client):
        self._mock_send_tx(evaluator_client)
        new_token = "0x" + "aa" * 20
        evaluator_client.set_bond_token(new_token)
        evaluator_client.contract.functions.setBondToken.assert_called_once()


class TestRetry:
    @patch("bnbagent.core.contract_mixin.time.sleep")
    def test_nonce_retry(self, mock_sleep, evaluator_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        evaluator_client.w3.eth.send_raw_transaction.side_effect = [
            Exception("nonce too low"),
            bytes.fromhex(FAKE_TX_HASH[2:]),
        ]
        result = evaluator_client._send_tx(fn)
        assert result["status"] == 1

    @patch("bnbagent.core.contract_mixin.time.sleep")
    def test_rate_limit_retry(self, mock_sleep, evaluator_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        evaluator_client.w3.eth.send_raw_transaction.side_effect = [
            Exception("429 too many requests"),
            bytes.fromhex(FAKE_TX_HASH[2:]),
        ]
        result = evaluator_client._send_tx(fn)
        assert result["status"] == 1
