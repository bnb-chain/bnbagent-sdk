"""Synchronous upload utility for storage providers."""

from __future__ import annotations

import asyncio
import concurrent.futures


def upload_sync(provider, data: dict, filename: str | None = None) -> str:
    """Synchronous upload wrapper for any StorageProvider.

    Safe to call from non-async contexts regardless of whether
    an event loop exists on the current thread.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, provider.upload(data, filename))
        return future.result()
