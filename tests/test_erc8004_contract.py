"""Tests for ContractInterface._execute_transaction() revert detection and pre-flight."""

from __future__ import annotations

import concurrent.futures
from unittest.mock import MagicMock, Mock, patch

import pytest

from bnbagent.erc8004.contract import ContractInterface
from tests.conftest import FAKE_ADDRESS, FAKE_CONTRACT_ADDRESS


def _make_contract(web3=None, paymaster=None):
    """Build a ContractInterface with all heavy dependencies mocked."""
    if web3 is None:
        web3 = MagicMock()
    # NonceManager keys singletons by (rpc_url, account) — give the mock a
    # stable endpoint_uri so it doesn't fall back to id(provider).
    web3.provider.endpoint_uri = "https://fake-rpc.example.com"
    wallet_provider = MagicMock()
    wallet_provider.address = FAKE_ADDRESS

    with patch.object(ContractInterface, "_get_default_abi", return_value=[]):
        with patch("bnbagent.erc8004.contract.Web3.to_checksum_address", side_effect=lambda x: x):
            ci = ContractInterface(
                web3=web3,
                contract_address=FAKE_CONTRACT_ADDRESS,
                wallet_provider=wallet_provider,
                paymaster=paymaster,
            )
    return ci, web3, wallet_provider


def _make_function_mock(web3, gas_limit=100_000):
    """Build a contract function mock that produces a signed tx and hash."""
    fn = MagicMock()
    fn.estimate_gas.return_value = gas_limit
    fn.build_transaction.return_value = {
        "from": "0xDeadBeef",
        "to": "0x1234",
        "data": "0xdeadbeef",
        "value": 0,
        "gas": gas_limit,
        "gasPrice": 3_000_000_000,
        "nonce": 1,
        "chainId": 97,
    }
    signed = MagicMock()
    signed.__getitem__ = lambda self, key: b"\x00" * 32 if key == "rawTransaction" else None
    web3.eth.get_transaction_count.return_value = 1
    web3.eth.gas_price = 3_000_000_000
    web3.eth.chain_id = 97
    raw_tx = b"\x00" * 32
    signed_mock = MagicMock()
    signed_mock.__getitem__ = MagicMock(return_value=raw_tx)
    return fn, signed_mock


class TestExecuteTransactionReceiptRevert:
    def test_raises_on_receipt_status_zero(self, caplog):
        """receipt.status == 0 must raise RuntimeError and log an error."""
        web3 = MagicMock()
        ci, web3, wallet_provider = _make_contract(web3=web3)

        fn = MagicMock()
        fn.estimate_gas.return_value = 100_000
        fn.build_transaction.return_value = {
            "from": "0xDeadBeef", "to": "0x1234",
            "data": "0x", "value": 0, "gas": 100_000,
            "gasPrice": 3_000_000_000, "nonce": 1, "chainId": 97,
        }
        web3.eth.get_transaction_count.return_value = 1
        web3.eth.gas_price = 3_000_000_000
        web3.eth.chain_id = 97

        raw_bytes = b"\xab" * 32
        signed = MagicMock()
        signed.__getitem__ = lambda s, k: raw_bytes if k == "rawTransaction" else None
        wallet_provider.sign_transaction.return_value = signed

        sent_hash = b"\xab" * 32
        web3.eth.send_raw_transaction.return_value = sent_hash
        web3.eth.call.return_value = b""  # pre-flight passes

        revert_receipt = Mock()
        revert_receipt.__getitem__ = lambda s, k: {
            "status": 0,
            "blockNumber": 999,
            "gasUsed": 21000,
            "transactionHash": sent_hash,
        }[k]
        web3.eth.wait_for_transaction_receipt.return_value = revert_receipt

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError, match="Transaction reverted on-chain"):
                ci._execute_transaction(fn, description="test-op")

        assert "[ContractInterface]" in caplog.text
        assert "test-op" in caplog.text

    def test_success_receipt_returns_normally(self):
        """receipt.status == 1 must return dict without raising."""
        web3 = MagicMock()
        ci, web3, wallet_provider = _make_contract(web3=web3)

        fn = MagicMock()
        fn.estimate_gas.return_value = 100_000
        fn.build_transaction.return_value = {
            "from": "0xDeadBeef", "to": "0x1234",
            "data": "0x", "value": 0, "gas": 100_000,
            "gasPrice": 3_000_000_000, "nonce": 1, "chainId": 97,
        }
        web3.eth.get_transaction_count.return_value = 1
        web3.eth.gas_price = 3_000_000_000
        web3.eth.chain_id = 97

        raw_bytes = b"\xab" * 32
        signed = MagicMock()
        signed.__getitem__ = lambda s, k: raw_bytes if k == "rawTransaction" else None
        wallet_provider.sign_transaction.return_value = signed

        sent_hash = b"\xab" * 32
        web3.eth.send_raw_transaction.return_value = sent_hash
        web3.eth.call.return_value = b""

        ok_receipt = Mock()
        ok_receipt.__getitem__ = lambda s, k: {
            "status": 1,
            "blockNumber": 1000,
            "gasUsed": 50000,
            "transactionHash": sent_hash,
        }[k]
        web3.eth.wait_for_transaction_receipt.return_value = ok_receipt

        result = ci._execute_transaction(fn, description="test-op")
        assert "transactionHash" in result
        assert "receipt" in result


