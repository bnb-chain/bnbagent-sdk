"""LocalExecutor paymaster path: sponsor-if-sponsorable, else self-pay.

The executor must resolve gas sponsorship at runtime via MegaFuel's
``isSponsorable`` and degrade gracefully to self-pay — never hard-fail when a
write is not sponsorable (e.g. ERC-8183 mainnet) or the paymaster is
unreachable. A genuine pre-flight revert still propagates.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bnbagent.wallets.local_executor import LocalExecutor


def _ok_receipt(tx_hash: bytes) -> dict:
    return {"status": 1, "blockNumber": 1, "gasUsed": 50_000, "transactionHash": tx_hash}


def _make_pieces(*, gas_limit: int = 100_000):
    """A function mock + web3 mock + wallet mock wired for one write."""
    web3 = MagicMock()
    web3.eth.gas_price = 3_000_000_000
    web3.eth.chain_id = 97
    web3.eth.call.return_value = b""  # pre-flight passes
    web3.eth.send_raw_transaction.return_value = b"\xab" * 32
    web3.eth.wait_for_transaction_receipt.return_value = _ok_receipt(b"\xab" * 32)

    fn = MagicMock()
    fn.estimate_gas.return_value = gas_limit
    fn.build_transaction.return_value = {
        "from": "0xDeadBeef", "to": "0x1234", "data": "0x",
        "value": 0, "gas": gas_limit, "gasPrice": 3_000_000_000,
        "nonce": 1, "chainId": 97,
    }

    wallet = MagicMock()
    wallet.address = "0x" + "11" * 20
    signed = MagicMock()
    signed.__getitem__ = lambda _s, k: b"\x00" * 32 if k == "rawTransaction" else None
    wallet.sign_transaction.return_value = signed
    return fn, web3, wallet


def _make_paymaster(*, sponsorable: bool):
    pm = MagicMock()
    pm.eth_getTransactionCount.return_value = 7
    pm.isSponsorable.return_value = sponsorable
    pm.eth_sendRawTransaction.return_value = "0x" + "cd" * 32
    return pm


def test_sponsorable_goes_through_paymaster():
    fn, web3, wallet = _make_pieces()
    pm = _make_paymaster(sponsorable=True)
    pm_hash = bytes.fromhex("cd" * 32)
    web3.eth.wait_for_transaction_receipt.return_value = _ok_receipt(pm_hash)

    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=pm)
    result = ex._execute_function(fn, description="submit")

    pm.eth_sendRawTransaction.assert_called_once()          # sent via paymaster
    web3.eth.send_raw_transaction.assert_not_called()       # NOT self-paid
    assert result["transactionHash"] == "0x" + "cd" * 32
    # sponsored tx is sent gas-free
    assert fn.build_transaction.return_value["gasPrice"] == 0


def test_not_sponsorable_falls_back_to_self_pay(caplog):
    fn, web3, wallet = _make_pieces()
    pm = _make_paymaster(sponsorable=False)

    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=pm)
    with caplog.at_level("INFO"):
        result = ex._execute_function(fn, description="create_job")

    pm.eth_sendRawTransaction.assert_not_called()           # paymaster NOT used to send
    web3.eth.send_raw_transaction.assert_called_once()      # self-paid
    assert result["transactionHash"] == "0x" + "ab" * 32
    assert "not sponsorable" in caplog.text


def test_issponsorable_error_falls_back_to_self_pay():
    fn, web3, wallet = _make_pieces()
    pm = _make_paymaster(sponsorable=True)
    pm.isSponsorable.side_effect = RuntimeError("megafuel 503")

    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=pm)
    result = ex._execute_function(fn, description="fund")

    pm.eth_sendRawTransaction.assert_not_called()
    web3.eth.send_raw_transaction.assert_called_once()
    assert result["transactionHash"] == "0x" + "ab" * 32


def test_paymaster_nonce_error_falls_back_to_self_pay():
    fn, web3, wallet = _make_pieces()
    pm = _make_paymaster(sponsorable=True)
    pm.eth_getTransactionCount.side_effect = RuntimeError("megafuel down")

    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=pm)
    result = ex._execute_function(fn, description="settle")

    pm.isSponsorable.assert_not_called()                    # never reached
    web3.eth.send_raw_transaction.assert_called_once()      # self-paid
    assert result["transactionHash"] == "0x" + "ab" * 32


def test_no_paymaster_self_pays():
    fn, web3, wallet = _make_pieces()
    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=None)
    result = ex._execute_function(fn, description="register_job")
    web3.eth.send_raw_transaction.assert_called_once()
    assert result["transactionHash"] == "0x" + "ab" * 32


def test_preflight_revert_propagates_does_not_self_pay():
    """A genuine revert in the sponsored path must raise, not silently self-pay
    (self-pay would revert too — surface the reason once)."""
    fn, web3, wallet = _make_pieces()
    pm = _make_paymaster(sponsorable=True)
    web3.eth.call.side_effect = RuntimeError("execution reverted: SubmissionTooLate()")

    ex = LocalExecutor(web3=web3, wallet_provider=wallet, paymaster=pm)
    with pytest.raises(RuntimeError, match="would revert"):
        ex._execute_function(fn, description="submit")

    pm.eth_sendRawTransaction.assert_not_called()
    web3.eth.send_raw_transaction.assert_not_called()       # no self-pay attempt
