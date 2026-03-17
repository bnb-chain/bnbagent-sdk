"""Tests for NonceManager — singleton, thread-safe nonce management."""

import threading
from unittest.mock import MagicMock

import pytest

from bnbagent.nonce_manager import NonceManager
from tests.conftest import FAKE_ADDRESS


def _make_w3(rpc_url="https://fake-rpc.example.com", nonce=0):
    w3 = MagicMock()
    w3.provider.endpoint_uri = rpc_url
    w3.eth.get_transaction_count.return_value = nonce
    return w3


class TestSingleton:
    def test_for_account_creates_instance(self):
        w3 = _make_w3()
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        assert isinstance(mgr, NonceManager)

    def test_for_account_caches_same_key(self):
        w3 = _make_w3()
        mgr1 = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr2 = NonceManager.for_account(w3, FAKE_ADDRESS)
        assert mgr1 is mgr2

    def test_for_account_different_rpc_creates_separate(self):
        w3a = _make_w3("https://rpc-a.example.com")
        w3b = _make_w3("https://rpc-b.example.com")
        mgr_a = NonceManager.for_account(w3a, FAKE_ADDRESS)
        mgr_b = NonceManager.for_account(w3b, FAKE_ADDRESS)
        assert mgr_a is not mgr_b

    def test_for_account_different_account_creates_separate(self):
        w3 = _make_w3()
        addr2 = "0x" + "11" * 20
        mgr1 = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr2 = NonceManager.for_account(w3, addr2)
        assert mgr1 is not mgr2

    def test_for_account_checksums_address(self):
        w3 = _make_w3()
        lower = FAKE_ADDRESS.lower()
        mgr1 = NonceManager.for_account(w3, lower)
        mgr2 = NonceManager.for_account(w3, FAKE_ADDRESS)
        assert mgr1 is mgr2

    def test_clear_all(self):
        w3 = _make_w3()
        NonceManager.for_account(w3, FAKE_ADDRESS)
        assert len(NonceManager._instances) > 0
        NonceManager._clear_all()
        assert len(NonceManager._instances) == 0


class TestGetNonce:
    def test_seeds_from_chain_on_first_call(self):
        w3 = _make_w3(nonce=5)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        nonce = mgr.get_nonce()
        assert nonce == 5
        w3.eth.get_transaction_count.assert_called_once()

    def test_increments_locally_after_seed(self):
        w3 = _make_w3(nonce=10)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        n1 = mgr.get_nonce()
        n2 = mgr.get_nonce()
        n3 = mgr.get_nonce()
        assert n1 == 10
        assert n2 == 11
        assert n3 == 12
        # Only one RPC call for seeding
        assert w3.eth.get_transaction_count.call_count == 1

    def test_no_rpc_on_subsequent_calls(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        for _ in range(10):
            mgr.get_nonce()
        assert w3.eth.get_transaction_count.call_count == 1

    def test_thread_safety_unique_nonces(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        results = []
        lock = threading.Lock()

        def grab_nonce():
            n = mgr.get_nonce()
            with lock:
                results.append(n)

        threads = [threading.Thread(target=grab_nonce) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        assert len(set(results)) == 50  # all unique

    def test_pending_count_mode(self):
        from web3 import Web3
        w3 = _make_w3(nonce=42)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        checksummed = Web3.to_checksum_address(FAKE_ADDRESS)
        w3.eth.get_transaction_count.assert_called_with(
            checksummed, "pending"
        )


class TestHandleError:
    def test_nonce_too_low_resyncs(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        w3.eth.get_transaction_count.return_value = 5
        result = mgr.handle_error(Exception("nonce too low"), 0)
        assert result is True
        assert mgr.get_nonce() == 5

    def test_already_known_resyncs(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        w3.eth.get_transaction_count.return_value = 3
        result = mgr.handle_error(Exception("already known"), 0)
        assert result is True

    def test_replacement_underpriced_resyncs(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        w3.eth.get_transaction_count.return_value = 3
        result = mgr.handle_error(
            Exception("replacement transaction underpriced"), 0
        )
        assert result is True

    def test_unrelated_error_returns_false(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        result = mgr.handle_error(Exception("out of gas"), 0)
        assert result is False

    def test_resync_updates_nonce(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        _ = mgr.get_nonce()  # 0
        _ = mgr.get_nonce()  # 1
        w3.eth.get_transaction_count.return_value = 10
        mgr.handle_error(Exception("nonce too low"), 1)
        assert mgr.get_nonce() == 10


class TestReset:
    def test_reset_clears_nonce(self):
        w3 = _make_w3(nonce=5)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        mgr.reset()
        w3.eth.get_transaction_count.return_value = 20
        assert mgr.get_nonce() == 20

    def test_reset_causes_reseed(self):
        w3 = _make_w3(nonce=0)
        mgr = NonceManager.for_account(w3, FAKE_ADDRESS)
        mgr.get_nonce()
        assert w3.eth.get_transaction_count.call_count == 1
        mgr.reset()
        mgr.get_nonce()
        assert w3.eth.get_transaction_count.call_count == 2
