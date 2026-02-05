"""
Test cases for EVMWalletProvider
"""

import pytest
import tempfile
import os
import json
from unittest.mock import patch, MagicMock
from eth_account import Account

from bnbagent import EVMWalletProvider


class TestEVMWalletProvider:
    """Test cases for EVMWalletProvider"""

    @pytest.fixture
    def temp_state_file(self):
        """Create a temporary state file path"""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        # Remove file so wallet can create it fresh
        os.unlink(path)
        yield path
        # Cleanup
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    def test_password(self):
        """Test password"""
        return "test-secure-password-123"

    @pytest.fixture
    def test_private_key(self):
        """A valid test private key (DO NOT use in production)"""
        return "0x" + "a" * 64  # 32 bytes hex

    def test_create_new_wallet(self, temp_state_file, test_password):
        """Test creating a new wallet"""
        with patch.object(
            EVMWalletProvider, "__init__", lambda self, **kwargs: None
        ):
            pass  # Skip actual init for unit test

        # Create wallet with mocked state manager
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(password=test_password)

            # Should have created a new wallet
            assert wallet.address is not None
            assert wallet.address.startswith("0x")
            assert len(wallet.address) == 42

    def test_password_required(self):
        """Test that password is required"""
        with pytest.raises(ValueError, match="Password is required"):
            EVMWalletProvider(password="")

        with pytest.raises(ValueError, match="Password is required"):
            EVMWalletProvider(password=None)

    def test_import_private_key(self, test_password, test_private_key):
        """Test importing a private key"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            # Should have the correct address for the imported key
            expected_account = Account.from_key(test_private_key)
            assert wallet.address == expected_account.address

    def test_import_private_key_without_0x(self, test_password):
        """Test importing a private key without 0x prefix"""
        private_key_no_prefix = "a" * 64

        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=private_key_no_prefix
            )

            expected_account = Account.from_key(private_key_no_prefix)
            assert wallet.address == expected_account.address

    def test_invalid_private_key(self, test_password):
        """Test importing an invalid private key"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            with pytest.raises(ValueError, match="Invalid private key"):
                EVMWalletProvider(
                    password=test_password,
                    private_key="invalid-key"
                )

    def test_export_private_key(self, test_password, test_private_key):
        """Test exporting the private key"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            exported_key = wallet.export_private_key()

            # Should return the same key with 0x prefix
            assert exported_key.startswith("0x")
            assert len(exported_key) == 66  # 0x + 64 hex chars

    def test_export_keystore(self, test_password, test_private_key):
        """Test exporting the keystore"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            keystore = wallet.export_keystore()

            # Keystore should be a valid Keystore V3 format
            assert isinstance(keystore, dict)
            assert "version" in keystore
            assert keystore["version"] == 3
            assert "crypto" in keystore
            assert "address" in keystore

    def test_keystore_can_be_decrypted(self, test_password, test_private_key):
        """Test that exported keystore can be decrypted with the same password"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            keystore = wallet.export_keystore()

            # Decrypt and verify
            decrypted_key = Account.decrypt(keystore, test_password)
            recovered_account = Account.from_key(decrypted_key)

            assert recovered_account.address == wallet.address

    def test_sign_transaction(self, test_password, test_private_key):
        """Test signing a transaction"""
        from eth_utils import to_checksum_address

        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            # Use checksummed address
            to_address = to_checksum_address("0x" + "b" * 40)

            transaction = {
                "to": to_address,
                "value": 1000000000000000000,  # 1 ETH
                "gas": 21000,
                "gasPrice": 20000000000,
                "nonce": 0,
                "chainId": 97,
            }

            signed = wallet.sign_transaction(transaction)

            assert "rawTransaction" in signed
            assert "hash" in signed
            assert "r" in signed
            assert "s" in signed
            assert "v" in signed

    def test_sign_message(self, test_password, test_private_key):
        """Test signing a message"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            message = "Hello, World!"
            signed = wallet.sign_message(message)

            assert "messageHash" in signed
            assert "signature" in signed
            assert "r" in signed
            assert "s" in signed
            assert "v" in signed

    def test_get_wallet_info(self, test_password, test_private_key):
        """Test getting wallet info"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            info = wallet.get_wallet_info()

            assert "address" in info
            assert info["address"] == wallet.address

    def test_address_property(self, test_password, test_private_key):
        """Test address property"""
        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(
                password=test_password,
                private_key=test_private_key
            )

            address = wallet.address

            assert address.startswith("0x")
            assert len(address) == 42
            # Verify it's the correct address for the private key
            expected = Account.from_key(test_private_key).address
            assert address == expected

    def test_load_existing_wallet(self, test_password, test_private_key):
        """Test loading an existing encrypted wallet"""
        # Create a keystore
        account = Account.from_key(test_private_key)
        keystore = Account.encrypt(account.key, test_password)

        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = True
            mock_instance.get.side_effect = lambda key: keystore if key == "keystore" else None
            mock_sm.return_value = mock_instance

            wallet = EVMWalletProvider(password=test_password)

            assert wallet.address == account.address

    def test_wrong_password_fails(self, test_private_key):
        """Test that wrong password fails to decrypt"""
        # Create a keystore with one password
        account = Account.from_key(test_private_key)
        keystore = Account.encrypt(account.key, "correct-password")

        with patch("bnbagent.wallets.evm_wallet_provider.StateFileManager") as mock_sm:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = True
            mock_instance.get.side_effect = lambda key: keystore if key == "keystore" else None
            mock_sm.return_value = mock_instance

            with pytest.raises(RuntimeError, match="Failed to load wallet"):
                EVMWalletProvider(password="wrong-password")
