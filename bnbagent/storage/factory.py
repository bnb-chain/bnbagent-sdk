"""Storage provider factory for bnbagent SDK."""
import os
from pathlib import Path
from typing import Optional

from .interface import IStorageProvider
from .local_provider import LocalStorageProvider
from .ipfs_provider import IPFSStorageProvider


def create_storage_provider(
    provider_type: str = "local",
    local_path: str = ".agent-data",
    pinata_jwt: Optional[str] = None,
    pinata_gateway: str = "https://gateway.pinata.cloud/ipfs/",
) -> IStorageProvider:
    """
    Create storage provider based on configuration.
    
    Args:
        provider_type: "local" or "ipfs"
        local_path: Path for local storage
        pinata_jwt: JWT for Pinata (required if ipfs)
        pinata_gateway: IPFS gateway URL
    
    Returns:
        IStorageProvider instance
    """
    if provider_type == "ipfs":
        if not pinata_jwt:
            raise ValueError("PINATA_JWT required for IPFS storage")
        return IPFSStorageProvider(
            pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
            pinning_api_key=pinata_jwt,
            gateway_url=pinata_gateway,
        )
    return LocalStorageProvider(base_dir=local_path)


def storage_provider_from_env(
    local_path: str = ".agent-data",
) -> Optional[IStorageProvider]:
    """
    Create storage provider from environment variables.
    
    Reads:
        - STORAGE_PROVIDER: "local" or "ipfs" (default: "local")
        - PINATA_JWT: Required if STORAGE_PROVIDER=ipfs
        - PINATA_GATEWAY: Optional gateway URL
    
    Returns:
        IStorageProvider or None if configuration invalid
    """
    provider_type = os.getenv("STORAGE_PROVIDER", "local").lower()
    
    if provider_type == "ipfs":
        pinata_jwt = os.getenv("PINATA_JWT")
        if not pinata_jwt:
            return None
        return IPFSStorageProvider(
            pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
            pinning_api_key=pinata_jwt,
            gateway_url=os.getenv("PINATA_GATEWAY", "https://gateway.pinata.cloud/ipfs/"),
        )
    
    return LocalStorageProvider(base_dir=local_path)
