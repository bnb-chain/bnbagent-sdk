"""Run budget: atomic reservations of judge calls / tokens / USD."""

from __future__ import annotations

import threading

from ..types.options import Budget
from ..types.result import Cost


class BudgetTracker:
    def __init__(self, budget: Budget | None = None):
        self.budget = budget or Budget()
        self._lock = threading.Lock()
        self.judge_calls = 0
        self.tokens = 0
        self.usd = 0.0

    def reserve(self, *, judge_calls: int = 0, tokens: int = 0, usd: float = 0.0) -> bool:
        """Reserve resources; returns False (reserving nothing) if any limit would be exceeded."""
        b = self.budget
        with self._lock:
            if (
                b.max_judge_calls is not None
                and self.judge_calls + judge_calls > b.max_judge_calls
            ):
                return False
            if b.max_tokens is not None and self.tokens + tokens > b.max_tokens:
                return False
            if b.max_usd is not None and self.usd + usd > b.max_usd:
                return False
            self.judge_calls += judge_calls
            self.tokens += tokens
            self.usd += usd
            return True

    def cost(self) -> Cost:
        return Cost(judge_calls=self.judge_calls, tokens=self.tokens, usd_estimate=self.usd)
