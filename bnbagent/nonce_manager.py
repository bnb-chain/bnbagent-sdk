"""
Thread-safe nonce manager with local tracking and auto-recovery.

Production-grade nonce management for sequential blockchain transactions:
  - Seeds from 'pending' on first use (captures in-mempool txs)
  - Local increment avoids RPC on every send
  - Auto re-syncs on nonce errors (too low, already known, underpriced)
  - Thread-safe via Lock (safe with asyncio.to_thread)
  - Singleton per (rpc_url, account) — shared across APEXClient instances
"""

import logging
import threading
from typing import Dict, Optional, Tuple

from web3 import Web3

logger = logging.getLogger(__name__)


class NonceManager:
    """
    Thread-safe nonce manager with local tracking and chain re-sync on error.

    Usage:
        nonce_mgr = NonceManager.for_account(w3, account_address)
        nonce = nonce_mgr.get_nonce()      # auto-seeds, then increments locally
        # ... send tx with nonce ...
        # on nonce error:
        if nonce_mgr.handle_error(error, nonce):
            # retry with new nonce from get_nonce()
    """

    _instances: Dict[Tuple[str, str], "NonceManager"] = {}
    _class_lock = threading.Lock()

    # Substrings that indicate a nonce-related RPC error
    _NONCE_ERROR_PATTERNS = (
        "nonce too low",
        "already known",
        "replacement transaction underpriced",
    )

    @classmethod
    def for_account(cls, w3: Web3, account: str) -> "NonceManager":
        """
        Get or create a NonceManager singleton for this account + RPC endpoint.

        Two APEXClient instances sharing the same wallet and RPC will
        automatically share the same NonceManager.
        """
        account = Web3.to_checksum_address(account)
        rpc_url = _get_rpc_url(w3)
        key = (rpc_url, account)
        with cls._class_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(w3, account)
            return cls._instances[key]

    def __init__(self, w3: Web3, account: str):
        self._w3 = w3
        self._account = Web3.to_checksum_address(account)
        self._lock = threading.Lock()
        self._nonce: Optional[int] = None

    def get_nonce(self) -> int:
        """
        Get the next nonce to use.

        First call seeds from chain ('pending'). Subsequent calls increment
        locally without RPC. Thread-safe — concurrent callers get unique nonces.
        """
        with self._lock:
            if self._nonce is None:
                self._nonce = self._w3.eth.get_transaction_count(
                    self._account, "pending"
                )
                logger.debug(
                    f"[NonceManager] Seeded nonce for {self._account}: {self._nonce}"
                )
            nonce = self._nonce
            self._nonce += 1
            return nonce

    def handle_error(self, error: Exception, used_nonce: int) -> bool:
        """
        Handle a transaction error. Re-syncs nonce from chain if the error
        is nonce-related.

        Args:
            error: The exception raised by send_raw_transaction
            used_nonce: The nonce that was used in the failed transaction

        Returns:
            True if the error was nonce-related and the caller should retry.
        """
        error_str = str(error).lower()

        if not any(p in error_str for p in self._NONCE_ERROR_PATTERNS):
            return False

        with self._lock:
            chain_nonce = self._w3.eth.get_transaction_count(
                self._account, "pending"
            )
            self._nonce = chain_nonce
            logger.warning(
                f"[NonceManager] Nonce error (used={used_nonce}), "
                f"re-synced to {chain_nonce}"
            )
        return True

    def reset(self):
        """
        Force re-sync from chain on next get_nonce() call.

        Useful after submitting transactions outside this manager
        (e.g., via contract.py or external tools).
        """
        with self._lock:
            self._nonce = None

    @classmethod
    def _clear_all(cls):
        """Clear all singleton instances. For testing only."""
        with cls._class_lock:
            cls._instances.clear()


def _get_rpc_url(w3: Web3) -> str:
    """Extract RPC URL from a Web3 instance for singleton keying."""
    provider = w3.provider
    if hasattr(provider, "endpoint_uri"):
        return str(provider.endpoint_uri)
    return str(id(provider))
