"""StorageConfig — unified configuration for storage providers.

Env surface (module-scoped, ``STORAGE_`` prefix):
    STORAGE_PROVIDER    — "local" or "ipfs" (default: "local")
    STORAGE_LOCAL_PATH  — base dir for local provider (default: ".agent-data")
    STORAGE_API_KEY     — API key (e.g. Pinata JWT for IPFS)
    STORAGE_API_URL     — custom pin service URL (optional)
    STORAGE_GATEWAY_URL — HTTP gateway for reads
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import get_env

STORAGE_ENV_PREFIX = "STORAGE_"


@dataclass
class StorageConfig:
    """Configuration for storage providers.

    Supports local file storage, IPFS (via Pinata or compatible pinning services),
    and future providers (e.g., Greenfield).

    Usage:
        config = StorageConfig.from_env()
        config = StorageConfig(type="ipfs", api_key="...")
    """

    type: str = "local"  # "local" | "ipfs" | "gnfd" (future)
    base_dir: str = ".agent-data"
    # Generic storage service config
    api_key: str | None = None
    api_url: str | None = None
    gateway_url: str | None = None

    @classmethod
    def from_env(cls) -> StorageConfig:
        """Create config from environment variables.

        See module docstring for the full env surface. Legacy ``PINATA_*``
        fallbacks are no longer honoured — use the ``STORAGE_*`` keys.
        """
        return cls(
            type=(get_env("PROVIDER", "local", prefix=STORAGE_ENV_PREFIX) or "local").lower(),
            base_dir=get_env("LOCAL_PATH", ".agent-data", prefix=STORAGE_ENV_PREFIX)
            or ".agent-data",
            api_key=get_env("API_KEY", prefix=STORAGE_ENV_PREFIX),
            api_url=get_env("API_URL", prefix=STORAGE_ENV_PREFIX),
            gateway_url=get_env("GATEWAY_URL", prefix=STORAGE_ENV_PREFIX),
        )
