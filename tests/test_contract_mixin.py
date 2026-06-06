"""Tests for ContractClientMixin._send_tx gas-limit estimation.

A hardcoded 2M gas limit makes nodes demand ``balance >= 2M * gasPrice``
(~0.007 BNB) upfront while typical writes burn 50-150k gas. ``gas=None``
now estimates on-chain with a 20% buffer (mirroring erc8004) and only
falls back to ``DEFAULT_GAS_FALLBACK`` when estimation is unavailable.
"""

from unittest.mock import MagicMock

import pytest
from web3.exceptions import ContractLogicError

from bnbagent.core.contract_mixin import DEFAULT_GAS_FALLBACK, ContractClientMixin
from bnbagent.core.nonce_manager import NonceManager
from tests.conftest import FAKE_ADDRESS


class _FakeClient(ContractClientMixin):
    def __init__(self, w3, wallet_provider):
        self.w3 = w3
        self._wallet_provider = wallet_provider
        self._account = FAKE_ADDRESS


@pytest.fixture(autouse=True)
def _clear_nonce_singletons():
    NonceManager._clear_all()
    yield
    NonceManager._clear_all()


@pytest.fixture
def client(mock_web3):
    wallet = MagicMock()
    wallet.address = FAKE_ADDRESS
    wallet.sign_transaction.return_value = {"rawTransaction": b"\x00" * 32}
    return _FakeClient(mock_web3, wallet)


def _make_fn(estimate=100_000):
    fn = MagicMock()
    fn.estimate_gas.return_value = estimate
    fn.build_transaction.side_effect = lambda params: {
        **params,
        "to": "0x" + "11" * 20,
        "data": "0x",
    }
    return fn


def _built_gas(fn) -> int:
    return fn.build_transaction.call_args[0][0]["gas"]


class TestGasEstimation:
    def test_estimates_with_20pct_buffer(self, client):
        fn = _make_fn(estimate=100_000)
        result = client._send_tx(fn)
        assert result["status"] == 1
        fn.estimate_gas.assert_called_once_with({"from": FAKE_ADDRESS, "value": 0})
        assert _built_gas(fn) == 120_000

    def test_estimate_includes_value(self, client):
        fn = _make_fn()
        client._send_tx(fn, value=123)
        fn.estimate_gas.assert_called_once_with({"from": FAKE_ADDRESS, "value": 123})

    def test_explicit_gas_skips_estimation(self, client):
        fn = _make_fn()
        client._send_tx(fn, gas=500_000)
        fn.estimate_gas.assert_not_called()
        assert _built_gas(fn) == 500_000

    def test_transport_error_falls_back_to_default(self, client):
        fn = _make_fn()
        fn.estimate_gas.side_effect = ConnectionError("rpc down")
        result = client._send_tx(fn)
        assert result["status"] == 1
        assert _built_gas(fn) == DEFAULT_GAS_FALLBACK

    def test_genuine_revert_propagates(self, client):
        fn = _make_fn()
        fn.estimate_gas.side_effect = ContractLogicError(
            "execution reverted: NotProvider"
        )
        with pytest.raises(RuntimeError, match="Transaction would revert"):
            client._send_tx(fn)
        fn.build_transaction.assert_not_called()

    def test_opaque_revert_falls_back_to_default(self, client):
        # Some nodes return no revert data; str() of the error ends with
        # ", '0x')" — the same escape hatch the pre-flight uses.
        fn = _make_fn()
        fn.estimate_gas.side_effect = ContractLogicError(
            ("execution reverted", "0x")
        )
        result = client._send_tx(fn)
        assert result["status"] == 1
        assert _built_gas(fn) == DEFAULT_GAS_FALLBACK

    def test_skip_preflight_skips_estimation(self, client):
        fn = _make_fn()
        client._send_tx(fn, skip_preflight=True)
        fn.estimate_gas.assert_not_called()
        assert _built_gas(fn) == DEFAULT_GAS_FALLBACK
