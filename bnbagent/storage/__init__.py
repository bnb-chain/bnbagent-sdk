"""Pluggable off-chain storage for ServiceRecord persistence."""

from .interface import IStorageProvider
from .local_provider import LocalStorageProvider

__all__ = ["IStorageProvider", "LocalStorageProvider"]

try:
    from .ipfs_provider import IPFSStorageProvider
    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
