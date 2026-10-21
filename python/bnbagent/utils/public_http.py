"""Bounded, direct HTTP reads for untrusted public URLs (not operator RPC config).

Uses explicit address ranges for consistent behavior across Python versions.
Only native global IPv6 unicast (plus validated IPv4-mapped addresses) is
accepted. NAT64, 6to4 and Teredo are deliberately excluded: an embedded or
translated destination must not bypass the IPv4 policy. Private deployments
also need egress controls for locally routed public prefixes.

Reference: https://www.iana.org/assignments/iana-ipv6-special-registry/
"""

from __future__ import annotations

import concurrent.futures
import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

_V4_BLOCKED = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)
_V6_GLOBAL = ipaddress.ip_network("2000::/3")
_V6_BLOCKED = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "2001::/23",
        "2001:db8::/32",
        "2002::/16",
        "3fff::/20",
    )
)
_BLOCKED_HOSTS = {"metadata.google.internal", "metadata.goog"}
_CID = re.compile(r"(?:Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})\Z")
# A timed-out resolver keeps its slot until it actually finishes. Never create
# unbounded threads/queued work, or wait for executor shutdown on the hot path.
_DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-dns")
_DNS_SLOTS = threading.BoundedSemaphore(4)


class PublicHttpError(ValueError):
    """A public download was refused or could not complete within its limits."""


def is_public_ip(address: str) -> bool:
    if "%" in address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address):
        return not any(ip in subnet for subnet in _V4_BLOCKED)
    return ip in _V6_GLOBAL and not any(ip in subnet for subnet in _V6_BLOCKED)


def public_gateway_url(url: str, gateway: str) -> str:
    """Expand a bare IPFS CID, rejecting paths, queries and authority injection."""
    if not url.startswith("ipfs://"):
        return url
    cid = url[7:]
    if len(cid) > 128 or not _CID.fullmatch(cid):
        raise PublicHttpError("Invalid IPFS CID")
    parsed = urlsplit(gateway)
    if parsed.query or parsed.fragment:
        raise PublicHttpError("Gateway must not contain a query or fragment")
    return f"{gateway.rstrip('/')}/{cid}"


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicHttpError("Public download timed out")
    return remaining


def _resolve(host: str, port: int, deadline: float) -> str:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise PublicHttpError("Public DNS resolver is busy")
        try:
            future = _DNS_POOL.submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        except Exception:
            _DNS_SLOTS.release()
            raise
        future.add_done_callback(lambda _: _DNS_SLOTS.release())
        answers = future.result(timeout=min(5.0, _remaining(deadline)))
        addresses = [answer[4][0] for answer in answers]
    if not addresses or any(not is_public_ip(ip) for ip in addresses):
        raise PublicHttpError("Public URL resolves to a disallowed address")
    return addresses[0]


def fetch_public_json(url: str, *, max_bytes: int = 1024 * 1024, timeout: float = 15.0) -> dict:
    """Fetch one JSON object; no redirects, proxies, credentials or decompression.

    DNS, connect, TLS and streamed reads share an overall deadline. The socket
    dials the validated IP directly; TLS still verifies the original hostname.
    Errors are deliberately generic: do not leak URLs/tokens in caller logs.
    """
    if (
        not isinstance(max_bytes, int)
        or max_bytes <= 0
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise PublicHttpError("Invalid public download limits")
    conn = None
    sock = None
    timer = None
    try:
        if len(url) > 4096 or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
            raise PublicHttpError("Invalid public URL")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or "%" in host
            or parsed.username is not None
            or parsed.password is not None
            or host in _BLOCKED_HOSTS
        ):
            raise PublicHttpError("Invalid public URL")
        port = (
            parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        )
        if not 1 <= port <= 65535:
            raise PublicHttpError("Invalid public URL port")
        deadline = time.monotonic() + timeout
        ip = _resolve(host, port, deadline)
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_remaining(deadline))
        sock.connect((ip, port))
        if parsed.scheme == "https":
            sock.settimeout(_remaining(deadline))
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)

        # A socket inactivity timeout alone lets peers drip-feed headers or
        # chunks forever. Interrupt the socket at the overall deadline.
        def expire():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(_remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        conn = http.client.HTTPConnection(host, port)
        conn.sock = sock
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            authority += f":{port}"
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        sock.settimeout(_remaining(deadline))
        conn.request(
            "GET",
            path,
            headers={
                "Host": authority,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        sock.settimeout(_remaining(deadline))
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            raise PublicHttpError("Public download returned a non-success status")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise PublicHttpError("Encoded public responses are not supported")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > max_bytes):
            raise PublicHttpError("Public response exceeds the size limit")
        body = bytearray()
        while not response.isclosed():
            sock.settimeout(_remaining(deadline))
            chunk = response.read1(min(8192, max_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise PublicHttpError("Public response exceeds the size limit")
        if response.length not in (None, 0):
            raise PublicHttpError("Public response was incomplete")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise PublicHttpError("Public response must be a JSON object")
        return data
    except PublicHttpError:
        raise
    except Exception:
        raise PublicHttpError("Public download failed") from None
    finally:
        if timer is not None:
            timer.cancel()
        if conn is not None:
            conn.close()
        if sock is not None:
            sock.close()
