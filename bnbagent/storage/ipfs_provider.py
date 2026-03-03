"""
IPFSStorageProvider — IPFS pinning service storage.

Uses HTTP API (Pinata/Infura/Web3.Storage) for upload and an IPFS gateway for download.
Requires `httpx` or `aiohttp` (optional dependency).
"""

import json

import httpx

from .interface import IStorageProvider


class IPFSStorageProvider(IStorageProvider):
    """
    IPFS storage via HTTP pinning API.

    Args:
        pinning_api_url: e.g. "https://api.pinata.cloud/pinning/pinJSONToIPFS"
        pinning_api_key: Bearer token or API key for the pinning service
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

    async def upload(self, data: dict) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"pinataContent": data}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                self._pinning_url, json=payload, headers=headers
            )
            resp.raise_for_status()
            result = resp.json()

        cid = result.get("IpfsHash") or result.get("cid")
        if not cid:
            raise ValueError(f"Unexpected pinning response: {result}")
        return f"ipfs://{cid}"

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

    @staticmethod
    def _extract_cid(url: str) -> str:
        if url.startswith("ipfs://"):
            return url[7:]
        if "/ipfs/" in url:
            return url.split("/ipfs/")[-1]
        return url
