"""
EVM Wallet Provider Implementation

Manages traditional EVM wallets with Keystore V3 encryption.
All private keys are stored encrypted using password-based encryption.
Compatible with MetaMask/Geth Keystore format.
"""

from typing import Optional, Dict, Any
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_account.messages import encode_defunct

from ..utils.logger import get_logger
from ..utils.state_file import StateFileManager
from .wallet_provider import WalletProvider


class EVMWalletProvider(WalletProvider):
    """
    EVM wallet provider with mandatory Keystore encryption.

    All wallets are stored using Keystore V3 format with password protection.
    This ensures private keys are never stored in plain text.

    Security features:
    - Password-based encryption using scrypt KDF
    - AES-128-CTR encryption for private keys
    - File permissions set to 0o600 (owner read/write only)
    - Compatible with MetaMask/Geth keystore format
    """

    def __init__(
        self,
        password: str,
        private_key: Optional[str] = None,
        debug: bool = False,
    ):
        """
        Initialize the EVM wallet provider with Keystore encryption.

        Args:
            password: Password for Keystore encryption/decryption (REQUIRED).
                     Used to encrypt new wallets and decrypt existing ones.
            private_key: Optional private key string (hex format with or without 0x prefix).
                        If provided, imports and encrypts this key.
                        If not provided, loads existing keystore or creates new wallet.
            debug: Enable debug logging

        Raises:
            ValueError: If password is empty or None

        Example:
            >>> # Create new encrypted wallet (auto-generates key)
            >>> wallet = EVMWalletProvider(password="my-secure-password")

            >>> # Import existing private key with encryption
            >>> wallet = EVMWalletProvider(
            ...     password="my-secure-password",
            ...     private_key="0x..."
            ... )

            >>> # Load existing encrypted wallet
            >>> wallet = EVMWalletProvider(password="my-secure-password")
        """
        if not password:
            raise ValueError(
                "Password is required for wallet encryption. "
                "Please provide a secure password."
            )

        self.debug = debug
        self._logger = get_logger(f"{__name__}.{self.__class__.__name__}", debug=debug)
        self._password = password

        # Initialize state file manager
        self.state_manager = StateFileManager(debug=debug)

        self._account: Optional[LocalAccount] = None

        # Load or create wallet
        if private_key:
            self._import_private_key(private_key)
        else:
            self._load_or_create_wallet()

    def _import_private_key(self, private_key: str) -> None:
        """
        Import and encrypt a private key.

        Args:
            private_key: Private key in hex format (with or without 0x prefix)
        """
        try:
            # Remove 0x prefix if present
            if private_key.startswith("0x"):
                private_key = private_key[2:]

            # Validate private key format
            if len(private_key) != 64:
                raise ValueError("Private key must be 64 hex characters (32 bytes)")

            self._account = Account.from_key(private_key)
            self._logger.debug(
                f"Imported private key for address: {self._account.address}"
            )

            # Save as encrypted keystore
            self._save_wallet()

        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")

    def _load_or_create_wallet(self) -> None:
        """
        Load wallet from file or create a new one.
        Only creates wallet once - if file exists, loads from it.
        """
        if self.state_manager.exists():
            self._load_from_file()
        else:
            self._create_wallet()

    def _load_from_file(self) -> None:
        """
        Load and decrypt wallet from Keystore file.
        Also supports migrating legacy plain text format to encrypted.
        """
        try:
            # Try to load encrypted keystore
            keystore = self.state_manager.get("keystore")

            if keystore:
                # Decrypt keystore
                try:
                    private_key = Account.decrypt(keystore, self._password)
                    self._account = Account.from_key(private_key)
                    self._logger.debug(
                        f"Loaded encrypted wallet: {self._account.address}"
                    )
                except ValueError as e:
                    raise ValueError(f"Failed to decrypt keystore (wrong password?): {e}")
            else:
                # Check for legacy plain text format and migrate
                private_key = self.state_manager.get("private_key")

                if not private_key:
                    raise ValueError(
                        "Invalid state file: missing keystore. "
                        "Please create a new wallet."
                    )

                # Remove 0x prefix if present
                if private_key.startswith("0x"):
                    private_key = private_key[2:]

                self._account = Account.from_key(private_key)
                self._logger.info(
                    f"Migrating legacy wallet to encrypted format: {self._account.address}"
                )

                # Migrate to encrypted format
                self._save_wallet()

        except Exception as e:
            raise RuntimeError(f"Failed to load wallet: {str(e)}")

    def _create_wallet(self) -> None:
        """Create a new wallet and save encrypted."""
        try:
            # Generate new account
            self._account = Account.create()
            self._logger.debug(f"Created new wallet: {self._account.address}")

            # Save encrypted
            self._save_wallet()

        except Exception as e:
            raise RuntimeError(f"Failed to create wallet: {str(e)}")

    def _save_wallet(self) -> None:
        """Save wallet as encrypted Keystore V3 format."""
        # Encrypt private key using Keystore V3 format
        keystore = Account.encrypt(self._account.key, self._password)

        # Save keystore to state file
        self.state_manager.set("keystore", keystore)
        self.state_manager.set("address", self._account.address)
        self.state_manager.set("encrypted", True)

        # Remove plain text private_key if it exists (security cleanup)
        try:
            data = self.state_manager.load()
            if "private_key" in data:
                del data["private_key"]
                self.state_manager.save(data)
                self._logger.debug("Removed legacy plain text key from state file")
        except Exception:
            pass

        self._logger.debug(f"Saved encrypted wallet (Keystore V3)")

    @property
    def address(self) -> str:
        """
        Get the wallet address.

        Returns:
            str: The Ethereum address of the wallet
        """
        if self._account is None:
            raise RuntimeError("Account not initialized")
        return self._account.address

    def sign_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sign a transaction.

        Args:
            transaction: Transaction dictionary with fields like 'to', 'value', 'gas',
                        'gasPrice', 'nonce', 'data', 'chainId'

        Returns:
            dict: Signed transaction with 'rawTransaction', 'hash', 'r', 's', 'v'
        """
        self._logger.debug(f"Signing transaction: {transaction}")

        signed_txn = self._account.sign_transaction(transaction)

        self._logger.debug(f"Transaction signed: hash={signed_txn.hash.hex()}")

        # Return dict format for consistent interface across wallet providers
        return {
            "rawTransaction": signed_txn.raw_transaction,
            "hash": signed_txn.hash,
            "r": signed_txn.r,
            "s": signed_txn.s,
            "v": signed_txn.v,
        }

    def sign_message(self, message: str) -> Dict[str, Any]:
        """
        Sign a message using EIP-191 personal sign.

        Args:
            message: Message string to sign

        Returns:
            dict: Signature with 'messageHash', 'r', 's', 'v', 'signature'
        """
        self._logger.debug(f"Signing message: {message[:50]}...")

        # Use EIP-191 personal sign format
        signable_message = encode_defunct(text=message)
        signed_message = self._account.sign_message(signable_message)

        self._logger.debug(f"Message signed: hash={signed_message.messageHash.hex()}")

        # Return dict format for consistent interface across wallet providers
        return {
            "messageHash": signed_message.messageHash,
            "r": signed_message.r,
            "s": signed_message.s,
            "v": signed_message.v,
            "signature": signed_message.signature,
        }

    def export_private_key(self) -> str:
        """
        Export the private key in hex format.

        WARNING: Handle with care! Never share or expose your private key.
        Anyone with access to your private key can control your wallet.

        Returns:
            str: Private key with 0x prefix

        Example:
            >>> wallet = EVMWalletProvider(password="my-password")
            >>> private_key = wallet.export_private_key()
            >>> print(f"Private Key: {private_key}")
        """
        self._logger.warning(
            "Exporting private key. Handle with extreme care - "
            "never share or expose your private key!"
        )
        return f"0x{self._account.key.hex()}"

    def export_keystore(self) -> Dict[str, Any]:
        """
        Export the wallet as Keystore V3 JSON.

        The exported keystore is encrypted with the current password.
        This format is compatible with MetaMask, Geth, and other wallets.

        Returns:
            dict: Keystore V3 JSON object

        Example:
            >>> wallet = EVMWalletProvider(password="my-password")
            >>> keystore = wallet.export_keystore()
            >>> import json
            >>> with open("my-wallet.json", "w") as f:
            ...     json.dump(keystore, f)
        """
        keystore = Account.encrypt(self._account.key, self._password)
        self._logger.debug(f"Exported keystore for address: {self._account.address}")
        return keystore

    def get_wallet_info(self) -> Dict[str, str]:
        """
        Get wallet information (address only, no sensitive data).

        Returns:
            dict: Wallet info with 'address'

        Example:
            >>> wallet = EVMWalletProvider(password="my-password")
            >>> info = wallet.get_wallet_info()
            >>> print(f"Address: {info['address']}")
        """
        return {
            "address": self.address,
        }
