"""Guarded network access: public hosts only, pinned connections, capped downloads.

Used wherever the package fetches a URL it did not choose: artifacts, deliverables and suites
named on chain, targets and datasets on the eval service. Private, loopback, link-local,
reserved and tunnelled (NAT64, 6to4, Teredo, IPv4-mapped) addresses are rejected before
connecting and on every redirect; the connection goes to the address that passed the check
(no DNS rebinding); bodies are capped. Also: ``data:`` decoding and declared-hash checks.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_to_bytes, urljoin, urlsplit

from .hashing import canonical_json, keccak_hex

if TYPE_CHECKING:
    import httpx

__all__ = [
    "DEFAULT_FETCH_CAP",
    "FetchError",
    "Resolver",
    "cap_response",
    "decode_data_uri",
    "fetch_artifact",
    "hash_matches",
    "is_public_ip",
    "pin_request",
    "public_host_check",
    "public_transport",
]

DEFAULT_FETCH_CAP = 10 * 1024 * 1024  # bytes, when ``max_bytes`` is not set
MAX_REDIRECTS = 3
TIMEOUT_S = 10.0

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


async def _system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPAT = ipaddress.ip_network("::/96")


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address tunnels to (mapped, compatible, NAT64, 6to4, Teredo)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64 or (ip in _V4_COMPAT and int(ip) > 1):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    return None


def is_public_ip(ip_text: str) -> bool:
    """Global unicast, after unwrapping IPv4-in-IPv6 forms; ``False`` for anything else."""
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            ip_text.split("%")[0]
        )
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if not (ip.is_global and not ip.is_multicast):
            return False
        embedded = _embedded_v4(ip)
        if embedded is None:
            return True
        ip = embedded
    return ip.is_global and not ip.is_multicast


async def public_host_check(url: str, resolver: Resolver | None = None) -> list[str]:
    """Raise ``FetchError("blocked_host")`` unless every address of the URL's host is public.

    Returns the checked addresses (connect to one of them to avoid DNS rebinding).
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("invalid_uri", f"no host in {url!r}")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise FetchError("blocked_host", f"host {host!r} is not public")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            addresses = await (resolver or _system_resolver)(host, port)
        except OSError as exc:
            raise FetchError("unreachable", f"cannot resolve {host!r}: {exc}") from exc
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise FetchError("blocked_host", f"host {host!r} resolves to a non-public address")
    return addresses


async def pin_request(request: httpx.Request, resolver: Resolver | None = None) -> httpx.Request:
    """Check ``request``'s host and pin the connection to a checked address.

    The URL host is replaced by the checked IP (the ``Host`` header and, for https, the TLS SNI
    and certificate name keep the original host), so a second DNS answer (rebinding) between
    the check and the connect cannot redirect the connection to a private address.
    """
    if request.url.scheme not in ("http", "https"):
        raise FetchError("blocked_host", f"scheme {request.url.scheme!r} is not allowed")
    addresses = await public_host_check(str(request.url), resolver)
    host = request.url.host
    try:
        ipaddress.ip_address(host)
        return request  # an IP literal: nothing to resolve again
    except ValueError:
        pass
    ip = addresses[0].split("%")[0]
    if "host" not in request.headers:
        request.headers["Host"] = request.url.netloc.decode("ascii")
    request.url = request.url.copy_with(host=ip)
    if request.url.scheme == "https":
        request.extensions = {**request.extensions, "sni_hostname": host}
    return request


def cap_response(response: httpx.Response, cap: int, request: httpx.Request) -> httpx.Response:
    """Wrap ``response`` (as returned by a transport) so its body cannot exceed ``cap`` bytes."""
    import httpx

    length = response.headers.get("content-length")
    declared_too_large = bool(length and length.isdigit() and int(length) > cap)
    inner = response.stream

    class _Capped(httpx.AsyncByteStream):
        async def __aiter__(self):
            if declared_too_large:  # raised on read, so the client still closes the stream
                raise httpx.ReadError(f"content-length {length} > {cap} bytes", request=request)
            size = 0
            async for chunk in inner:  # type: ignore[union-attr]
                size += len(chunk)
                if size > cap:
                    raise httpx.ReadError(f"response exceeds {cap} bytes", request=request)
                yield chunk

        async def aclose(self) -> None:
            close = getattr(inner, "aclose", None)
            if close is not None:
                await close()

    return httpx.Response(
        response.status_code,
        headers=response.headers,
        stream=_Capped(),
        extensions=response.extensions,
        request=request,
    )


