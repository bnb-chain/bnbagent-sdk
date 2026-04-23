"""Storage provider factory for bnbagent SDK."""

from __future__ import annotations

from .config import StorageConfig
from .interface import StorageProvider
from .local_provider import LocalStorageProvider


def create_storage_provider(config: StorageConfig) -> StorageProvider:
    """Create storage provider based on configuration.

    Args:
        config: StorageConfig instance (required).

    Returns:
        StorageProvider instance
    """
    if config.type == "ipfs":
        if not config.api_key:
            raise ValueError("api_key (STORAGE_API_KEY) required for IPFS storage")
        from .ipfs_provider import IPFSStorageProvider

        return IPFSStorageProvider(
            pinning_api_url=config.api_url or "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            pinning_api_key=config.api_key,
            gateway_url=config.gateway_url or "https://gateway.pinata.cloud/ipfs/",
        )
    elif config.type == "gnfd":
        raise NotImplementedError("Greenfield storage coming soon")

    return LocalStorageProvider(base_dir=config.base_dir)


def storage_provider_from_env(
    local_path: str = ".agent-data",
) -> StorageProvider | None:
    """Create storage provider from environment variables.

    Reads:
        STORAGE_PROVIDER: "local" or "ipfs" (default: "local")
        STORAGE_API_KEY: Required if STORAGE_PROVIDER=ipfs
        STORAGE_GATEWAY_URL: Optional gateway URL

    Returns:
        StorageProvider or None if configuration invalid
    """
    config = StorageConfig.from_env()
    config.base_dir = local_path or config.base_dir

    if config.type == "ipfs" and not config.api_key:
        return None

    return create_storage_provider(config)
