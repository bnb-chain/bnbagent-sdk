"""Run the SDK's ``funded_job_watcher`` with an evaluation gate as ``on_funded``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from bnbagent.erc8183.job_ops import ERC8183JobOps, funded_job_watcher

from ._convert import plain_job_dict

__all__ = ["Gate", "watch"]

Gate = Callable[[dict[str, Any]], Awaitable["dict[str, Any] | bool | None"]]


async def watch(
    gate: Gate,
    *,
    network: Any = "bsc-testnet",
    wallet: Any | None = None,
    provider_address: str | None = None,
    job_ops: Any | None = None,
    interval: float = 30.0,
    stop: asyncio.Event | None = None,
    max_attempts: int = 5,
    **job_ops_kwargs: Any,
) -> None:
    """Poll funded jobs and call ``await gate(job)`` for each verified one.

    Watcher semantics: ``gate`` returning ``False`` or ``{"retry": True}`` (or raising) retries
    the job on a later tick, up to ``max_attempts``; anything else (incl. ``None``) is final.
    The job dict keeps the SDK's camelCase keys with ``status`` as our literal (``"funded"``).
    Pass ``job_ops`` to reuse one, else it is built from ``wallet`` or ``provider_address``
    (poll-only, no signing).
    """
    if job_ops is None:
        job_ops = ERC8183JobOps(
            wallet, network, provider_address=provider_address, **job_ops_kwargs
        )

    # A plain coroutine function: the watcher runs non-coroutine callables in a thread and
    # would never await an object whose __call__ is async (EvalGate).
    async def on_funded(job: dict[str, Any]) -> Any:
        return await gate(plain_job_dict(job))

    await funded_job_watcher(
        job_ops, on_funded, interval=interval, stop=stop, max_attempts=max_attempts
    )
