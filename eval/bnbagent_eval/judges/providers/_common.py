"""Shared provider helpers: retries, error classification, concurrency limits."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ...exceptions import JudgeNonRetryable, JudgeThrottled

T = TypeVar("T")

_CONTEXT_OVERFLOW_HINTS = (
    "context length",
    "context_length",
    "too many tokens",
    "maximum context",
    "prompt is too long",
    "input is too long",
)


def classify_http_error(status: int, body: str) -> Exception:
    """Map an HTTP error to a retryable / non-retryable judge error."""
    lowered = body.lower()
    if any(h in lowered for h in _CONTEXT_OVERFLOW_HINTS):
        return JudgeNonRetryable(f"context overflow: {body[:200]}")
    if status == 429 or status >= 500:
        return JudgeThrottled(f"HTTP {status}: {body[:200]}")
    return JudgeNonRetryable(f"HTTP {status}: {body[:200]}")


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Retry ``fn`` on :class:`JudgeThrottled` with jittered exponential backoff."""
    for attempt in range(attempts):
        try:
            return await fn()
        except JudgeThrottled:
            if attempt == attempts - 1:
                raise
            await sleep(base_delay * (2**attempt) * (1 + random.random() * 0.25))
    raise AssertionError("unreachable")


def rejects_temperature(message: str) -> bool:
    """Whether a provider error says the model does not accept ``temperature``."""
    m = message.lower()
    return "temperature" in m and any(
        w in m for w in ("deprecated", "not supported", "unsupported", "not allowed", "invalid")
    )


def rejects_forced_tool(message: str) -> bool:
    """Whether a provider error says the model cannot be forced to call a specific tool."""
    m = message.lower()
    return ("tool_choice" in m or "toolchoice" in m) and any(
        w in m for w in ("not supported", "unsupported", "not allowed", "invalid")
    )


def tool_reply(calls: list[tuple[str | None, object]], tool_name: str) -> object:
    """The structured reply among a response's tool calls ``[(name, input), ...]``.

    The first call of ``tool_name`` wins. With auto tool choice some models (seen live with
    Claude Sonnet on Bedrock) call an *action named in the prompt* as if it were a tool,
    e.g. ``read_artifact({"name": ...})`` instead of ``submit_verdict({"action":
    "read_artifact", "args": {...}})``; that call is mapped to ``{"action", "args"}``.
    """
    for name, payload in calls:
        if name == tool_name or not name:  # unnamed: a stub / proxy that drops the name
            return payload
    for name, payload in calls:
        if name:
            return {"action": name, "args": payload if isinstance(payload, dict) else {}}
    return None


class InFlightLimit:
    """Per-provider cap on concurrent judge requests."""

    def __init__(self, limit: int = 8):
        self._sem = asyncio.Semaphore(limit)

    async def __aenter__(self) -> None:
        await self._sem.acquire()

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()