def public_transport(
    inner: httpx.AsyncBaseTransport | None = None,
    *,
    resolver: Resolver | None = None,
    max_response_bytes: int | None = None,
) -> httpx.AsyncBaseTransport:
    """An httpx transport that applies the public-IP guard (with DNS pinning) to every request.

    Pinning is applied when ``inner`` is a real network transport (the default); injected test
    transports see the original URL. ``max_response_bytes`` caps every response body.
    """
    import httpx

    class _PublicTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)
            self.pin = isinstance(self.inner, httpx.AsyncHTTPTransport)

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            try:
                if self.pin:
                    request = await pin_request(request, resolver)
                else:
                    if request.url.scheme not in ("http", "https"):
                        raise FetchError("blocked_host", f"scheme {request.url.scheme!r}")
                    await public_host_check(str(request.url), resolver)
            except FetchError as exc:
                raise httpx.ConnectError(f"blocked: {exc}", request=request) from exc
            response = await self.inner.handle_async_request(request)
            if max_response_bytes is not None:
                response = cap_response(response, max_response_bytes, request)
            return response

        async def aclose(self) -> None:
            await self.inner.aclose()

    return _PublicTransport()


def decode_data_uri(uri: str) -> tuple[bytes, str | None]:
    header, sep, payload = uri[len("data:") :].partition(",")
    if not sep:
        raise FetchError("invalid_uri", "malformed data: URI")
    fields = header.split(";")
    media = fields[0] or None
    if "base64" in fields[1:]:
        try:
            return base64.b64decode(payload, validate=True), media
        except ValueError as exc:
            raise FetchError("invalid_uri", f"bad base64 in data: URI: {exc}") from exc
    return unquote_to_bytes(payload), media


async def _fetch_http(
    url: str, client: httpx.AsyncClient, cap: int, resolver: Resolver | None
) -> tuple[bytes, str | None]:
    import httpx

    for _ in range(MAX_REDIRECTS + 1):
        await public_host_check(url, resolver)
        try:
            async with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect and "location" in resp.headers:
                    url = urljoin(url, resp.headers["location"])
                    if urlsplit(url).scheme not in ("http", "https"):
                        raise FetchError("blocked_host", "redirect to a non-http(s) URI")
                    continue
                if resp.status_code >= 400:
                    raise FetchError("unreachable", f"HTTP {resp.status_code}")
                length = resp.headers.get("content-length")
                if length and length.isdigit() and int(length) > cap:
                    raise FetchError("too_large", f"content-length {length} > {cap} bytes")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > cap:
                        raise FetchError("too_large", f"content exceeds {cap} bytes")
                    chunks.append(chunk)
                media = resp.headers.get("content-type")
                return b"".join(chunks), media.split(";")[0].strip() if media else None
        except httpx.HTTPError as exc:
            raise FetchError("unreachable", f"{type(exc).__name__}: {exc}") from exc
    raise FetchError("unreachable", "too many redirects")


async def fetch_artifact(
    uri: str,
    *,
    cap: int = DEFAULT_FETCH_CAP,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
    storage: Any = None,
) -> tuple[bytes, str | None]:
    """Return ``(content, served media type)``; raise :class:`FetchError` on failure."""
    scheme = urlsplit(uri).scheme.lower()
    if scheme == "data":
        content, media = decode_data_uri(uri)
    elif scheme == "memory":
        if storage is None:
            raise FetchError("unsupported_scheme", "memory:// needs a StoragePort (ctx.storage)")
        try:
            doc = await storage.download(uri)
        except (FileNotFoundError, KeyError) as exc:
            raise FetchError("unreachable", f"not found: {uri}") from exc
        content, media = canonical_json(doc).encode(), "application/json"
    elif scheme in ("http", "https"):
        if client is None:
            import httpx

            transport = public_transport(resolver=resolver)
            async with httpx.AsyncClient(
                timeout=TIMEOUT_S, trust_env=False, transport=transport
            ) as own:
                return await _fetch_http(uri, own, cap, resolver)
        return await _fetch_http(uri, client, cap, resolver)
    else:
        raise FetchError("unsupported_scheme", f"unsupported URI scheme {scheme!r}")
    if len(content) > cap:
        raise FetchError("too_large", f"content exceeds {cap} bytes")
    return content, media


def hash_matches(declared: str, content: bytes) -> bool:
    text = declared.strip().lower()
    if text.startswith("sha256:"):
        return hashlib.sha256(content).hexdigest() == text.removeprefix("sha256:")
    digest = text.removeprefix("keccak256:").removeprefix("0x")
    return keccak_hex(content)[2:] == digest
