"""
IPFSStorageProvider — IPFS pinning service storage.

Uses HTTP API (Pinata/Infura/Web3.Storage) for upload and an IPFS gateway for download.
Requires `httpx` (optional dependency).
"""

import asyncio
import json
import logging
from typing import Optional

import httpx

from .interface import IStorageProvider

logger = logging.getLogger(__name__)


class IPFSStorageProvider(IStorageProvider):
    """
    IPFS storage via HTTP pinning API.

    Args:
        pinning_api_url: e.g. "https://api.pinata.cloud/pinning/pinJSONToIPFS"
        pinning_api_key: Bearer token (JWT) for the pinning service
        gateway_url: e.g. "https://gateway.pinata.cloud/ipfs/"
    """

    def __init__(
        self,
        pinning_api_url: str,
        pinning_api_key: str,
        gateway_url: str = "https://gateway.pinata.cloud/ipfs/",
    ):
        self._pinning_url = pinning_api_url
        self._api_key = pinning_api_key
        self._gateway = gateway_url.rstrip("/")

    def save_sync(self, data: dict, filename: Optional[str] = None) -> str:
        """
        Synchronous upload — compatible with ACPJobOps.submit_result.

        Wraps the async upload() method for use in synchronous contexts.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.upload(data, filename))
                return future.result()
        else:
            return asyncio.run(self.upload(data, filename))

    async def upload(self, data: dict, filename: Optional[str] = None) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Use provided filename or extract from job.id
        if filename:
            pin_name = filename.replace(".json", "")
        else:
            job_data = data.get("job", {})
            job_id = job_data.get("id") if isinstance(job_data, dict) else None
            pin_name = f"job-{job_id}" if job_id else "deliverable"

        payload = {
            "pinataContent": data,
            "pinataMetadata": {"name": pin_name},
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                self._pinning_url, json=payload, headers=headers
            )
            resp.raise_for_status()
            result = resp.json()

        cid = result.get("IpfsHash") or result.get("cid")
        if not cid:
            raise ValueError(f"Unexpected pinning response: {result}")

        ipfs_url = f"ipfs://{cid}"
        logger.info(f"[IPFSStorageProvider] Uploaded {pin_name} to {ipfs_url}")
        return ipfs_url

    async def download(self, url: str) -> dict:
        cid = self._extract_cid(url)
        gateway_url = f"{self._gateway}/{cid}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(gateway_url)
            resp.raise_for_status()
            return resp.json()

    async def exists(self, url: str) -> bool:
        cid = self._extract_cid(url)
        gateway_url = f"{self._gateway}/{cid}"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.head(gateway_url)
                return resp.status_code == 200
            except httpx.HTTPError:
                return False

    def get_gateway_url(self, ipfs_url: str) -> str:
        """Convert ipfs:// URL to HTTP gateway URL for browser access."""
        cid = self._extract_cid(ipfs_url)
        return f"{self._gateway}/{cid}"

    @staticmethod
    def _extract_cid(url: str) -> str:
        if url.startswith("ipfs://"):
            return url[7:]
        if "/ipfs/" in url:
            return url.split("/ipfs/")[-1]
        return url
