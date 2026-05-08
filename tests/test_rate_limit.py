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
