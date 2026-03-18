"""
Test cases for EVMWalletProvider (~/.bnbagent/wallets/ keystore)
"""

import json

import pytest
from eth_account import Account

from bnbagent import EVMWalletProvider

PW = "test-secure-password-123"
PK = "0x" + "a" * 64  # 32 bytes hex


class TestEVMWalletProvider:
    """Test cases for EVMWalletProvider"""

    @pytest.fixture
    def wdir(self, tmp_path):
        """Isolated wallets directory."""
        return tmp_path / "wallets"

    # ── Creation & Import ──

    def test_create_new_wallet(self, wdir):
        wallet = EVMWalletProvider(password=PW, wallets_dir=wdir)
        assert wallet.address.startswith("0x")
        assert len(wallet.address) == 42
        assert wallet.source == "created_new"
        # File written
        assert (wdir / f"{wallet.address}.json").is_file()

    def test_import_private_key(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        expected = Account.from_key(PK).address
        assert wallet.address == expected
        assert wallet.source == "imported"
        assert (wdir / f"{expected}.json").is_file()

    def test_import_private_key_without_0x(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key="a" * 64, wallets_dir=wdir)
        expected = Account.from_key("a" * 64).address
        assert wallet.address == expected

    def test_invalid_private_key(self, wdir):
        with pytest.raises(ValueError, match="Invalid private key"):
            EVMWalletProvider(password=PW, private_key="invalid-key", wallets_dir=wdir)

    def test_password_required(self):
        with pytest.raises(ValueError, match="Password is required"):
            EVMWalletProvider(password="")
        with pytest.raises(ValueError, match="Password is required"):
            EVMWalletProvider(password=None)

    def test_persist_false_requires_private_key(self):
        with pytest.raises(ValueError, match="private_key is required"):
            EVMWalletProvider(password=PW, persist=False)

    # ── Load from keystore ──

    def test_load_existing_wallet(self, wdir):
        # Import first
        w1 = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        # Load back
        w2 = EVMWalletProvider(password=PW, wallets_dir=wdir)
        assert w2.address == w1.address
        assert w2.source == "loaded_keystore"

    def test_load_by_address(self, wdir):
        w1 = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        w2 = EVMWalletProvider(password=PW, address=w1.address, wallets_dir=wdir)
        assert w2.address == w1.address

    def test_wrong_password_fails(self, wdir):
        EVMWalletProvider(password="correct", private_key=PK, wallets_dir=wdir)
        with pytest.raises(ValueError, match="wrong password"):
            EVMWalletProvider(password="wrong", wallets_dir=wdir)

    def test_multiple_wallets_requires_address(self, wdir):
        EVMWalletProvider(password=PW, private_key="0x" + "a" * 64, wallets_dir=wdir)
        EVMWalletProvider(password=PW, private_key="0x" + "b" * 64, wallets_dir=wdir)
        with pytest.raises(ValueError, match="Multiple wallets"):
            EVMWalletProvider(password=PW, wallets_dir=wdir)

    def test_nonexistent_address_fails(self, wdir):
        wdir.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError, match="Keystore not found"):
            EVMWalletProvider(password=PW, address="0xdead", wallets_dir=wdir)

    # ── Static helpers ──

    def test_keystore_exists(self, wdir):
        assert not EVMWalletProvider.keystore_exists(wallets_dir=wdir)
        w = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        assert EVMWalletProvider.keystore_exists(wallets_dir=wdir)
        assert EVMWalletProvider.keystore_exists(address=w.address, wallets_dir=wdir)
        assert not EVMWalletProvider.keystore_exists(address="0xdead", wallets_dir=wdir)

    def test_list_wallets(self, wdir):
        assert EVMWalletProvider.list_wallets(wdir) == []
        w1 = EVMWalletProvider(password=PW, private_key="0x" + "a" * 64, wallets_dir=wdir)
        assert EVMWalletProvider.list_wallets(wdir) == [w1.address]

    # ── Signing ──

    def test_sign_transaction(self, wdir):
        from eth_utils import to_checksum_address

        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        tx = {
            "to": to_checksum_address("0x" + "b" * 40),
            "value": 10**18,
            "gas": 21000,
            "gasPrice": 20_000_000_000,
            "nonce": 0,
            "chainId": 97,
        }
        signed = wallet.sign_transaction(tx)
        assert "rawTransaction" in signed
        assert "hash" in signed

    def test_sign_message(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        signed = wallet.sign_message("Hello, World!")
        assert "messageHash" in signed
        assert "signature" in signed

    # ── Export ──

    def test_export_private_key(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        exported = wallet.export_private_key()
        assert exported.startswith("0x")
        assert len(exported) == 66

    def test_export_keystore(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        ks = wallet.export_keystore()
        assert ks["version"] == 3
        assert "crypto" in ks
        # Verify roundtrip
        recovered = Account.from_key(Account.decrypt(ks, PW))
        assert recovered.address == wallet.address

    def test_get_wallet_info(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, wallets_dir=wdir)
        info = wallet.get_wallet_info()
        assert info == {"address": wallet.address}

    # ── In-memory only ──

    def test_persist_false_no_file(self, wdir):
        wallet = EVMWalletProvider(password=PW, private_key=PK, persist=False, wallets_dir=wdir)
        assert wallet.address == Account.from_key(PK).address
        assert not wdir.exists()  # No directory created

    # ── Legacy migration ──

    def test_migrate_legacy_bnbagent_state(self, wdir, tmp_path, monkeypatch):
        """Legacy .bnbagent_state in CWD should be migrated to ~/.bnbagent/wallets/."""
        # Create legacy file
        account = Account.from_key(PK)
        keystore = Account.encrypt(account.key, PW)
        legacy_path = tmp_path / ".bnbagent_state"
        legacy_path.write_text(json.dumps({"keystore": keystore, "address": account.address}))

        monkeypatch.chdir(tmp_path)
        wallet = EVMWalletProvider(password=PW, wallets_dir=wdir)
        assert wallet.address == account.address
        assert not legacy_path.exists()  # Removed after migration
        assert (wdir / f"{account.address}.json").is_file()
