"""Pluggable off-chain storage for ServiceRecord persistence."""

from __future__ import annotations

from .config import StorageConfig
from .factory import create_storage_provider, storage_provider_from_env
from .interface import StorageProvider
from .local_provider import LocalStorageProvider

__all__ = [
    "StorageConfig",
    "StorageProvider",
    "LocalStorageProvider",
    "create_storage_provider",
    "storage_provider_from_env",
]

try:
    from .ipfs_provider import IPFSStorageProvider  # noqa: F401

    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
