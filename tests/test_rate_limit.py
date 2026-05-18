"""Tests for the in-memory sliding-window rate limiter used on /negotiate."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from bnbagent.erc8183.server.rate_limit import SlidingWindowLimiter


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit(self):
        limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            limiter.check("1.2.3.4")

    def test_rejects_over_limit_for_same_key(self):
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60.0)
        limiter.check("1.2.3.4")
        limiter.check("1.2.3.4")
        with pytest.raises(HTTPException) as exc:
            limiter.check("1.2.3.4")
        assert exc.value.status_code == 429

    def test_keys_are_independent(self):
        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)
        limiter.check("1.2.3.4")
        limiter.check("5.6.7.8")  # different key, fresh budget
        with pytest.raises(HTTPException):
            limiter.check("1.2.3.4")

    def test_window_recovers_after_expiry(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=10.0)
        limiter.check("ip")
        with pytest.raises(HTTPException):
            limiter.check("ip")

        clock[0] += 11.0  # past the 10s window
        limiter.check("ip")  # bucket is pruned, allowed again

    def test_invalid_construction_args_rejected(self):
        with pytest.raises(ValueError):
            SlidingWindowLimiter(max_requests=0, window_seconds=60.0)
        with pytest.raises(ValueError):
            SlidingWindowLimiter(max_requests=10, window_seconds=0)

    # ── LRU max_keys tests ──

    def test_max_keys_validation(self):
        with pytest.raises(ValueError):
            SlidingWindowLimiter(max_requests=10, window_seconds=60.0, max_keys=0)
        with pytest.raises(ValueError):
            SlidingWindowLimiter(max_requests=10, window_seconds=60.0, max_keys=-1)

    def test_unique_keys_bounded_by_max_keys(self):
        limiter = SlidingWindowLimiter(max_requests=100, window_seconds=60.0, max_keys=5)
        for i in range(20):
            limiter.check(f"ip-{i}")
        assert len(limiter._buckets) == 5

    def test_lru_evicts_oldest_when_max_keys_exceeded(self):
        limiter = SlidingWindowLimiter(max_requests=100, window_seconds=60.0, max_keys=3)
        limiter.check("a")
        limiter.check("b")
        limiter.check("c")
        assert "a" in limiter._buckets
        # Insert 4th key — "a" (LRU) should be evicted
        limiter.check("d")
        assert "a" not in limiter._buckets
        assert len(limiter._buckets) == 3
        # "a" having been evicted, it can accept a new hit (budget reset)
        limiter.check("a")

    def test_recent_access_resets_lru_position(self):
        limiter = SlidingWindowLimiter(max_requests=100, window_seconds=60.0, max_keys=3)
        limiter.check("a")
        limiter.check("b")
        # Re-access "a" — it moves to most-recently-used, so "b" is now LRU
        limiter.check("a")
        limiter.check("c")
        # Insert 4th key — "b" (LRU) should be evicted, not "a"
        limiter.check("d")
        assert "b" not in limiter._buckets
        assert "a" in limiter._buckets
        assert len(limiter._buckets) == 3
