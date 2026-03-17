"""Tests for APEXClient — ERC-8183 contract interaction."""

import time
from enum import IntEnum
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from bnbagent.apex_client import (
    APEXClient,
    APEXStatus,
    DEFAULT_LIVENESS_SECONDS,
    DVM_BUFFER_SECONDS,
    get_default_expiry,
)
from tests.conftest import (
    FAKE_ADDRESS,
    FAKE_PRIVATE_KEY,
    FAKE_CONTRACT_ADDRESS,
    FAKE_TX_HASH,
)


class TestAPEXStatus:
    def test_enum_values(self):
        assert APEXStatus.NONE == 0
        assert APEXStatus.OPEN == 1
        assert APEXStatus.FUNDED == 2
        assert APEXStatus.SUBMITTED == 3
        assert APEXStatus.COMPLETED == 4
        assert APEXStatus.REJECTED == 5
        assert APEXStatus.EXPIRED == 6

    def test_is_int_enum(self):
        assert issubclass(APEXStatus, IntEnum)
        assert isinstance(APEXStatus.FUNDED, int)


class TestGetDefaultExpiry:
    def test_returns_future_timestamp(self):
        expiry = get_default_expiry()
        assert expiry > int(time.time())

    def test_includes_dvm_buffer(self):
        now = int(time.time())
        expiry = get_default_expiry()
        expected_min = now + DEFAULT_LIVENESS_SECONDS + DVM_BUFFER_SECONDS - 2
        expected_max = now + DEFAULT_LIVENESS_SECONDS + DVM_BUFFER_SECONDS + 2
        assert expected_min <= expiry <= expected_max

    def test_custom_liveness(self):
        now = int(time.time())
        custom_liveness = 3600
        expiry = get_default_expiry(liveness_seconds=custom_liveness)
        expected_min = now + custom_liveness + DVM_BUFFER_SECONDS - 2
        expected_max = now + custom_liveness + DVM_BUFFER_SECONDS + 2
        assert expected_min <= expiry <= expected_max


class TestInit:
    def test_with_private_key(self, mock_web3, fake_abi):
        client = APEXClient(mock_web3, FAKE_CONTRACT_ADDRESS, FAKE_PRIVATE_KEY, fake_abi)
        assert client._private_key == FAKE_PRIVATE_KEY
        assert client._account == FAKE_ADDRESS

    def test_without_private_key(self, mock_web3, fake_abi):
        client = APEXClient(mock_web3, FAKE_CONTRACT_ADDRESS, abi=fake_abi)
        assert client._private_key is None
        assert client._account is None

    def test_checksums_address(self, mock_web3, fake_abi):
        lower_addr = FAKE_CONTRACT_ADDRESS.lower()
        client = APEXClient(mock_web3, lower_addr, abi=fake_abi)
        assert client.address == Web3.to_checksum_address(lower_addr)


