from unittest.mock import patch


import pytest


from eth_account import Account


from bnbagent.wallets.evm_wallet_provider import EVMWalletProvider


@pytest.mark.parametrize("address", ["../../outside", "/tmp/other", "0x1234"])
def test_keystore_selector_rejects_path_injection(tmp_path, address):
    with pytest.raises(ValueError, match="20-byte"):
        EVMWalletProvider(password="test", address=address, wallets_dir=tmp_path)


def test_keystore_cannot_substitute_wallet_identity(tmp_path):
    selected = Account.from_key("0x" + "11" * 32)
    substitute = Account.from_key("0x" + "22" * 32)
    (tmp_path / f"{selected.address}.json").write_text("{}")
    with patch(
        "bnbagent.wallets.evm_wallet_provider.Account.decrypt", return_value=substitute.key
    ):
        with pytest.raises(ValueError, match="identity"):
            EVMWalletProvider(password="test", address=selected.address, wallets_dir=tmp_path)
