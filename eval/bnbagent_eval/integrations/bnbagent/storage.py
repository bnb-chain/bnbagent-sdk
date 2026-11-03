"""``StoragePort`` over an SDK ``StorageProvider``, plus URI resolution (data / https / ipfs)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote_to_bytes

from bnbagent.utils.public_http import fetch_public_json, public_gateway_url

from ...exceptions import ChainIntegrationError
from ._errors import INVALID_REQUEST, STORAGE_ERROR, to_chain_error

DEFAULT_IPFS_GATEWAY = "https://gateway.pinata.cloud/ipfs/"  # the SDK's IPFS provider default

__all__ = ["DEFAULT_IPFS_GATEWAY", "BnbagentStorage", "UriResolver", "decode_data_uri"]


def decode_data_uri(uri: str) -> dict[str, Any]:
    """Decode a JSON ``data:`` URI (base64 or percent-encoded)."""
    header, sep, payload = uri.partition(",")
    if not sep or not header.startswith("data:"):
        raise ChainIntegrationError("malformed data: URI", code=INVALID_REQUEST)
    try:
        if header.endswith(";base64"):
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote_to_bytes(payload)
        doc = json.loads(raw)
    except Exception as exc:
        raise ChainIntegrationError(
            f"undecodable data: URI ({type(exc).__name__})", code=INVALID_REQUEST
        ) from exc
    if not isinstance(doc, dict):
        raise ChainIntegrationError("data: URI is not a JSON object", code=INVALID_REQUEST)
    return doc


class UriResolver:
    """Read a JSON document from a URI.

    ``data:`` is decoded locally; ``http(s)://`` goes through the SDK's SSRF-guarded
    ``fetch_public_json``; ``ipfs://`` goes through the storage provider when it is not a
    ``file://`` provider, else through ``ipfs_gateway``; anything else goes to the provider.
    """

    def __init__(
        self,
        provider: Any | None = None,
        *,
        ipfs_gateway: str = DEFAULT_IPFS_GATEWAY,
        fetch_json: Callable[[str], dict] = fetch_public_json,
    ):
        self.provider = provider
        self.ipfs_gateway = ipfs_gateway
        self._fetch_json = fetch_json

    async def read(self, uri: str) -> dict[str, Any]:
        if uri.startswith("data:"):
            return decode_data_uri(uri)
        try:
            if uri.startswith(("https://", "http://")):
                return await self._fetch(uri)
            if uri.startswith("ipfs://"):
                if self.provider is not None and not getattr(
                    self.provider, "uses_file_url", False
                ):
                    return await self._download(uri)
                return await self._fetch(public_gateway_url(uri, self.ipfs_gateway))
            if self.provider is None:
                raise ChainIntegrationError(
                    f"cannot resolve a {uri.split(':', 1)[0]}: URI without storage",
                    code=STORAGE_ERROR,
                )
            return await self._download(uri)
        except Exception as exc:
            raise to_chain_error(exc, op="read uri") from exc

    async def _fetch(self, url: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_json, url)

    async def _download(self, uri: str) -> dict[str, Any]:
        doc = await self.provider.download(uri)
        if not isinstance(doc, dict):
            raise ChainIntegrationError("stored document is not a JSON object", code=STORAGE_ERROR)
        return doc


class BnbagentStorage:
    """``StoragePort`` over an SDK ``StorageProvider`` (already async; no thread hop)."""

    def __init__(self, provider: Any, *, resolver: UriResolver | None = None):
        self.provider = provider
        self.resolver = resolver or UriResolver(provider)

    async def upload(self, data: Mapping[str, Any], name: str) -> str:
        try:
            return str(await self.provider.upload(dict(data), name))
        except Exception as exc:
            raise to_chain_error(exc, op="storage upload") from exc

    async def download(self, uri: str) -> dict[str, Any]:
        return await self.resolver.read(uri)

    async def exists(self, uri: str) -> bool:
        if uri.startswith("data:"):
            return True
        try:
            return bool(await self.provider.exists(uri))
        except Exception as exc:
            raise to_chain_error(exc, op="storage exists") from exc