class TestWriteMethods:
    def test_create_job(self, apex_client):
        # Mock contract function chain
        fn_mock = MagicMock()
        apex_client.contract.functions.createJob.return_value = fn_mock
        fn_mock.build_transaction.return_value = {"nonce": 0}

        # Mock event processing
        log = MagicMock()
        log.__getitem__ = lambda self, key: {"args": {"jobId": 42}}[key] if key == "args" else None
        # Simplify: mock _send_tx directly for most write tests
        apex_client._send_tx = MagicMock(return_value={
            "transactionHash": FAKE_TX_HASH,
            "status": 1,
            "receipt": {"transactionHash": bytes.fromhex(FAKE_TX_HASH[2:]), "status": 1},
        })
        apex_client.contract.events.JobCreated.return_value.process_receipt.return_value = [
            {"args": {"jobId": 42}}
        ]

        result = apex_client.create_job(
            provider=FAKE_ADDRESS,
            evaluator=FAKE_ADDRESS,
            expired_at=9999999999,
            description="test job",
        )
        assert result["jobId"] == 42

    def test_set_budget(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.set_budget(1, 1000)
        apex_client.contract.functions.setBudget.assert_called_once_with(1, 1000, b"")
        assert result["status"] == 1

    def test_fund(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.fund(1, 1000)
        apex_client.contract.functions.fund.assert_called_once_with(1, 1000, b"")
        assert result["status"] == 1

    def test_set_provider(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        apex_client.set_provider(1, FAKE_ADDRESS)
        apex_client.contract.functions.setProvider.assert_called_once()

    def test_submit_valid(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        deliverable = b"\x01" * 32
        result = apex_client.submit(1, deliverable)
        assert result["status"] == 1

    def test_submit_invalid_length(self, apex_client):
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            apex_client.submit(1, b"\x01" * 16)

    def test_complete(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.complete(1)
        apex_client.contract.functions.complete.assert_called_once()
        assert result["status"] == 1

    def test_reject(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.reject(1)
        apex_client.contract.functions.reject.assert_called_once()
        assert result["status"] == 1

    def test_claim_refund(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.claim_refund(1)
        apex_client.contract.functions.claimRefund.assert_called_once_with(1)
        assert result["status"] == 1

    def test_claim_pending(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        result = apex_client.claim_pending()
        apex_client.contract.functions.claimPending.assert_called_once()
        assert result["status"] == 1

    def test_submit_with_opt_params(self, apex_client):
        apex_client._send_tx = MagicMock(return_value={"transactionHash": FAKE_TX_HASH, "status": 1})
        deliverable = b"\x01" * 32
        opt = b"custom_params"
        apex_client.submit(1, deliverable, opt)
        apex_client.contract.functions.submit.assert_called_once_with(1, deliverable, opt)


class TestReadMethods:
    def test_get_job(self, apex_client):
        raw = (
            FAKE_ADDRESS,  # client
            FAKE_ADDRESS,  # provider
            FAKE_ADDRESS,  # evaluator
            "0x" + "00" * 20,  # hook
            1000,  # budget
            9999999999,  # expiredAt
            2,  # status (FUNDED)
            b"\x00" * 32,  # deliverable
            "test description",  # description
        )
        apex_client.contract.functions.getJob.return_value.call.return_value = raw

        job = apex_client.get_job(1)
        assert job["jobId"] == 1
        assert job["client"] == FAKE_ADDRESS
        assert job["budget"] == 1000
        assert job["status"] == APEXStatus.FUNDED
        assert job["description"] == "test description"

    def test_get_job_status(self, apex_client):
        apex_client.contract.functions.getJobStatus.return_value.call.return_value = 3
        status = apex_client.get_job_status(1)
        assert status == APEXStatus.SUBMITTED

    def test_payment_token(self, apex_client):
        apex_client.contract.functions.paymentToken.return_value.call.return_value = FAKE_ADDRESS
        assert apex_client.payment_token() == FAKE_ADDRESS

    def test_min_budget(self, apex_client):
        apex_client.contract.functions.minBudget.return_value.call.return_value = 500
        assert apex_client.min_budget() == 500

    def test_next_job_id(self, apex_client):
        apex_client.contract.functions.nextJobId.return_value.call.return_value = 10
        assert apex_client.next_job_id() == 10

    def test_pending_withdrawals(self, apex_client):
        apex_client.contract.functions.pendingWithdrawals.return_value.call.return_value = 200
        assert apex_client.pending_withdrawals(FAKE_ADDRESS) == 200


class TestEventQueries:
    def test_get_job_funded_events_no_filter(self, apex_client):
        mock_log = MagicMock()
        mock_log.__getitem__ = lambda s, k: {
            "args": {"jobId": 1, "client": FAKE_ADDRESS, "amount": 100},
            "blockNumber": 50,
            "transactionHash": bytes.fromhex(FAKE_TX_HASH[2:]),
        }[k]
        apex_client.contract.events.JobFunded.return_value.get_logs.return_value = [mock_log]

        events = apex_client.get_job_funded_events(from_block=0)
        assert len(events) == 1
        assert events[0]["jobId"] == 1

    def test_get_job_funded_events_with_provider_filter(self, apex_client):
        apex_client.contract.events.JobFunded.return_value.get_logs.return_value = []
        events = apex_client.get_job_funded_events(from_block=0, provider=FAKE_ADDRESS)
        call_kwargs = apex_client.contract.events.JobFunded.return_value.get_logs.call_args
        assert call_kwargs[1].get("argument_filters") is not None

    def test_get_job_created_events(self, apex_client):
        apex_client.contract.events.JobCreated.return_value.get_logs.return_value = []
        events = apex_client.get_job_created_events(from_block=0)
        assert events == []

    def test_get_budget_set_events_no_filter(self, apex_client):
        apex_client.contract.events.BudgetSet.return_value.get_logs.return_value = []
        events = apex_client.get_budget_set_events()
        assert events == []

    def test_get_budget_set_events_with_job_id(self, apex_client):
        apex_client.contract.events.BudgetSet.return_value.get_logs.return_value = []
        apex_client.get_budget_set_events(job_id=5)
        call_kwargs = apex_client.contract.events.BudgetSet.return_value.get_logs.call_args
        filters = call_kwargs[1].get("argument_filters")
        assert filters is not None
        assert filters["jobId"] == 5

    def test_get_budget_set_events_returns_data(self, apex_client):
        mock_log = MagicMock()
        mock_log.__getitem__ = lambda s, k: {
            "args": {"jobId": 5, "amount": 999},
            "blockNumber": 60,
            "transactionHash": bytes.fromhex(FAKE_TX_HASH[2:]),
        }[k]
        apex_client.contract.events.BudgetSet.return_value.get_logs.return_value = [mock_log]

        events = apex_client.get_budget_set_events(job_id=5)
        assert len(events) == 1
        assert events[0]["amount"] == 999


class TestSendTx:
    def test_requires_private_key(self, mock_web3, fake_abi):
        client = APEXClient(mock_web3, FAKE_CONTRACT_ADDRESS, abi=fake_abi)
        fn = MagicMock()
        with pytest.raises(RuntimeError, match="private_key required"):
            client._send_tx(fn)

    def test_success_path(self, apex_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        result = apex_client._send_tx(fn)
        assert result["status"] == 1
        assert "transactionHash" in result

    @patch("bnbagent.apex_client.time.sleep")
    def test_nonce_retry(self, mock_sleep, apex_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        apex_client.w3.eth.send_raw_transaction.side_effect = [
            Exception("nonce too low"),
            bytes.fromhex(FAKE_TX_HASH[2:]),
        ]
        result = apex_client._send_tx(fn)
        assert result["status"] == 1

    @patch("bnbagent.apex_client.time.sleep")
    def test_rate_limit_retry(self, mock_sleep, apex_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        apex_client.w3.eth.send_raw_transaction.side_effect = [
            Exception("429 too many requests"),
            bytes.fromhex(FAKE_TX_HASH[2:]),
        ]
        result = apex_client._send_tx(fn)
        assert result["status"] == 1
        mock_sleep.assert_called_once()

    @patch("bnbagent.apex_client.time.sleep")
    def test_exhausts_retries(self, mock_sleep, apex_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        apex_client.w3.eth.send_raw_transaction.side_effect = Exception("nonce too low")
        with pytest.raises(Exception, match="nonce too low"):
            apex_client._send_tx(fn)

    def test_non_retryable_raises(self, apex_client):
        fn = MagicMock()
        fn.build_transaction.return_value = {"nonce": 0}
        apex_client.w3.eth.send_raw_transaction.side_effect = Exception("execution reverted")
        with pytest.raises(Exception, match="execution reverted"):
            apex_client._send_tx(fn)


class TestCallWithRetry:
    def test_success(self, apex_client):
        fn = MagicMock()
        fn.call.return_value = 42
        result = apex_client._call_with_retry(fn)
        assert result == 42

    @patch("bnbagent.apex_client.time.sleep")
    def test_rate_limit_retry(self, mock_sleep, apex_client):
        fn = MagicMock()
        fn.call.side_effect = [Exception("429"), 99]
        result = apex_client._call_with_retry(fn)
        assert result == 99

    def test_non_retryable_raises(self, apex_client):
        fn = MagicMock()
        fn.call.side_effect = Exception("some other error")
        with pytest.raises(Exception, match="some other error"):
            apex_client._call_with_retry(fn)