class TestPreflightEthCall:
    def _setup(self, web3=None):
        if web3 is None:
            web3 = MagicMock()
        ci, web3, wallet_provider = _make_contract(web3=web3)
        fn = MagicMock()
        fn.estimate_gas.return_value = 100_000
        fn.build_transaction.return_value = {
            "from": "0xDeadBeef", "to": "0x1234",
            "data": "0x", "value": 0, "gas": 100_000,
            "gasPrice": 3_000_000_000, "nonce": 1, "chainId": 97,
        }
        web3.eth.get_transaction_count.return_value = 1
        web3.eth.gas_price = 3_000_000_000
        web3.eth.chain_id = 97

        raw_bytes = b"\xab" * 32
        signed = MagicMock()
        signed.__getitem__ = lambda s, k: raw_bytes if k == "rawTransaction" else None
        wallet_provider.sign_transaction.return_value = signed

        sent_hash = b"\xab" * 32
        web3.eth.send_raw_transaction.return_value = sent_hash

        ok_receipt = Mock()
        ok_receipt.__getitem__ = lambda s, k: {
            "status": 1, "blockNumber": 1, "gasUsed": 1,
            "transactionHash": sent_hash,
        }[k]
        web3.eth.wait_for_transaction_receipt.return_value = ok_receipt
        return ci, web3, wallet_provider, fn, sent_hash

    def test_raises_on_preflight_revert(self):
        """eth_call revert with revert data must raise before send_raw_transaction."""
        ci, web3, wallet_provider, fn, _ = self._setup()
        web3.eth.call.side_effect = Exception("execution reverted: Unauthorized")

        with pytest.raises(RuntimeError, match="Transaction would revert"):
            ci._execute_transaction(fn, description="pre-flight-test")

        web3.eth.send_raw_transaction.assert_not_called()

    def test_proceeds_on_opaque_0x_preflight(self):
        """eth_call returning opaque '0x' must log warning and continue to send."""
        ci, web3, wallet_provider, fn, _ = self._setup()
        web3.eth.call.side_effect = Exception("('0x', ...)")

        result = ci._execute_transaction(fn, description="opaque-test")
        web3.eth.send_raw_transaction.assert_called_once()
        assert "transactionHash" in result

    def test_proceeds_on_preflight_timeout(self):
        """eth_call timeout must log warning and continue to send."""
        ci, web3, wallet_provider, fn, _ = self._setup()

        def slow_call(params):
            raise concurrent.futures.TimeoutError()

        web3.eth.call.side_effect = slow_call

        # Patch ThreadPoolExecutor so TimeoutError propagates correctly
        original_executor = concurrent.futures.ThreadPoolExecutor

        class ImmediateTimeoutExecutor:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def submit(self, fn, *args, **kwargs):
                f = concurrent.futures.Future()
                f.set_exception(concurrent.futures.TimeoutError())
                return f

        with patch("bnbagent.erc8004.contract._cf.ThreadPoolExecutor", ImmediateTimeoutExecutor):
            result = ci._execute_transaction(fn, description="timeout-test")

        web3.eth.send_raw_transaction.assert_called_once()
        assert "transactionHash" in result


