"""Public tx-tuning knobs: per-chain gas-price floor + configurable receipt timeout.

Covers BUG-12/13/14 — the gas floor must be per-chain (testnet needs ~1 Gwei,
mainnet 0.1 Gwei) and the receipt timeout must be tunable via env var or the
public setter, with precedence ``setter > env > default`` for both knobs.
"""

from unittest.mock import MagicMock

import pytest

from bnbagent.core.contract_mixin import (
    DEFAULT_RECEIPT_TIMEOUT,
    MIN_GAS_PRICE_WEI,
    get_default_receipt_timeout,
    min_gas_price_wei,
    set_default_receipt_timeout,
    set_min_gas_price_wei,
)
from bnbagent.networks.addresses import BSC_MAINNET_CHAIN_ID, BSC_TESTNET_CHAIN_ID
from bnbagent.wallets.local_executor import LocalExecutor

_ONE_GWEI = 1_000_000_000
_TENTH_GWEI = 100_000_000


def _le_pieces(*, gas_price=3_000_000_000, chain_id=BSC_TESTNET_CHAIN_ID, capture=None):
    """A function mock + web3 mock + wallet mock wired for one self-pay write.

    When ``capture`` (a dict) is given, ``build_transaction`` records the params
    it was called with so a test can assert the resolved gasPrice.
    """
    web3 = MagicMock()
    web3.eth.gas_price = gas_price
    web3.eth.chain_id = chain_id
    web3.eth.call.return_value = b""  # pre-flight passes
    web3.eth.send_raw_transaction.return_value = b"\xab" * 32
    web3.eth.wait_for_transaction_receipt.return_value = {
        "status": 1,
        "blockNumber": 1,
        "gasUsed": 50_000,
        "transactionHash": b"\xab" * 32,
    }

    fn = MagicMock()
    fn.estimate_gas.return_value = 100_000
    if capture is None:
        fn.build_transaction.return_value = {
            "from": "0xabc",
            "to": "0x1234",
            "data": "0x",
            "value": 0,
            "gas": 100_000,
            "gasPrice": gas_price,
            "nonce": 1,
            "chainId": chain_id,
        }
    else:

        def _cap(params):
            capture.update(params)
            return {**params, "to": "0x1234", "data": "0x"}

        fn.build_transaction.side_effect = _cap

    wallet = MagicMock()
    wallet.address = "0x" + "11" * 20
    signed = MagicMock()
    signed.__getitem__ = lambda _s, k: b"\x00" * 32 if k == "rawTransaction" else None
    wallet.sign_transaction.return_value = signed
    return fn, web3, wallet


class TestMinGasPriceFloor:
    def test_mainnet_floor_is_tenth_gwei(self):
        assert min_gas_price_wei(BSC_MAINNET_CHAIN_ID) == _TENTH_GWEI

    def test_testnet_floor_is_one_gwei(self):
        assert min_gas_price_wei(BSC_TESTNET_CHAIN_ID) == _ONE_GWEI

    def test_unknown_chain_falls_back_to_default(self):
        assert min_gas_price_wei(12345) == MIN_GAS_PRICE_WEI == _TENTH_GWEI

    def test_env_overrides_per_chain_default(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_MIN_GAS_PRICE_WEI", "5000000000")
        # env applies globally, to every chain
        assert min_gas_price_wei(BSC_TESTNET_CHAIN_ID) == 5_000_000_000
        assert min_gas_price_wei(BSC_MAINNET_CHAIN_ID) == 5_000_000_000

    def test_invalid_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_MIN_GAS_PRICE_WEI", "not-a-number")
        assert min_gas_price_wei(BSC_TESTNET_CHAIN_ID) == _ONE_GWEI

    def test_setter_overrides_env_and_default(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_MIN_GAS_PRICE_WEI", "5000000000")
        set_min_gas_price_wei(7_000_000_000)
        assert min_gas_price_wei(BSC_TESTNET_CHAIN_ID) == 7_000_000_000
        assert min_gas_price_wei(BSC_MAINNET_CHAIN_ID) == 7_000_000_000

    def test_setter_rejects_non_positive(self):
        with pytest.raises(ValueError):
            set_min_gas_price_wei(0)


class TestReceiptTimeout:
    def test_default_is_300(self):
        assert get_default_receipt_timeout() == DEFAULT_RECEIPT_TIMEOUT == 300

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_RECEIPT_TIMEOUT", "600")
        assert get_default_receipt_timeout() == 600

    def test_invalid_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_RECEIPT_TIMEOUT", "soon")
        assert get_default_receipt_timeout() == 300

    def test_setter_overrides_env(self, monkeypatch):
        monkeypatch.setenv("BNBAGENT_RECEIPT_TIMEOUT", "600")
        set_default_receipt_timeout(900)
        assert get_default_receipt_timeout() == 900

    def test_setter_rejects_non_positive(self):
        with pytest.raises(ValueError):
            set_default_receipt_timeout(0)


class TestLocalExecutorTimeoutResolution:
    """``LocalExecutor(receipt_timeout=None)`` resolves the SDK default lazily,
    so a runtime ``set_default_receipt_timeout()`` is honored even after the
    executor was constructed (mirrors the cached intent-executor case)."""

    def test_none_resolves_to_global_default(self):
        fn, web3, wallet = _le_pieces()
        ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=None)
        ex._execute_function(fn, description="x")
        _, kwargs = web3.eth.wait_for_transaction_receipt.call_args
        assert kwargs["timeout"] == 300

    def test_runtime_setter_honored_after_construction(self):
        fn, web3, wallet = _le_pieces()
        ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=None)
        set_default_receipt_timeout(777)
        ex._execute_function(fn, description="x")
        _, kwargs = web3.eth.wait_for_transaction_receipt.call_args
        assert kwargs["timeout"] == 777

    def test_explicit_timeout_wins_over_global(self):
        fn, web3, wallet = _le_pieces()
        ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=None, receipt_timeout=42)
        set_default_receipt_timeout(777)
        ex._execute_function(fn, description="x")
        _, kwargs = web3.eth.wait_for_transaction_receipt.call_args
        assert kwargs["timeout"] == 42


class TestLocalExecutorGasFloor:
    """The self-pay path (the buyer ``fund`` deposit) floors at the per-chain min."""

    def test_self_pay_floors_at_one_gwei_on_testnet(self):
        captured: dict = {}
        fn, web3, wallet = _le_pieces(gas_price=100, capture=captured)
        ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=None)
        ex._execute_function(fn, description="fund")
        assert captured["gasPrice"] == _ONE_GWEI
