"""
EVM Wallet Provider Implementation

Manages EVM wallets with Keystore V3 encryption.
Keystores are stored in ~/.bnbagent/wallets/<address>.json.

Security:
- scrypt KDF + AES-128-CTR encryption (Keystore V3 / MetaMask / Geth compatible)
- File permissions 0o600 (owner read/write only)
- Directory permissions 0o700 (owner only)
- Private key only needed on first import; subsequent runs use password only
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import inspect

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount

from ..signing import SigningPolicy, check as _policy_check
from .wallet_provider import WalletProvider

logger = logging.getLogger(__name__)

# Default wallet directory
_WALLETS_DIR = Path.home() / ".bnbagent" / "wallets"


class EVMWalletProvider(WalletProvider):
    """
    EVM wallet provider with Keystore V3 encryption.

    Wallets are stored as individual JSON files in ``~/.bnbagent/wallets/``,
    named by address (e.g. ``0x1234...abcd.json``).

    Typical lifecycle::

        # First run — import and encrypt
        wallet = EVMWalletProvider(password="pw", private_key="0x...")

        # Subsequent runs — load from keystore (no private key needed)
        wallet = EVMWalletProvider(password="pw", address="0x1234...abcd")

        # Auto-select — if only one wallet exists
        wallet = EVMWalletProvider(password="pw")
    """

    def __init__(
        self,
        password: str,
        private_key: str | None = None,
        address: str | None = None,
        persist: bool = True,
        wallets_dir: str | Path | None = None,
        signing_policy: SigningPolicy | None = None,
    ):
        """
        Initialize the EVM wallet provider.

        Args:
            password: Password for Keystore encryption/decryption (REQUIRED).
            private_key: Private key to import (hex, with or without 0x).
                        Only needed on first run; encrypted to disk afterward.
            address: Address of an existing keystore to load. If omitted and
                    no private_key is given, auto-selects if exactly one
                    keystore exists.
            persist: Save encrypted keystore to disk (default: True).
                    Set False for in-memory-only (e.g. tests).
            wallets_dir: Override wallet directory (default: ~/.bnbagent/wallets/).
            signing_policy: Policy applied to every :meth:`sign_typed_data`
                call. Defaults to :meth:`SigningPolicy.strict_default`, which
                only accepts EIP-3009 ``TransferWithAuthorization`` /
                ``ReceiveWithAuthorization`` against the registered U-token
                payment-token deployments and refuses every unbounded Permit
                variant. Pass :meth:`SigningPolicy.permissive` to disable the
                gate (intended for tests; logs a warning on construction).

        Raises:
            ValueError: If password is empty, private_key is invalid, or
                       no wallet can be resolved.
        """
        if not password:
            raise ValueError(
                "Password is required for wallet encryption. Please provide a secure password."
            )

        self._password = password
        self._persist = persist
        self._wallets_dir = Path(wallets_dir) if wallets_dir else _WALLETS_DIR
        self._account: LocalAccount | None = None
        self._source: str = ""  # "imported", "loaded_keystore", "created_new"
        self._signing_policy = signing_policy or SigningPolicy.strict_default()

        if private_key:
            self._import_private_key(private_key)
        elif persist:
            self._load_wallet(address)
        else:
            raise ValueError("private_key is required when persist=False (in-memory-only mode)")

    # ── Static helpers ──

    @staticmethod
    def keystore_exists(
        address: str | None = None,
        wallets_dir: str | Path | None = None,
    ) -> bool:
        """Check if an encrypted keystore exists on disk.

        Args:
            address: Check for a specific address. If None, returns True
                    if *any* keystore file exists.
            wallets_dir: Override wallet directory.

        Returns:
            True if a matching keystore file is found.
        """
        d = Path(wallets_dir) if wallets_dir else _WALLETS_DIR
        if not d.is_dir():
            return False
        if address:
            return (d / f"{address}.json").is_file()
        return any(d.glob("0x*.json"))

    @staticmethod
    def list_wallets(wallets_dir: str | Path | None = None) -> list[str]:
        """List all wallet addresses that have keystores on disk.

        Returns:
            List of checksummed addresses (e.g. ["0x1234...abcd"]).
        """
        d = Path(wallets_dir) if wallets_dir else _WALLETS_DIR
        if not d.is_dir():
            return []
        return [p.stem for p in sorted(d.glob("0x*.json"))]

    @property
    def source(self) -> str:
        """How the wallet was initialized: 'imported', 'loaded_keystore', or 'created_new'."""
        return self._source

    # ── Private key import ──

    def _import_private_key(self, private_key: str) -> None:
        """Import and encrypt a private key."""
        try:
            if private_key.startswith("0x"):
                private_key = private_key[2:]
            if len(private_key) != 64:
                raise ValueError("Private key must be 64 hex characters (32 bytes)")

            self._account = Account.from_key(private_key)
            self._source = "imported"

            if self._persist:
                self._save_keystore()
                logger.info(
                    "Private key imported and encrypted: %s "
                    "(PRIVATE_KEY can be removed from env)",
                    self._account.address,
                )
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}") from e

    # ── Load from disk ──

    def _load_wallet(self, address: str | None) -> None:
        """Load a wallet from keystore, or create a new one if none exists."""
        if address:
            self._load_keystore(address)
        else:
            wallets = self.list_wallets(self._wallets_dir)
            if len(wallets) == 1:
                self._load_keystore(wallets[0])
            elif len(wallets) > 1:
                raise ValueError(
                    f"Multiple wallets found in {self._wallets_dir}: {wallets}. "
                    "Set WALLET_ADDRESS to specify which one to use."
                )
            else:
                self._create_wallet()

    def _load_keystore(self, address: str) -> None:
        """Load and decrypt a keystore file by address."""
        ks_path = self._wallets_dir / f"{address}.json"
        if not ks_path.is_file():
            raise ValueError(f"Keystore not found: {ks_path}")

        try:
            with open(ks_path) as f:
                keystore = json.load(f)
            private_key = Account.decrypt(keystore, self._password)
            self._account = Account.from_key(private_key)
            self._source = "loaded_keystore"
            logger.info("Wallet loaded from keystore: %s", self._account.address)
        except ValueError as e:
            raise ValueError(f"Failed to decrypt keystore (wrong password?): {e}") from e
        except Exception as e:
            raise RuntimeError(f"Failed to load keystore {ks_path}: {e}") from e

    def _create_wallet(self) -> None:
        """Generate a new wallet and save encrypted."""
        try:
            self._account = Account.create()
            self._source = "created_new"
            if self._persist:
                self._save_keystore()
            logger.info("Created new wallet: %s", self._account.address)
        except Exception as e:
            raise RuntimeError(f"Failed to create wallet: {e}") from e

    # ── Save to disk ──

    def _save_keystore(self) -> None:
        """Save wallet as ~/.bnbagent/wallets/<address>.json (Keystore V3)."""
        self._wallets_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._wallets_dir, 0o700)

        keystore = Account.encrypt(self._account.key, self._password)
        ks_path = self._wallets_dir / f"{self._account.address}.json"

        # Atomic write
        fd, temp_path = tempfile.mkstemp(
            dir=self._wallets_dir, prefix=".ks_", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(keystore, f)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, ks_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

        logger.debug("Saved keystore: %s", ks_path)

    # ── Public API ──

    @property
    def address(self) -> str:
        """Get the wallet address."""
        if self._account is None:
            raise RuntimeError("Account not initialized")
        return self._account.address

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """Sign a transaction. Returns dict with 'rawTransaction', 'hash', 'r', 's', 'v'."""
        signed_txn = self._account.sign_transaction(transaction)
        return {
            "rawTransaction": signed_txn.raw_transaction,
            "hash": signed_txn.hash,
            "r": signed_txn.r,
            "s": signed_txn.s,
            "v": signed_txn.v,
        }

    def sign_message(self, message: str) -> dict[str, Any]:
        """Sign a message using EIP-191 personal sign."""
        signable_message = encode_defunct(text=message)
        signed_message = self._account.sign_message(signable_message)
        return {
            "messageHash": signed_message.message_hash,
            "r": signed_message.r,
            "s": signed_message.s,
            "v": signed_message.v,
            "signature": signed_message.signature,
        }

    @property
    def signing_policy(self) -> SigningPolicy:
        """The SigningPolicy currently enforcing sign_typed_data calls."""
        return self._signing_policy

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign EIP-712 typed data after passing the configured SigningPolicy.

        See :meth:`WalletProvider.sign_typed_data` for full semantics. The
        policy check runs first and may raise
        :class:`bnbagent.signing.PolicyViolation`.
        """
        _policy_check(self._signing_policy, domain, types, message)
        return self._raw_sign_typed_data(domain, types, message)

    def _DANGEROUS_sign_typed_data_no_policy(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """⚠️ BYPASSES SigningPolicy. Tests + trusted SDK code only.

        Calls the raw EIP-712 signer without policy enforcement. Every call
        emits a WARNING log with the calling module so audit grep can find
        bypasses. Production / agent-reachable code MUST NOT call this; CI
        lint will reject occurrences in @tool-decorated closures.
        """
        caller = "<unknown>"
        try:
            frame = inspect.stack()[1]
            caller = f"{frame.filename}:{frame.lineno}"
        except Exception:
            pass
        logger.warning(
            "_DANGEROUS_sign_typed_data_no_policy invoked from %s — POLICY BYPASS",
            caller,
        )
        return self._raw_sign_typed_data(domain, types, message)

    def _raw_sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Underlying eth_account call. Does not consult SigningPolicy."""
        # eth_account expects ``types`` without the EIP712Domain entry — it adds
        # its own. Drop it if the caller included it (a common convention).
        message_types = {k: v for k, v in types.items() if k != "EIP712Domain"}
        signed = self._account.sign_typed_data(
            domain_data=domain,
            message_types=message_types,
            message_data=message,
        )
        return {
            "messageHash": signed.message_hash,
            "r": signed.r,
            "s": signed.s,
            "v": signed.v,
            "signature": signed.signature,
        }

    def export_private_key(self) -> str:
        """Export the private key in hex format. Handle with extreme care."""
        logger.warning("Exporting private key — never share or expose it!")
        return f"0x{self._account.key.hex()}"

    def export_keystore(self) -> dict[str, Any]:
        """Export the wallet as Keystore V3 JSON (MetaMask/Geth compatible)."""
        return Account.encrypt(self._account.key, self._password)

    def get_wallet_info(self) -> dict[str, str]:
        """Get wallet information (address only, no sensitive data)."""
        return {"address": self.address}
