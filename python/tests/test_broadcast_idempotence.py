"""An ambiguous broadcast must not sign a second payment.

RPC fault injection checks SDK control flow; it is not an asset-loss experiment.
"""

from functools import partial

import pytest
from web3 import Web3
from web3.exceptions import TimeExhausted

from bnbagent.core.contract_mixin import ContractClientMixin
from bnbagent.core.nonce_manager import NonceManager
from bnbagent.exceptions import TransactionPendingError
from bnbagent.wallets.local_executor import LocalExecutor
from tests.test_local_executor_paymaster import _make_pieces


@pytest.mark.parametrize("entry", ["contract", "executor"])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        "already known",
        "replacement transaction underpriced",
        "429 Too Many Requests",
        "request timed out",
        "connection reset",
    ],
)
def test_ambiguous_broadcast_keeps_original_hash_and_never_resigns(entry, pending, message):
    NonceManager._clear_all()
    fn, web3, wallet = _make_pieces()
    web3.eth.send_raw_transaction.side_effect = ValueError(message)
    expected_hash = bytes(Web3.keccak(b"\x00" * 32))
    if pending:
        web3.eth.wait_for_transaction_receipt.side_effect = TimeExhausted("pending")
    if entry == "contract":
        client = ContractClientMixin()
        client.w3, client._wallet_provider, client._account = web3, wallet, wallet.address
        send = partial(client._send_tx, fn)
    else:
        executor = LocalExecutor(web3=web3, wallet_provider=wallet)
        send = partial(executor._execute_function, fn, description="acceptance")
    try:
        if pending:
            with pytest.raises(TransactionPendingError) as pending_error:
                send()
            assert pending_error.value.tx_hash == "0x" + expected_hash.hex()
        else:
            send()
        wallet.sign_transaction.assert_called_once()
        web3.eth.send_raw_transaction.assert_called_once()
        assert web3.eth.wait_for_transaction_receipt.call_args.args[0] == expected_hash
    finally:
        NonceManager._clear_all()
