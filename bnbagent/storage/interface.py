"""
IStorageProvider — pluggable off-chain storage interface.

Implementations handle upload/download of ServiceRecord JSON.
The chain only stores hashes; full data lives off-chain.
"""

import json
from abc import ABC, abstractmethod
from typing import Optional

from web3 import Web3


class IStorageProvider(ABC):
    """Abstract base for pluggable off-chain storage."""

    @abstractmethod
    async def upload(self, data: dict, filename: Optional[str] = None) -> str:
        """
        Upload JSON data. Returns a URL (ipfs://..., file://..., etc.).
        
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
