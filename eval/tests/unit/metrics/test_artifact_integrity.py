"""format.artifact_integrity: hash, media type, size cap, fetch errors and the SSRF guard.

HTTP goes through ``httpx.MockTransport`` and a fake DNS resolver: no network.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import httpx
import pytest

from bnbagent_eval.hashing import canonical_json, keccak_hex
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.metrics.format.artifact_integrity import ArtifactIntegrity
from bnbagent_eval.net import FetchError, fetch_artifact, public_host_check
from bnbagent_eval.testing.fakes import MemoryStorage
from bnbagent_eval.types import Artifact, EvalCase, MetricConfig, MetricResult

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DNS = {"cdn.example.com": ["93.184.216.34"], "evil.example.com": ["10.0.0.5"]}


async def resolver(host: str, port: int) -> list[str]:
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/img.png":
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    if path == "/redirect-internal":
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    if path == "/redirect-ok":
        return httpx.Response(301, headers={"location": "/img.png"})
    if path == "/big":
        return httpx.Response(200, content=b"x" * 5000)
    if path == "/down":
        raise httpx.ConnectError("connection refused", request=request)
    return httpx.Response(404)


def metric(client: httpx.AsyncClient | None = None) -> ArtifactIntegrity:
    client = client or httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ArtifactIntegrity(client=client, resolver=resolver)


async def run(m: ArtifactIntegrity, *arts: Artifact, **params: Any) -> MetricResult:
    ctx = MetricContext(
        config=MetricConfig(id="format.artifact_integrity", params=params),
        method=ArtifactIntegrity.spec.method(),
        params=params,
    )
    return await m.evaluate(EvalCase(id="a", artifacts=arts), ctx)


def art(uri: str, content: bytes = PNG, media: str = "image/png", **kw: Any) -> Artifact:
    return Artifact(uri=uri, hash=keccak_hex(content), media_type=media, **kw)


def data_uri(content: bytes, media: str = "image/png") -> str:
    return f"data:{media};base64,{base64.b64encode(content).decode()}"


async def test_hash_match_and_mismatch() -> None:
    good = art(data_uri(PNG), size=len(PNG))
    bad = art(data_uri(PNG), content=b"tampered")
    res = await run(metric(), good, bad)
    assert res.score == 0.5 and not res.passed and res.threshold == 1.0
    rows = res.details["artifacts"]
    assert rows[0]["ok"] and rows[0]["bytes"] == len(PNG)
    assert rows[1]["problems"] == ["hash_mismatch"]


async def test_hash_formats_and_plain_data_uri() -> None:
    text = b"hello world"
    for declared in (
        keccak_hex(text).upper().replace("0X", "0x"),
        "keccak256:" + keccak_hex(text)[2:],
        "sha256:" + hashlib.sha256(text).hexdigest(),
    ):
        a = Artifact(uri="data:text/plain,hello%20world", hash=declared, media_type="text/plain")
        assert (await run(metric(), a)).score == 1.0, declared


async def test_size_declared_mismatch() -> None:
    res = await run(metric(), art(data_uri(PNG), size=3))
    assert res.details["artifacts"][0]["problems"] == ["size_mismatch"]


async def test_media_type_allow_list() -> None:
    a = art(data_uri(PNG))
    assert (await run(metric(), a, media_types=["image/*"])).score == 1.0
    res = await run(metric(), a, media_types=["application/pdf"])
    assert res.score == 0.0 and res.details["artifacts"][0]["problems"] == ["media_type"]


async def test_max_bytes_cap_data_and_http() -> None:
    res = await run(metric(), art(data_uri(PNG)), max_bytes=10)
    assert res.score == 0.0 and res.details["artifacts"][0]["problems"] == ["too_large"]
    big = art("https://cdn.example.com/big", content=b"x" * 5000)
    res = await run(metric(), big, max_bytes=1000)
    assert res.details["artifacts"][0]["problems"] == ["too_large"]
    assert (await run(metric(), big, max_bytes=5000)).score == 1.0


async def test_http_fetch_and_redirect() -> None:
    res = await run(metric(), art("https://cdn.example.com/img.png"))
    assert res.score == 1.0
    assert res.details["artifacts"][0]["served_media_type"] == "image/png"
    assert (await run(metric(), art("https://cdn.example.com/redirect-ok"))).score == 1.0


@pytest.mark.parametrize(
    ("uri", "code"),
    [
        ("https://cdn.example.com/down", "unreachable"),
        ("https://cdn.example.com/missing", "unreachable"),
        ("https://nowhere.example.com/x", "unreachable"),
        ("ipfs://bafy/x", "unsupported_scheme"),
        ("memory://abc/x", "unsupported_scheme"),  # no ctx.storage yet
        ("data:image/png;base64,***", "invalid_uri"),
    ],
)
async def test_fetch_failures(uri: str, code: str) -> None:
    res = await run(metric(), art(uri))
    assert res.status == "scored" and res.score == 0.0
    assert res.details["artifacts"][0]["problems"] == [code]


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.1.2.3/x",
        "http://192.168.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://evil.example.com/x",  # public name resolving to a private address
        "http://printer.local/x",
        "https://cdn.example.com/redirect-internal",  # redirect into the private network
    ],
)
async def test_ssrf_guard(uri: str) -> None:
    calls: list[str] = []

    def spy(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    m = metric(httpx.AsyncClient(transport=httpx.MockTransport(spy)))
    res = await run(m, art(uri))
    assert res.details["artifacts"][0]["problems"] == ["blocked_host"]
    assert all("127.0.0.1" not in c for c in calls)  # never connected to the private target


async def test_public_host_check_accepts_public_ip() -> None:
    await public_host_check("https://93.184.216.34/x")
    with pytest.raises(FetchError):
        await public_host_check("https:///x")


async def test_memory_scheme_through_storage() -> None:
    storage = MemoryStorage()
    doc = {"report": "ok"}
    uri = await storage.upload(doc, "r.json")
    content, media = await fetch_artifact(uri, storage=storage)
    assert content == canonical_json(doc).encode() and media == "application/json"
    assert keccak_hex(content)[2:] == uri.split("/")[2]
    with pytest.raises(FetchError) as err:
        await fetch_artifact("memory://nope/x", storage=storage)
    assert err.value.code == "unreachable"


async def test_missing_artifacts_skip() -> None:
    res = await run(metric())
    assert res.status == "skipped" and res.error.code == "missing_input"
