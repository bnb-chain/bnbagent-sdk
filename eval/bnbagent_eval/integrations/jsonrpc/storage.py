"""Read-only document fetching: ``data:`` URIs, HTTP(S) with an SSRF guard, IPFS via a gateway.

The guard rejects ``localhost`` and any host that is (or resolves to) a non-public address
(loopback, private, link-local, CGNAT, multicast, reserved). Redirects are followed manually
(a few hops) and re-checked. The configured IPFS gateway is trusted (operator configuration),
so a local gateway works; its redirects are still checked.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import socket
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import unquote

import httpx

from ...exceptions import ChainIntegrationError
from ...net import (
    FetchError,
    is_public_ip,
    pin_request,
    public_host_check,
)

Resolver = Callable[[str], Awaitable[Sequence[str]]]
DEFAULT_MAX_BYTES = 1_048_576  # deliverable manifests are capped at 256 KB by the SDK
MAX_REDIRECTS = 3


def read_only(what: str) -> ChainIntegrationError:
    return ChainIntegrationError(f"read_only: {what} is not supported", code="read_only")


async def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` to its IP addresses with the system resolver."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _port_resolver(resolver: Resolver) -> Callable[[str, int], Awaitable[list[str]]]:
    async def resolve(host: str, port: int) -> list[str]:
        return list(await resolver(host))

    return resolve


async def check_public_url(url: str, resolver: Resolver = system_resolver) -> None:
    """Raise ``ChainIntegrationError(code="blocked_url")`` unless ``url`` is public http(s).

    The same guard as every other egress path (``metrics.format.artifact_integrity``).
    """
    try:
        parsed = httpx.URL(url)
    except Exception as exc:  # httpx.InvalidURL and friends
        raise ChainIntegrationError(f"invalid URL {url!r}", code="blocked_url") from exc
    if parsed.scheme not in ("http", "https"):
        raise ChainIntegrationError(f"scheme {parsed.scheme!r} not allowed", code="blocked_url")
    try:
        await public_host_check(url, _port_resolver(resolver))
    except FetchError as exc:
        if exc.code == "unreachable":
            raise ChainIntegrationError(
                f"cannot resolve {parsed.host!r}", code="fetch_failed", retryable=True
            ) from exc
        raise ChainIntegrationError(
            f"host {parsed.host!r} is not public", code="blocked_url"
        ) from exc


def decode_data_uri(uri: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """Decode a JSON ``data:`` URI (base64 or percent-encoded)."""
    header, sep, payload = uri.partition(",")
    if not header.startswith("data:") or not sep:
        raise ChainIntegrationError("malformed data: URI", code="bad_document")
    params = header[len("data:") :].split(";")
    try:
        raw = (
            base64.b64decode(payload, validate=True)
            if "base64" in params[1:]
            else unquote(payload).encode()
        )
    except (binascii.Error, ValueError) as exc:
        raise ChainIntegrationError("malformed base64 in data: URI", code="bad_document") from exc
    if len(raw) > max_bytes:
        raise ChainIntegrationError("data: URI exceeds the size cap", code="too_large")
    return _json_object(raw, "data: URI")


def _json_object(raw: bytes, source: str) -> dict[str, Any]:
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ChainIntegrationError(f"{source} is not JSON", code="bad_document") from exc
    if not isinstance(doc, dict):
        raise ChainIntegrationError(f"{source} is not a JSON object", code="bad_document")
    return doc


class ReadOnlyStorage:
    """``StoragePort`` that downloads ``data:``, ``http(s)://`` and ``ipfs://`` documents."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        ipfs_gateway: str = "https://ipfs.io/ipfs/",
        max_bytes: int = DEFAULT_MAX_BYTES,
        resolver: Resolver = system_resolver,
        timeout: float = 15.0,
    ):
        # Our own client connects to the checked address (no DNS rebinding); an injected
        # client (tests, custom transports) is only checked, not pinned.
        self._pin = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=False)
        self.ipfs_gateway = ipfs_gateway if ipfs_gateway.endswith("/") else ipfs_gateway + "/"
        self.max_bytes = max_bytes
        self.resolver = resolver

    def gateway_url(self, uri: str) -> str:
        path = uri[len("ipfs://") :].lstrip("/")
        if path.startswith("ipfs/"):
            path = path[len("ipfs/") :]
        if not path:
            raise ChainIntegrationError("empty ipfs:// URI", code="bad_document")
        return self.ipfs_gateway + path

    async def upload(self, data: Mapping[str, Any], name: str) -> str:
        raise read_only("upload")

    async def download(self, uri: str) -> dict[str, Any]:
        if uri.startswith("data:"):
            return decode_data_uri(uri, max_bytes=self.max_bytes)
        if uri.startswith("ipfs://"):
            return await self.fetch_json(self.gateway_url(uri), trusted=True)
        if uri.startswith(("https://", "http://")):
            return await self.fetch_json(uri)
        raise ChainIntegrationError(
            f"unsupported URI scheme in {uri[:32]!r}", code="unsupported_uri"
        )

    async def exists(self, uri: str) -> bool:
        try:
            await self.download(uri)
        except ChainIntegrationError as exc:
            if exc.code == "not_found":
                return False
            raise
        return True

    async def fetch_json(self, url: str, *, trusted: bool = False) -> dict[str, Any]:
        """GET ``url`` (public hosts only unless ``trusted``) and parse a JSON object."""
        for _ in range(MAX_REDIRECTS + 1):
            if not trusted:
                await check_public_url(url, self.resolver)
            raw, location = await self._get(url, pin=self._pin and not trusted)
            if location is None:
                return _json_object(raw, url)
            url, trusted = location, False
        raise ChainIntegrationError(f"too many redirects for {url!r}", code="fetch_failed")

    async def _get(self, url: str, *, pin: bool = False) -> tuple[bytes, str | None]:
        request = self._client.build_request("GET", url)
        if pin:
            try:
                request = await pin_request(request, _port_resolver(self.resolver))
            except FetchError as exc:
                raise ChainIntegrationError(f"{url}: {exc}", code="blocked_url") from exc
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
            try:
                status = response.status_code
                if status in (301, 302, 303, 307, 308) and "location" in response.headers:
                    # join against the original URL: a pinned request's URL holds the IP
                    return b"", str(httpx.URL(url).join(response.headers["location"]))
                if status == 404:
                    raise ChainIntegrationError(f"{url}: not found", code="not_found")
                if status == 429 or status >= 500:
                    raise ChainIntegrationError(
                        f"{url}: HTTP {status}", code="fetch_failed", retryable=True
                    )
                if status >= 400:
                    raise ChainIntegrationError(f"{url}: HTTP {status}", code="fetch_failed")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_bytes:
                    raise ChainIntegrationError(f"{url}: exceeds the size cap", code="too_large")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise ChainIntegrationError(
                            f"{url}: exceeds the size cap", code="too_large"
                        )
                return bytes(body), None
            finally:
                await response.aclose()
        except httpx.TimeoutException as exc:
            raise ChainIntegrationError(
                f"{url}: timeout", code="fetch_failed", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise ChainIntegrationError(
                f"{url}: {type(exc).__name__}", code="fetch_failed", retryable=True
            ) from exc


__all__ = [
    "ReadOnlyStorage",
    "check_public_url",
    "decode_data_uri",
    "is_public_ip",
    "read_only",
    "system_resolver",
]