class TestRegisterAgentPropagatesRevert:
    def test_register_agent_raises_on_revert(self):
        """register_agent must raise RuntimeError (not return success=True) on revert."""
        web3 = MagicMock()
        ci, web3, wallet_provider = _make_contract(web3=web3)

        # Mock the contract and its functions
        register_fn = MagicMock()
        register_fn.estimate_gas.return_value = 100_000
        register_fn.build_transaction.return_value = {
            "from": "0xDeadBeef", "to": "0x1234",
            "data": "0x", "value": 0, "gas": 100_000,
            "gasPrice": 3_000_000_000, "nonce": 1, "chainId": 97,
        }
        ci.contract = MagicMock()
        ci.contract.functions.register.return_value = register_fn

        web3.eth.get_transaction_count.return_value = 1
        web3.eth.gas_price = 3_000_000_000
        web3.eth.chain_id = 97
        web3.eth.call.return_value = b""

        raw_bytes = b"\xab" * 32
        signed = MagicMock()
        signed.__getitem__ = lambda s, k: raw_bytes if k == "rawTransaction" else None
        wallet_provider.sign_transaction.return_value = signed

        sent_hash = b"\xab" * 32
        web3.eth.send_raw_transaction.return_value = sent_hash

        revert_receipt = Mock()
        revert_receipt.__getitem__ = lambda s, k: {
            "status": 0, "blockNumber": 999, "gasUsed": 21000,
            "transactionHash": sent_hash,
        }[k]
        web3.eth.wait_for_transaction_receipt.return_value = revert_receipt

        with pytest.raises(RuntimeError, match="Agent registration failed"):
            ci.register_agent(agent_uri="https://example.com/agent")


