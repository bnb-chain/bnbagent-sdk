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

    def test_sign_typed_data_eip3009_round_trip(self, wdir):
        """EIP-712 round-trip: sign → recover address matches wallet.

        Uses ``SigningPolicy.permissive()`` to keep this test focused on
        the cryptographic round-trip (validBefore here is far past the
        default 900s future cap). Strict-policy behaviour is covered in
        ``test_wallet_policy_gating.py``.
        """
        from eth_account.messages import encode_typed_data

        from bnbagent.signing import SigningPolicy

        wallet = EVMWalletProvider(
            password=PW, private_key=PK, wallets_dir=wdir,
            signing_policy=SigningPolicy.permissive(),
        )
        domain = {
            "name": "United Stables",
            "version": "1",
            "chainId": 56,
            "verifyingContract": "0xcE24439F2D9C6a2289F741120FE202248B666666",
        }
        types = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        }
        message = {
            "from": wallet.address,
            "to": "0x" + "b" * 40,
            "value": 1_000_000,
            "validAfter": 0,
            "validBefore": 2_000_000_000,
            "nonce": "0x" + "c" * 64,
        }
        signed = wallet.sign_typed_data(domain, types, message)

        assert "messageHash" in signed
        assert "signature" in signed
        # Recover signer address and verify it matches the wallet.
        message_types = {k: v for k, v in types.items() if k != "EIP712Domain"}
        signable = encode_typed_data(
            domain_data=domain, message_types=message_types, message_data=message
        )
        recovered = Account.recover_message(signable, signature=signed["signature"])
        assert recovered == wallet.address

    def test_sign_typed_data_strips_domain_type_if_supplied(self, wdir):
        """Caller may pass EIP712Domain entry in types; should produce same sig."""
        from bnbagent.signing import SigningPolicy
        wallet = EVMWalletProvider(
            password=PW, private_key=PK, wallets_dir=wdir,
            signing_policy=SigningPolicy.permissive(),
        )
        domain = {"name": "Test", "version": "1", "chainId": 56,
                  "verifyingContract": "0x" + "1" * 40}
        types_with_domain = {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Mail": [{"name": "contents", "type": "string"}],
        }
        types_without_domain = {"Mail": [{"name": "contents", "type": "string"}]}
        msg = {"contents": "hello"}
        sig1 = wallet.sign_typed_data(domain, types_with_domain, msg)
        sig2 = wallet.sign_typed_data(domain, types_without_domain, msg)
        assert sig1["signature"] == sig2["signature"]

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

