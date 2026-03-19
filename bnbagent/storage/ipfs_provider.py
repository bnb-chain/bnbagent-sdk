"""
IPFSStorageProvider — IPFS pinning service storage.

Uses HTTP API (Pinata/Infura/Web3.Storage) for upload and an IPFS gateway for download.
Requires `httpx` (optional dependency).
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from ..exceptions import StorageError
from .interface import StorageProvider

logger = logging.getLogger(__name__)


class IPFSStorageProvider(StorageProvider):
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

    def save_sync(self, data: dict, filename: str | None = None) -> str:
        """
        Synchronous upload for callers that are NOT in an async context.

        Runs ``upload()`` in a new event loop on a background thread via
        ``concurrent.futures.ThreadPoolExecutor``.  This is safe to call from
        any synchronous code path regardless of whether an event loop exists
        on the current thread.

        **Async callers should use ``await upload()`` directly** — this method
        exists only for purely synchronous call sites.
        """
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self.upload(data, filename))
            return future.result()

    async def upload(self, data: dict, filename: str | None = None) -> str:
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
            resp = await client.post(self._pinning_url, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

        cid = result.get("IpfsHash") or result.get("cid")
        if not cid:
            raise StorageError(f"Unexpected pinning response: {result}")

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

    # CIDv0: Qm + 44 base58 chars; CIDv1: b + base32 (58+ chars)
    _CID_RE = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$")

    @classmethod
    def _extract_cid(cls, url: str) -> str:
        if url.startswith("ipfs://"):
            cid = url[7:]
        elif "/ipfs/" in url:
            cid = url.split("/ipfs/")[-1]
        else:
            cid = url
        cid = cid.strip("/")
        if not cls._CID_RE.match(cid):
            raise StorageError(f"Invalid IPFS CID format: {cid}")
        return cid