class TestRetryAndNonceManagement:
    """Web3 (non-paymaster) path must retry on nonce errors and 429s."""

    def _setup_for_retry(self, web3=None):
        if web3 is None:
            web3 = MagicMock()
        ci, web3, wallet_provider = _make_contract(web3=web3)
        fn = MagicMock()
        fn.estimate_gas.return_value = 100_000
        fn.build_transaction.return_value = {
            "from": FAKE_ADDRESS, "to": FAKE_CONTRACT_ADDRESS,
            "data": "0x", "value": 0, "gas": 100_000,
            "gasPrice": 3_000_000_000, "nonce": 1, "chainId": 97,
        }
        web3.eth.get_transaction_count.return_value = 1
        web3.eth.gas_price = 3_000_000_000
        web3.eth.chain_id = 97
        web3.eth.call.return_value = b""

        raw_bytes = b"\xab" * 32
        signed = MagicMock()
        signed.__getitem__ = lambda s, k: raw_bytes if k == "rawTransaction" else None
        wallet_provider.sign_transaction.return_value = signed

        sent_hash = b"\xab" * 32
        ok_receipt = Mock()
        ok_receipt.__getitem__ = lambda s, k: {
            "status": 1, "blockNumber": 1, "gasUsed": 1,
            "transactionHash": sent_hash,
        }[k]
        web3.eth.wait_for_transaction_receipt.return_value = ok_receipt
        return ci, web3, wallet_provider, fn, sent_hash

    def test_retries_on_nonce_too_low(self):
        """Nonce-too-low error must trigger NonceManager re-sync and retry."""
        ci, web3, wallet_provider, fn, sent_hash = self._setup_for_retry()
        # First send fails with nonce error, second succeeds.
        web3.eth.send_raw_transaction.side_effect = [
            Exception("nonce too low"),
            sent_hash,
        ]
        result = ci._execute_transaction(fn, description="retry-nonce")
        assert web3.eth.send_raw_transaction.call_count == 2
        assert "transactionHash" in result
        # NonceManager re-syncs by calling get_transaction_count again.
        assert web3.eth.get_transaction_count.call_count >= 2

    def test_retries_on_rate_limit(self):
        """429 must trigger exponential backoff and retry."""
        ci, web3, wallet_provider, fn, sent_hash = self._setup_for_retry()
        web3.eth.send_raw_transaction.side_effect = [
            Exception("HTTP 429: too many requests"),
            sent_hash,
        ]
        with patch("bnbagent.erc8004.contract.time.sleep") as mock_sleep:
            result = ci._execute_transaction(fn, description="retry-429")
        assert web3.eth.send_raw_transaction.call_count == 2
        mock_sleep.assert_called_once()  # one backoff between the two attempts
        assert "transactionHash" in result

    def test_no_retry_on_unrelated_error(self):
        """Generic non-retryable error must raise immediately and reset nonce cache."""
        ci, web3, wallet_provider, fn, _ = self._setup_for_retry()
        web3.eth.send_raw_transaction.side_effect = Exception("insufficient funds")
        with pytest.raises(Exception, match="insufficient funds"):
            ci._execute_transaction(fn, description="no-retry")
        assert web3.eth.send_raw_transaction.call_count == 1

    def test_uses_gas_price_buffer(self):
        """Gas price must be 1.2x network value, floored at MIN_GAS_PRICE_WEI."""
        ci, web3, wallet_provider, fn, sent_hash = self._setup_for_retry()
        # Network reports 5 Gwei; expect tx to use ceil(5 * 1.2) = 6 Gwei.
        web3.eth.gas_price = 5_000_000_000
        web3.eth.send_raw_transaction.return_value = sent_hash
        ci._execute_transaction(fn, description="gas-buffer")
        build_kwargs = fn.build_transaction.call_args[0][0]
        assert build_kwargs["gasPrice"] == 6_000_000_000

    def test_send_raw_transaction_receives_bytes(self):
        """send_raw_transaction must be called with bytes, not a hex string."""
        ci, web3, wallet_provider, fn, sent_hash = self._setup_for_retry()
        web3.eth.send_raw_transaction.return_value = sent_hash
        ci._execute_transaction(fn, description="bytes-arg")
        sent_arg = web3.eth.send_raw_transaction.call_args[0][0]
        assert isinstance(sent_arg, bytes)


class TestInjectBuildWith:
    """_inject_built_with must auto-tag every registration with the SDK identifier."""

    def _make_ci(self):
        ci, _, _ = _make_contract()
        return ci

    def test_default_injection(self):
        """No metadata → built_with entry is appended automatically."""
        ci = self._make_ci()
        result = ci._inject_built_with(None)
        keys = [e["key"] for e in result]
        assert "built_with" in keys
        bw = next(e for e in result if e["key"] == "built_with")
        assert bw["value"].startswith("https://github.com/bnb-chain/bnbagent-sdk#v")

    def test_skip_if_user_set(self):
        """User-supplied built_with must be respected; SDK must not overwrite it."""
        ci = self._make_ci()
        user_meta = [{"key": "built_with", "value": "https://my-fork.com"}]
        result = ci._inject_built_with(user_meta)
        bw_entries = [e for e in result if e["key"] == "built_with"]
        assert len(bw_entries) == 1
        assert bw_entries[0]["value"] == "https://my-fork.com"

    def test_coexists_with_other_metadata(self):
        """Other user metadata must be preserved alongside the injected built_with."""
        ci = self._make_ci()
        user_meta = [{"key": "foo", "value": "bar"}]
        result = ci._inject_built_with(user_meta)
        assert any(e["key"] == "foo" and e["value"] == "bar" for e in result)
        assert any(e["key"] == "built_with" for e in result)
        assert len(result) == 2
