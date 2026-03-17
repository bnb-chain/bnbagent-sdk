"""Tests for Paymaster — ERC-4337 paymaster client."""

from unittest.mock import MagicMock, patch

import pytest

from bnbagent.paymaster import Paymaster, _to_hex, _to_address_hex


class TestHelpers:
    def test_to_hex_int(self):
        result = _to_hex(255)
        assert result.startswith("0x")
        assert int(result, 16) == 255

    def test_to_hex_bytes(self):
        result = _to_hex(b"\xab\xcd")
        assert result == "0xabcd"

    def test_to_hex_str_with_prefix(self):
        assert _to_hex("0xdeadbeef") == "0xdeadbeef"

    def test_to_hex_str_without_prefix(self):
        assert _to_hex("abcd") == "0xabcd"

    def test_to_hex_none(self):
        assert _to_hex(None) == "0x0"

    def test_to_hex_empty_bytes(self):
        assert _to_hex(b"") == "0x0"

    def test_to_hex_empty_string(self):
        assert _to_hex("") == "0x0"

    def test_to_address_hex_valid(self):
        addr = "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"
        result = _to_address_hex(addr)
        assert result.startswith("0x")
        assert len(result) == 42

    def test_to_address_hex_invalid(self):
        result = _to_address_hex("not-an-address")
        assert result == "0x0"

    def test_to_address_hex_none(self):
        assert _to_address_hex(None) == "0x0"


class TestPaymaster:
    def _make_paymaster(self):
        return Paymaster(paymaster_url="https://paymaster.example.com")

    @patch("bnbagent.paymaster.requests.post")
    def test_get_transaction_count_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0xa"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        count = pm.eth_getTransactionCount("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")
        assert count == 10

    @patch("bnbagent.paymaster.requests.post")
    def test_get_transaction_count_missing_result(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        with pytest.raises(ValueError, match="missing 'result'"):
            pm.eth_getTransactionCount("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")

    @patch("bnbagent.paymaster.requests.post")
    def test_send_raw_transaction_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0x" + "ab" * 32}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        tx_hash = pm.eth_sendRawTransaction("0xsignedtx")
        assert tx_hash.startswith("0x")

    @patch("bnbagent.paymaster.requests.post")
    def test_send_raw_transaction_missing_result(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        with pytest.raises(ValueError, match="missing 'result'"):
            pm.eth_sendRawTransaction("0xsignedtx")

    @patch("bnbagent.paymaster.requests.post")
    def test_send_raw_transaction_adds_prefix(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": "0xhash"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        pm.eth_sendRawTransaction("signedtx_no_prefix")
        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["params"][0].startswith("0x")

    @patch("bnbagent.paymaster.requests.post")
    def test_is_sponsorable_true(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"sponsorable": True}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        tx = {"to": "0x" + "ab" * 20, "from": "0x" + "cd" * 20, "value": 0, "data": b"", "gas": 21000}
        assert pm.isSponsorable(tx) is True

    @patch("bnbagent.paymaster.requests.post")
    def test_is_sponsorable_false(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"result": {"sponsorable": False}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        assert pm.isSponsorable({"to": "0x" + "ab" * 20, "from": "0x" + "cd" * 20}) is False

    @patch("bnbagent.paymaster.requests.post")
    def test_is_sponsorable_missing_result(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        assert pm.isSponsorable({"to": "0x" + "ab" * 20}) is False

    @patch("bnbagent.paymaster.requests.post")
    def test_rpc_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"message": "bad request", "code": -32600}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        pm = self._make_paymaster()
        with pytest.raises(RuntimeError, match="RPC error"):
            pm.eth_getTransactionCount("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")

    @patch("bnbagent.paymaster.requests.post")
    def test_connection_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        pm = self._make_paymaster()
        with pytest.raises(requests.exceptions.ConnectionError):
            pm.eth_getTransactionCount("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")
