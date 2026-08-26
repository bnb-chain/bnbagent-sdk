"""
Thread-safe nonce reservation state machine.

Production-grade nonce management for sequential blockchain transactions:
  - Seeds from 'pending' on first use (captures in-mempool txs)
  - Explicit reserved -> broadcast/finalized or released transitions
  - Pre-broadcast failures reuse their nonce instead of leaving gaps
  - Broadcast nonces can never be released accidentally
  - Thread-safe via Lock (safe with asyncio.to_thread)
  - Singleton per (rpc_url, account) — shared across ERC8183Client instances
"""

from __future__ import annotations

import logging
import threading
from enum import Enum

from web3 import Web3

logger = logging.getLogger(__name__)


class NonceState(str, Enum):
    """Lifecycle state for a locally allocated nonce."""

    RESERVED = "reserved"
    BROADCAST = "broadcast"


class NonceManager:
    """
    Thread-safe nonce manager with explicit reservation lifecycle.

    Usage:
        nonce_mgr = NonceManager.for_account(w3, account_address)
        nonce = nonce_mgr.reserve()
        try:
            # build + sign transaction
            nonce_mgr.mark_broadcast(nonce, tx_hash)
        except Exception:
            nonce_mgr.release(nonce)  # only before broadcast
    """

    _instances: dict[tuple[str, str], NonceManager] = {}
    _class_lock = threading.Lock()

    # Substrings that indicate a nonce-related RPC error
    _NONCE_ERROR_PATTERNS = (
        "nonce too low",
        "already known",
        "replacement transaction underpriced",
    )

    @classmethod
    def for_account(cls, w3: Web3, account: str) -> NonceManager:
        """
        Get or create a NonceManager singleton for this account + RPC endpoint.

        Two ERC8183Client instances sharing the same wallet and RPC will
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
        self._nonce: int | None = None
        self._states: dict[int, NonceState] = {}
        self._broadcast_hashes: dict[int, str] = {}
        self._released: set[int] = set()

    def reserve(self, seed_nonce: int | None = None) -> int:
        """Reserve the next usable nonce for one transaction attempt."""
        with self._lock:
            if self._nonce is None:
                chain_nonce = (
                    seed_nonce
                    if seed_nonce is not None
                    else self._w3.eth.get_transaction_count(self._account, "pending")
                )
                if not isinstance(chain_nonce, int) or isinstance(chain_nonce, bool):
                    raise TypeError("pending transaction count must be an integer")
                active_floor = max(self._states, default=chain_nonce - 1) + 1
                self._nonce = max(chain_nonce, active_floor)
                self._released = {n for n in self._released if n >= chain_nonce}
                logger.debug(
                    "[NonceManager] Seeded nonce for %s: %s", self._account, self._nonce
                )

            if self._released:
                nonce = min(self._released)
                self._released.remove(nonce)
            else:
                nonce = self._nonce
                self._nonce += 1
            if nonce in self._states:
                raise RuntimeError(f"nonce {nonce} is already {self._states[nonce].value}")
            self._states[nonce] = NonceState.RESERVED
            return nonce

    def release(self, nonce: int) -> bool:
        """Release a reservation that provably failed before broadcast.

        A broadcast nonce is immutable: attempting to release it raises so a
        caller cannot silently reuse a possibly in-flight nonce.
        """
        with self._lock:
            state = self._states.get(nonce)
            if state is NonceState.BROADCAST:
                raise RuntimeError(f"cannot release broadcast nonce {nonce}")
            if state is not NonceState.RESERVED:
                return False
            del self._states[nonce]
            self._released.add(nonce)
            self._collapse_released_tail_locked()
            return True

    def mark_broadcast(self, nonce: int, tx_hash: str) -> None:
        """Mark a nonce as possibly broadcast, using the locally derived hash."""
        with self._lock:
            state = self._states.get(nonce)
            if state is NonceState.BROADCAST:
                if self._broadcast_hashes.get(nonce) != tx_hash:
                    raise RuntimeError(f"nonce {nonce} already tracks another transaction")
                return
            if state is not NonceState.RESERVED:
                raise RuntimeError(f"nonce {nonce} is not reserved")
            self._states[nonce] = NonceState.BROADCAST
            self._broadcast_hashes[nonce] = tx_hash

    def mark_finalized(self, nonce: int) -> None:
        """Forget a confirmed/reverted nonce; it remains consumed on-chain."""
        with self._lock:
            self._states.pop(nonce, None)
            self._broadcast_hashes.pop(nonce, None)
            self._released.discard(nonce)

    def state_of(self, nonce: int) -> NonceState | None:
        """Return the tracked state (primarily useful for diagnostics/tests)."""
        with self._lock:
            return self._states.get(nonce)

    def broadcast_hash(self, nonce: int) -> str | None:
        with self._lock:
            return self._broadcast_hashes.get(nonce)

    def _collapse_released_tail_locked(self) -> None:
        if self._nonce is None:
            return
        while self._nonce > 0:
            tail = self._nonce - 1
            if tail not in self._released or tail in self._states:
                break
            self._released.remove(tail)
            self._nonce = tail

    def get_nonce(self) -> int:
        """
        Get the next nonce to use.

        First call seeds from chain ('pending'). Subsequent calls increment
        locally without RPC. Thread-safe — concurrent callers get unique nonces.
        """
        return self.reserve()

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
            chain_nonce = self._w3.eth.get_transaction_count(self._account, "pending")
            for nonce in list(self._states):
                if nonce < chain_nonce:
                    self._states.pop(nonce, None)
                    self._broadcast_hashes.pop(nonce, None)
            self._released = {n for n in self._released if n >= chain_nonce}
            active_floor = max(self._states, default=chain_nonce - 1) + 1
            self._nonce = max(chain_nonce, active_floor)
            logger.warning(
                "[NonceManager] Nonce error (used=%s), re-synced to %s",
                used_nonce,
                chain_nonce,
            )
        return True

    def reset(self):
        """
        Force re-sync from chain on next get_nonce() call.

        Useful after submitting transactions outside this manager
        (e.g., via contract.py or external tools).
        """
        with self._lock:
            # Reset is a compatibility escape hatch for attempts known not to
            # have broadcast. Never discard BROADCAST entries.
            self._states = {
                nonce: state
                for nonce, state in self._states.items()
                if state is NonceState.BROADCAST
            }
            self._broadcast_hashes = {
                nonce: tx_hash
                for nonce, tx_hash in self._broadcast_hashes.items()
                if nonce in self._states
            }
            self._released.clear()
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
