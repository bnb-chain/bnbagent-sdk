"""Pluggable off-chain storage for ServiceRecord persistence."""

from .interface import IStorageProvider
from .local_provider import LocalStorageProvider
from .factory import create_storage_provider, storage_provider_from_env

__all__ = [
    "IStorageProvider",
    "LocalStorageProvider",
    "create_storage_provider",
    "storage_provider_from_env",
]

try:
    from .ipfs_provider import IPFSStorageProvider
    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
