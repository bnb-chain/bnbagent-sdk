"""In-memory sliding-window rate limiter used by public APEX endpoints.

The agent server's `/negotiate` endpoint signs negotiation hashes with the
provider's wallet on every accepted request. Without throttling, any caller
can drive arbitrary signing work and accumulate signed quotes; this limiter
caps the per-IP rate to bound that abuse without breaking marketplace
discovery.

Trade-offs (intentional, single-replica scope):
- In-memory state: counters are not shared across replicas. Multi-replica
  deployments effectively get N × the per-replica limit; that is acceptable
  while horizontal scaling itself raises the cost of an attack.
- No background eviction: stale buckets are pruned lazily on the next
  ``check`` for that key. Memory growth is bounded by the number of
  distinct client IPs seen within a window.
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import HTTPException


class SlidingWindowLimiter:
    """Per-key sliding-window rate limiter.

    Allows up to ``max_requests`` events per ``window_seconds`` for any
    given key. Raises ``HTTPException(429)`` once the budget is exhausted.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = {}

    @property
    def max_requests(self) -> int:
        return self._max

    @property
    def window_seconds(self) -> float:
        return self._window

    def check(self, key: str) -> None:
        """Record a hit for ``key`` or raise 429 if the window is full."""
        now = time.monotonic()
        bucket = self._buckets.setdefault(key, deque())
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
            )
        bucket.append(now)
