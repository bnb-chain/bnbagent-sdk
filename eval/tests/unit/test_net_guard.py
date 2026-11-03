"""The egress guard in ``bnbagent_eval.net``: SSRF checks on IPv6 forms that tunnel to IPv4,
DNS pinning, response caps and fetch error paths."""

from __future__ import annotations

import httpx
import pytest

from bnbagent_eval.net import (
    FetchError,
    cap_response,
    decode_data_uri,
    fetch_artifact,
    is_public_ip,
    pin_request,
    public_host_check,
    public_transport,
)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("2606:4700::1111", True),
        ("::ffff:8.8.8.8", True),
        ("64:ff9b::808:808", True),  # NAT64 of a public address
        ("::8.8.8.8", True),  # IPv4-compatible form of a public address
        ("10.0.0.1", False),
        ("224.0.0.1", False),
        ("::ffff:10.0.0.1", False),  # IPv4-mapped private
        ("64:ff9b::a00:1", False),  # NAT64 -> 10.0.0.1
        ("::10.0.0.1", False),  # IPv4-compatible -> 10.0.0.1
        ("2002:a00:1::1", False),  # 6to4 -> 10.0.0.1
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),  # Teredo
        ("fe80::1%eth0", False),
        ("ff0e::1", False),
    ],
)
def test_public_address_classification(address: str, public: bool) -> None:
    assert is_public_ip(address) is public


async def _resolver_for(addresses):
    async def resolve(host, port):
        return list(addresses)

    return resolve


async def test_public_host_check_paths() -> None:
    public = await _resolver_for(["93.184.215.14"])
    assert await public_host_check("https://cdn.example.com/a", public) == ["93.184.215.14"]
    for url in ("http://localhost/x", "http://svc.internal/x", "http://box.local/x"):
        with pytest.raises(FetchError) as exc:
            await public_host_check(url, public)
        assert exc.value.code == "blocked_host"
    with pytest.raises(FetchError) as exc:
        await public_host_check("http:///nohost", public)
    assert exc.value.code == "invalid_uri"
    mixed = await _resolver_for(["93.184.215.14", "10.0.0.5"])  # one private answer is enough
    with pytest.raises(FetchError) as exc:
        await public_host_check("http://rebind.example.com/", mixed)
    assert exc.value.code == "blocked_host"

    async def failing(host, port):
        raise OSError("no such host")

    with pytest.raises(FetchError) as exc:
        await public_host_check("http://gone.example.com/", failing)
    assert exc.value.code == "unreachable"


async def test_pin_request_rewrites_host_and_keeps_sni() -> None:
    resolver = await _resolver_for(["93.184.215.14"])
    req = await pin_request(httpx.Request("GET", "https://cdn.example.com/a"), resolver)
    assert req.url.host == "93.184.215.14" and req.headers["Host"] == "cdn.example.com"
    assert req.extensions["sni_hostname"] == "cdn.example.com"
    literal = httpx.Request("GET", "http://93.184.215.14/a")
    assert (await pin_request(literal, resolver)).url.host == "93.184.215.14"
    with pytest.raises(FetchError):
        await pin_request(httpx.Request("GET", "ftp://cdn.example.com/a"), resolver)


async def test_cap_response_declared_and_streamed() -> None:
    req = httpx.Request("GET", "https://cdn.example.com/a")
    declared = cap_response(
        httpx.Response(200, headers={"content-length": "100"}, content=b"x" * 100), 10, req
    )
    with pytest.raises(httpx.ReadError):
        await declared.aread()
    streamed = cap_response(httpx.Response(200, content=b"y" * 50), 10, req)
    with pytest.raises(httpx.ReadError):
        await streamed.aread()
    small = cap_response(httpx.Response(200, content=b"ok"), 10, req)
    assert await small.aread() == b"ok"


async def test_public_transport_blocks_private_hosts() -> None:
    inner = httpx.MockTransport(lambda r: httpx.Response(200, content=b"body"))
    resolver = await _resolver_for(["10.1.2.3"])
    transport = public_transport(inner, resolver=resolver)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await client.get("https://internal.example.com/x")
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await client.get("http://10.0.0.1/x")
    ok = public_transport(
        inner, resolver=await _resolver_for(["93.184.215.14"]), max_response_bytes=2
    )
    async with httpx.AsyncClient(transport=ok) as client:
        with pytest.raises(httpx.ReadError):
            await client.get("https://cdn.example.com/x")  # 4 bytes > cap 2


def test_data_uri_errors() -> None:
    assert decode_data_uri("data:text/plain,hi%21") == (b"hi!", "text/plain")
    for bad in ("data:text/plain;base64", "data:;base64,@@@"):
        with pytest.raises(FetchError) as exc:
            decode_data_uri(bad)
        assert exc.value.code == "invalid_uri"


async def test_fetch_scheme_errors_and_default_client_guard() -> None:
    with pytest.raises(FetchError) as exc:
        await fetch_artifact("ftp://x/y")
    assert exc.value.code == "unsupported_scheme"
    with pytest.raises(FetchError) as exc:
        await fetch_artifact("memory://abc/doc.json")  # no storage port
    assert exc.value.code == "unsupported_scheme"
    with pytest.raises(FetchError) as exc:
        await fetch_artifact("data:,abcdef", cap=3)
    assert exc.value.code == "too_large"
    # The package's own client: the guard runs before any connection is attempted.
    with pytest.raises(FetchError) as exc:
        await fetch_artifact(
            "https://internal.example.com/a", resolver=await _resolver_for(["192.168.1.1"])
        )
    assert exc.value.code == "blocked_host"
