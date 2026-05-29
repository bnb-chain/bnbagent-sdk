"""In-memory per-token session budget tracker for X402Signer.

Tracks cumulative spending per checksum-normalized token contract within
the lifetime of a single X402Signer instance. Caps are configured at
construction.

Concurrency model (since v0.4.1): the canonical safe-under-concurrency API
is :meth:`reserve` + :meth:`rollback`. ``reserve`` does an atomic
check-and-increment under the tracker's ``Lock``; the caller does the slow
work (e.g. ``wallet.sign_typed_data``) **outside** the lock and calls
``rollback`` if anything fails. This keeps the "rejected signs never
consume budget" invariant while preventing two concurrent callers from
both passing a budget check and over-spending the cap.

The older :meth:`would_exceed` + :meth:`commit` pair is preserved for
backward compatibility but is **not atomic** between the two calls — do
not use it from concurrent code paths.
"""

from __future__ import annotations

from threading import Lock

from web3 import Web3

from .errors import X402BudgetExhaustedError


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
        """Read-only predicate.

        ⚠️ Race-unsafe in concurrent code: a True/False answer here can be
        invalidated by a parallel ``commit`` / ``reserve`` before the caller
        acts on it. Use :meth:`reserve` for the atomic check-and-increment.
        """
        cs = Web3.to_checksum_address(token)
        cap = self._caps.get(cs)
        if cap is None:
            return False
        return self._spent.get(cs, 0) + int(amount) > cap

    def commit(self, token: str, amount: int) -> None:
        """Record a successful spend (unconditional increment under lock).

        ⚠️ Race-unsafe when paired with a separate :meth:`would_exceed`
        check — the gap between check and commit is exactly the TOCTOU
        window. Use :meth:`reserve` for new code; ``commit`` is retained
        for backwards compatibility.
        """
        cs = Web3.to_checksum_address(token)
        with self._lock:
            self._spent[cs] = self._spent.get(cs, 0) + int(amount)

    def reserve(self, token: str, amount: int) -> None:
        """Atomic check-and-increment for the session budget.

        Holds the tracker's ``Lock`` for the entire check+increment so two
        callers cannot both observe the same remaining budget. The caller
        runs the slow work (signing) outside the lock and calls
        :meth:`rollback` on failure to release the reservation — preserving
        the "rejected signs never consume budget" invariant under
        concurrency.

        Raises:
            X402BudgetExhaustedError: If reservation would exceed the cap.
        """
        cs = Web3.to_checksum_address(token)
        amt = int(amount)
        with self._lock:
            cap = self._caps.get(cs)
            cur = self._spent.get(cs, 0)
            if cap is not None and cur + amt > cap:
                raise X402BudgetExhaustedError(
                    f"value {amt} would exceed session budget for {cs} "
                    f"(spent {cur} / cap {cap})"
                )
            self._spent[cs] = cur + amt

    def rollback(self, token: str, amount: int) -> None:
        """Release an earlier :meth:`reserve` (decrement under lock).

        Floors at zero so a buggy double-rollback can't push the counter
        negative. Pure arithmetic — does not raise on its own.
        """
        cs = Web3.to_checksum_address(token)
        with self._lock:
            self._spent[cs] = max(0, self._spent.get(cs, 0) - int(amount))
