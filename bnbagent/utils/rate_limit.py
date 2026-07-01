"""In-memory sliding-window rate limiter for public agent endpoints.

A public `/negotiate`-style endpoint signs negotiation hashes with the
provider's wallet on every accepted request. Without throttling, any caller
can drive arbitrary signing work and accumulate signed quotes; this limiter
caps the per-IP rate to bound that abuse without breaking marketplace
discovery.

The limiter is transport-agnostic: it raises :class:`RateLimitExceeded`,
and the serving layer (FastAPI route, MCP tool, A2A handler, ...) converts
that into its own protocol's rejection (e.g. HTTP 429).

Trade-offs (intentional, single-replica scope):
- In-memory state: counters are not shared across replicas. Multi-replica
  deployments effectively get N × the per-replica limit; that is acceptable
  while horizontal scaling itself raises the cost of an attack.
- Memory growth is hard-capped by ``max_keys``; least-recently-used keys
  are evicted once the cap is exceeded. This prevents an IPv6 address-cycling
  attack from growing the limiter's memory without bound.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque


class RateLimitExceeded(Exception):
    """Raised by :meth:`SlidingWindowLimiter.check` when a key's window is full.

    Transport-agnostic on purpose — the serving layer maps it to its own
    rejection (HTTP 429, MCP error, ...).
    """


class SlidingWindowLimiter:
    """Per-key sliding-window rate limiter with LRU key eviction.

    Allows up to ``max_requests`` events per ``window_seconds`` for any
    given key. Raises :class:`RateLimitExceeded` once the budget is exhausted.
    The number of tracked keys is hard-capped at ``max_keys``; once exceeded,
    the least-recently-used key is evicted to reclaim memory.
    """

    def __init__(self, max_requests: int, window_seconds: float, max_keys: int = 10_000) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if max_keys <= 0:
            raise ValueError("max_keys must be > 0")
        self._max = max_requests
        self._window = window_seconds
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def max_requests(self) -> int:
        return self._max

    @property
    def window_seconds(self) -> float:
        return self._window

    @property
    def max_keys(self) -> int:
        return self._max_keys

    def check(self, key: str) -> None:
        """Record a hit for ``key`` or raise ``RateLimitExceeded`` if the window is full."""
        now = time.monotonic()
        if key in self._buckets:
            # Move to end to mark as most-recently used
            self._buckets.move_to_end(key)
            bucket = self._buckets[key]
        else:
            bucket = deque()
            self._buckets[key] = bucket
            # Evict least-recently-used key if cap is exceeded
            if len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
        cutoff = now - self._window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max:
            raise RateLimitExceeded("Too many requests")
        bucket.append(now)
