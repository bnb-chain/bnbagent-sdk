"""In-memory per-token session budget tracker for X402Signer.

Tracks cumulative spending per checksum-normalized token contract within
the lifetime of a single X402Signer instance. Caps are configured at
construction; the tracker only records what was *successfully* spent —
the X402Signer commits to the tracker after the underlying wallet sign
returns, so a rejected sign never deducts.
"""

from __future__ import annotations

from threading import Lock

from web3 import Web3


class SessionBudgetTracker:
    """Per-token cumulative spend tracker with caps."""

    def __init__(self, caps: dict[str, int] | None = None) -> None:
        """
        Args:
            caps: ``{checksum_or_raw_token_address: max_total_base_units}``.
                Addresses are checksum-normalized on construction; a missing
                token is treated as having no cap (None) — i.e. unlimited
                session spend for that token (per-call cap still enforced
                separately by X402Signer).
        """
        self._caps: dict[str, int] = {}
        if caps:
            for addr, cap in caps.items():
                self._caps[Web3.to_checksum_address(addr)] = int(cap)
        self._spent: dict[str, int] = {}
        self._lock = Lock()

    def cap_for(self, token: str) -> int | None:
        return self._caps.get(Web3.to_checksum_address(token))

    def spent(self, token: str) -> int:
        return self._spent.get(Web3.to_checksum_address(token), 0)

    def would_exceed(self, token: str, amount: int) -> bool:
        cs = Web3.to_checksum_address(token)
        cap = self._caps.get(cs)
        if cap is None:
            return False
        return self._spent.get(cs, 0) + int(amount) > cap

    def commit(self, token: str, amount: int) -> None:
        """Record a successful spend. Idempotent only at the value-add level."""
        cs = Web3.to_checksum_address(token)
        with self._lock:
            self._spent[cs] = self._spent.get(cs, 0) + int(amount)
