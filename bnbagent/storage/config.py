"""StorageConfig — unified configuration for storage providers."""

from __future__ import annotations

import os
from dataclasses import dataclass


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

        Env vars:
            STORAGE_PROVIDER: "local" or "ipfs" (default: "local")
            STORAGE_API_KEY: API key (fallback: PINATA_JWT)
            STORAGE_API_URL: Pinning API URL (optional)
            STORAGE_GATEWAY_URL: Gateway URL (fallback: PINATA_GATEWAY)
            LOCAL_STORAGE_PATH: Local storage directory (default: ".agent-data")
        """
        return cls(
            type=os.getenv("STORAGE_PROVIDER", "local").lower(),
            base_dir=os.getenv("LOCAL_STORAGE_PATH", ".agent-data"),
            api_key=os.getenv("STORAGE_API_KEY") or os.getenv("PINATA_JWT"),
            api_url=os.getenv("STORAGE_API_URL"),
            gateway_url=os.getenv("STORAGE_GATEWAY_URL") or os.getenv("PINATA_GATEWAY"),
        )
