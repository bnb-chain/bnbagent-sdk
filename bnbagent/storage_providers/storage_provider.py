"""
StorageProvider — pluggable off-chain storage interface.

Implementations handle upload/download of deliverable JSON.
The chain only stores hashes; full data lives off-chain.

The primary interface is **async** (upload, download, exists) because storage
I/O (HTTP calls to IPFS pinning services, etc.) is naturally async.  For
synchronous callers use ``bnbagent.storage_providers.upload_sync(provider, data)``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from web3 import Web3


class StorageProvider(ABC):
    """Abstract base for pluggable off-chain storage.

    All core methods are async.  Use ``await upload()`` from async contexts
    (e.g. APEXJobOps).  For synchronous callers use
    ``bnbagent.storage_providers.upload_sync(provider, data)``.

    Built-in implementations (``LocalStorageProvider``, ``IPFSStorageProvider``)
    each provide a ``from_env()`` classmethod that reads their own env vars.
    Custom backends subclass this ABC and inject via
    ``APEXConfig(storage=MyStorage(...))``.
    """

    @abstractmethod
    async def upload(self, data: dict, filename: str | None = None) -> str:
        """Upload JSON data.  Returns a URL (ipfs://..., file://..., https://...).

        The URL must be reachable by client/voter unless the agent is running
        with ``APEX_AGENT_URL`` configured (which routes through the agent's
        own ``/job/{id}/response`` endpoint for file:// or empty URLs).

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
