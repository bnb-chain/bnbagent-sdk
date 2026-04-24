"""
StorageProvider — pluggable off-chain storage interface.

Implementations handle upload/download of deliverable JSON.
The chain only stores hashes; full data lives off-chain.

Async/Sync Design
-----------------
The primary interface is **async** (upload, download, exists) because storage
I/O (HTTP calls to IPFS pinning services, etc.) is naturally async.

Implementations MAY also provide a ``save_sync()`` convenience method for use
by callers that are not running inside an async context. ``save_sync()`` is
*not* part of the abstract interface because the async ``upload()`` method is
the canonical API — async callers should use ``upload()`` directly.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from web3 import Web3


class StorageProvider(ABC):
    """
    Abstract base for pluggable off-chain storage.

    All core methods are async.  Callers running in an async context (e.g.
    APEXJobOps) should ``await upload()`` directly.  A synchronous
    ``save_sync()`` helper may be provided by concrete implementations for
    use from purely synchronous code paths.
    """

    @abstractmethod
    async def upload(self, data: dict, filename: str | None = None) -> str:
        """
        Upload JSON data.  Returns a URL (ipfs://..., file://..., etc.).

        This is the canonical upload API — prefer this over ``save_sync()``.

        Args:
            data: JSON-serializable dict to upload
            filename: Optional filename hint (e.g., "job-123.json")
        """
        ...

    @abstractmethod
    async def download(self, url: str) -> dict:
        """Download and parse JSON data from a URL."""
        ...

    @abstractmethod
    async def exists(self, url: str) -> bool:
        """Check whether data at the given URL exists."""
        ...

    @staticmethod
    def compute_hash(data: dict) -> bytes:
        """Compute keccak256 of canonical JSON for on-chain verification."""
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return Web3.keccak(text=canonical)

    @staticmethod
    def compute_content_hash(content: str) -> bytes:
        """Compute keccak256 of raw content string (for requestHash / responseHash)."""
        return Web3.keccak(text=content)
