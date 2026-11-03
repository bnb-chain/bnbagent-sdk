"""integrations.jsonrpc: client, reader, read-only contract ports and guarded storage."""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("eth_abi")

from bnbagent_eval.chain.manifest import build_manifest  # noqa: E402
from bnbagent_eval.exceptions import ChainIntegrationError  # noqa: E402
from bnbagent_eval.integrations import jsonrpc  # noqa: E402
from bnbagent_eval.integrations.jsonrpc import abi  # noqa: E402
from bnbagent_eval.integrations.jsonrpc.client import (  # noqa: E402
    JsonRpcClient,
    block_param,
    from_hex,
    to_hex,
)
from bnbagent_eval.integrations.jsonrpc.storage import (  # noqa: E402
    ReadOnlyStorage,
    check_public_url,
    decode_data_uri,
    is_public_ip,
)
from bnbagent_eval.ports import JobView  # noqa: E402
from bnbagent_eval.testing.fakes import encode_data_uri  # noqa: E402
from tests.contract.port_worlds_jsonrpc import (  # noqa: E402
    ADDRESSES,
    RPC_URL,
    FakeNode,
    public_resolver,
)

CLIENT = "0x" + "c1" * 20
PROVIDER = "0x" + "d2" * 20
TOKEN = "0x" + "e3" * 20
VOTER = "0x" + "77" * 20


def make(node: FakeNode | None = None, **kwargs):
    node = node or FakeNode()
    kwargs.setdefault("resolver", public_resolver)
    ports = jsonrpc.ports(
        RPC_URL, chain_id=97, addresses=ADDRESSES, client=node.client(), **kwargs
    )
    return node, ports


def rpc_with(handler, **kwargs) -> JsonRpcClient:
    return JsonRpcClient(
        RPC_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs
    )


def funded(job_id: int = 1) -> JobView:
    return JobView(
        job_id=job_id,
        client=CLIENT,
        provider=PROVIDER,
        status="funded",
        expired_at=2_000_000_000,
        description='{"task": "summarize"}',
        budget=10**18,
    )


def manifest(job_id: int = 1, content: str = "the summary") -> dict:
    return build_manifest(
        job_id=job_id,
        chain_id=97,
        contracts={"commerce": ADDRESSES["commerce"]},
        content=content,
        metadata={"eval": {"score": 1.0}},
    )


# ----------------------------------------------------------------------------- hex / blocks


def test_hex_conversions() -> None:
    assert to_hex(0) == "0x0" and to_hex(26) == "0x1a"
    assert from_hex("0x1a") == 26 and from_hex("0x") == 0 and from_hex(7) == 7
    for bad in (-1, True, 1.5):
        with pytest.raises(ValueError):
            to_hex(bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        from_hex("12")
    assert block_param(None) == "latest"
    assert block_param(None, 255) == "0xff"
    assert block_param("finalized") == "finalized"
    assert block_param("0x0A") == "0xa"
    with pytest.raises(ValueError):
        block_param("yesterday")


async def test_receipt_and_transaction_hex_to_int() -> None:
    node, ports = make()
    tx = "0x" + "aa" * 32
    node.receipts[tx] = {
        "status": "0x1",
        "gasUsed": "0x5208",
        "blockNumber": "0x384",
        "from": CLIENT,
        "to": PROVIDER,
        "logs": [{"logIndex": "0x2", "data": "0x00", "topics": []}],
    }
    node.transactions[tx] = {"value": "0xde0b6b3a7640000", "nonce": "0x3", "input": "0x"}
    receipt = await ports.reader.get_receipt(tx)
    assert (receipt["status"], receipt["gasUsed"], receipt["blockNumber"]) == (1, 21000, 900)
    assert receipt["from"] == CLIENT and receipt["to"] == PROVIDER
    assert receipt["logs"][0]["logIndex"] == 2 and receipt["logs"][0]["data"] == "0x00"
    txn = await ports.reader.get_transaction(tx)
    assert txn["value"] == 10**18 and txn["nonce"] == 3 and txn["input"] == "0x"
    assert await ports.reader.get_transaction("0x" + "bb" * 32) is None
    assert await ports.reader.block_number() == 1000


async def test_pinned_block_and_balances() -> None:
    node, ports = make(block=777)
    node.balances[(CLIENT, None, 777)] = 5
    node.balances[(CLIENT, None, 10)] = 1
    node.balances[(CLIENT, TOKEN, 777)] = 42
    node.balances[(CLIENT, TOKEN, 10)] = 2
    reader = ports.reader
    assert await reader.get_balance(CLIENT) == 5
    assert await reader.get_balance(CLIENT, block=10) == 1
    assert await reader.get_balance(CLIENT, token=TOKEN) == 42
    assert await reader.get_balance(CLIENT, token=TOKEN, block=10) == 2
    assert await reader.block_number() == 777
    blocks = [p[-1] for m, p in node.requests if m in ("eth_getBalance", "eth_call")]
    assert blocks == ["0x309", "0xa", "0x309", "0xa"]
    assert not any(m == "eth_blockNumber" for m, _ in node.requests)
    # eth_call passthrough uses the pinned block too
    data = abi.load(abi.ERC20).encode_call("balanceOf", CLIENT)
    assert int(await reader.call(TOKEN, data), 16) == 42
    assert node.requests[-1][1][1] == "0x309"


# ----------------------------------------------------------------------------- errors


def _error(code: int, message: str):
    def handler(request: httpx.Request) -> httpx.Response:
        rid = json.loads(request.content)["id"]
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
        )

    return handler


@pytest.mark.parametrize(
    ("handler", "code", "retryable"),
    [
        (_error(-32602, "invalid params"), "rpc_error", False),
        (_error(3, "execution reverted"), "reverted", False),
        (_error(-32005, "query returned more than 10000 results"), "rpc_limit", True),
        (lambda r: httpx.Response(429), "rate_limited", True),
        (lambda r: httpx.Response(503), "http_error", True),
        (lambda r: httpx.Response(403), "http_error", False),
        (lambda r: httpx.Response(200, content=b"<html>"), "bad_response", False),
        (lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1}), "bad_response", False),
    ],
)
async def test_error_mapping(handler, code: str, retryable: bool) -> None:
    with pytest.raises(ChainIntegrationError) as info:
        await rpc_with(handler).eth_blockNumber()
    assert (info.value.code, info.value.retryable) == (code, retryable)


@pytest.mark.parametrize(
    ("exc", "code"),
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("refused"), "transport")],
)
async def test_transport_errors_are_retryable(exc: Exception, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ChainIntegrationError) as info:
        await rpc_with(handler).eth_getBalance(CLIENT)
    assert info.value.code == code and info.value.retryable


async def test_revert_on_missing_agent_is_none_but_rpc_errors_raise() -> None:
    _, ports = make()
    assert await ports.registry.get_agent_uri(9) is None
    assert await ports.registry.owner_of(9) is None
    registry = jsonrpc.ports(
        RPC_URL,
        chain_id=97,
        addresses=ADDRESSES,
        client=httpx.AsyncClient(transport=httpx.MockTransport(_error(-32000, "boom"))),
    ).registry
    with pytest.raises(ChainIntegrationError):
        await registry.get_agent_uri(9)


# ----------------------------------------------------------------------------- logs


async def test_log_range_chunking() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "eth_blockNumber":
            result = "0xfa"  # 250
        else:
            query = body["params"][0]
            seen.append((query["fromBlock"], query["toBlock"]))
            assert query["address"] == ADDRESSES["policy"]
            result = [{"blockNumber": query["fromBlock"], "logIndex": "0x0", "topics": []}]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    rpc = rpc_with(handler, max_log_range=100)
    reader = jsonrpc.JsonRpcReader(rpc, chain_id=97)
    logs = await reader.get_logs({"address": ADDRESSES["policy"], "fromBlock": 0})
    assert seen == [("0x0", "0x63"), ("0x64", "0xc7"), ("0xc8", "0xfa")]
    assert [log["blockNumber"] for log in logs] == [0, 100, 200]

    seen.clear()
    await reader.get_logs({"address": ADDRESSES["policy"], "fromBlock": 10, "toBlock": 109})
    assert seen == [("0xa", "0x6d")]
    seen.clear()
    await rpc_with(handler, max_log_range=None).eth_getLogs(
        {"address": ADDRESSES["policy"], "fromBlock": 0, "toBlock": "latest"}
    )
    assert seen == [("0x0", "latest")]


# ----------------------------------------------------------------------------- registry


REGISTRATION = {"name": "demo-agent", "services": []}


async def test_registration_data_https_ipfs() -> None:
    node, ports = make(ipfs_gateway="https://gateway.example/ipfs")
    reg = ports.registry
    node.uris[1] = encode_data_uri(REGISTRATION)
    node.uris[2] = "https://agents.example/2.json"
    node.put_document("https://agents.example/2.json", {**REGISTRATION, "name": "https-agent"})
    node.uris[3] = "ipfs://bafyexamplecid/agent.json"
    node.put_document(
        "https://gateway.example/ipfs/bafyexamplecid/agent.json",
        {**REGISTRATION, "name": "ipfs-agent"},
    )
    node.uris[4] = "data:application/json," + "%7B%22name%22%3A%22plain%22%7D"
    assert (await reg.read_registration(1))["name"] == "demo-agent"
    assert (await reg.read_registration(2))["name"] == "https-agent"
    assert (await reg.read_registration(3))["name"] == "ipfs-agent"
    assert (await reg.read_registration(4))["name"] == "plain"
    assert await reg.read_registration(404) is None
    node.uris[5] = "ftp://agents.example/5.json"
    with pytest.raises(ChainIntegrationError) as info:
        await reg.read_registration(5)
    assert info.value.code == "unsupported_uri"


async def test_metadata_and_owner() -> None:
    node, ports = make()
    node.uris[5] = encode_data_uri(REGISTRATION)
    node.owners[5] = CLIENT
    node.metadata[(5, "eval.metrics.demo")] = b"ipfs://x#0xabc"
    assert await ports.registry.get_metadata(5, "eval.metrics.demo") == "ipfs://x#0xabc"
    assert await ports.registry.get_metadata(5, "absent") is None
    assert await ports.registry.owner_of(5) == CLIENT


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/agent.json",
        "http://10.0.0.1/agent.json",
        "http://localhost/agent.json",
        "http://api.localhost/agent.json",
        "http://[::1]/agent.json",
        "http://169.254.169.254/latest/meta-data",
        "http://[::ffff:192.168.1.1]/x",
        "file:///etc/passwd",
    ],
)
async def test_ssrf_guard_rejects(url: str) -> None:
    node, ports = make()
    node.uris[7] = url
    node.put_document(url, REGISTRATION)
    with pytest.raises(ChainIntegrationError) as info:
        await ports.registry.read_registration(7)
    assert info.value.code in ("blocked_url", "unsupported_uri")
    assert node.http_gets == []


async def test_ssrf_guard_dns_and_redirects() -> None:
    async def private(host: str) -> list[str]:
        return ["10.1.2.3"] if host == "internal.example" else ["93.184.216.34"]

    with pytest.raises(ChainIntegrationError, match="not public"):
        await check_public_url("https://internal.example/x", private)
    await check_public_url("https://agents.example/x", private)
    assert is_public_ip("8.8.8.8") and not is_public_ip("100.64.0.1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    storage = ReadOnlyStorage(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
    )
    with pytest.raises(ChainIntegrationError) as info:
        await storage.download("https://agents.example/r.json")
    assert info.value.code == "blocked_url"


async def test_size_cap_and_bad_documents() -> None:
    node, ports = make(max_document_bytes=64)
    node.uris[1] = "https://agents.example/big.json"
    node.put_document("https://agents.example/big.json", {"pad": "x" * 200})
    with pytest.raises(ChainIntegrationError) as info:
        await ports.registry.read_registration(1)
    assert info.value.code == "too_large"
    with pytest.raises(ChainIntegrationError):
        decode_data_uri("data:application/json;base64,!!!")
    with pytest.raises(ChainIntegrationError):
        decode_data_uri(encode_data_uri({"a": 1}), max_bytes=4)
    node.put_document("https://agents.example/list.json", b"[1, 2]")
    with pytest.raises(ChainIntegrationError) as info:
        await ports.storage.download("https://agents.example/list.json")
    assert info.value.code == "bad_document"
    assert not await ports.storage.exists("https://agents.example/missing.json")


# ----------------------------------------------------------------------------- commerce


async def test_get_job_fields_and_not_found() -> None:
    node, ports = make()
    node.set_job(funded())
    job = await ports.commerce.get_job(1)
    assert job == JobView(
        job_id=1,
        client=CLIENT,
        provider=PROVIDER,
        status="funded",
        expired_at=2_000_000_000,
        description='{"task": "summarize"}',
        budget=10**18,
    )
    for i, status in enumerate(("open", "funded", "submitted", "completed", "rejected")):
        node.set_job(JobView(job_id=10 + i, client=CLIENT, provider=PROVIDER, status=status))
        assert (await ports.commerce.get_job(10 + i)).status == status
    with pytest.raises(ChainIntegrationError) as info:
        await ports.commerce.get_job(404)
    assert info.value.code == "not_found"


async def test_deliverable_verified_and_tampered() -> None:
    node, ports = make()
    node.set_job(funded())
    url = "https://store.example/job-1.json"
    doc = manifest()
    digest = node.submit(1, doc, url, block=900)
    node.put_document(url, doc)
    commerce = ports.commerce
    job = await commerce.get_job(1)
    assert job.status == "submitted" and job.deliverable == digest
    assert await commerce.get_deliverable_url(1) == url
    query = next(p[0] for m, p in node.requests if m == "eth_getLogs")
    assert (query["fromBlock"], query["toBlock"]) == ("0x37a", "0x38e")  # 890..910
    deliverable = await commerce.get_deliverable(1)
    assert deliverable.verified and deliverable.manifest_hash == digest
    assert deliverable.content == "the summary" and deliverable.metadata == {
        "eval": {"score": 1.0}
    }

    node.put_document(url, manifest(content="tampered"))
    tampered = await commerce.get_deliverable(1)
    assert not tampered.verified and tampered.content == "tampered"


async def test_deliverable_url_edge_cases() -> None:
    node, ports = make()
    node.set_job(funded(1))
    assert await ports.commerce.get_deliverable_url(1) is None  # not submitted
    node.set_job(funded(2))
    node.submit(2, manifest(2), "unused", opt_params=b"not json")
    assert await ports.commerce.get_deliverable_url(2) is None
    node.set_job(funded(3))
    node.submit(3, manifest(3), "https://store.example/3.json")
    node.logs.clear()
    with pytest.raises(ChainIntegrationError) as info:
        await ports.commerce.get_deliverable_url(3)
    assert info.value.code == "log_missing" and info.value.retryable


async def test_router_resolves_job_policy() -> None:
    other = "0x" + "cc" * 20
    addresses = {**ADDRESSES, "router": "0x" + "aa" * 20}
    node = FakeNode(addresses=addresses)
    ports = jsonrpc.ports(
        RPC_URL, chain_id=97, addresses=addresses, client=node.client(), resolver=public_resolver
    )
    node.set_job(funded())
    node.job_policies[1] = other
    node.submit(1, manifest(), "https://store.example/1.json", policy=other)
    assert await ports.commerce.get_deliverable_url(1) == "https://store.example/1.json"


# ----------------------------------------------------------------------------- policy / writes


async def test_policy_reads() -> None:
    node, ports = make()
    node.voters.add(VOTER)
    node.disputed.add(1)
    node.votes[1] = {VOTER}
    policy = ports.policy
    assert await policy.dispute_window() == 3600
    assert await policy.vote_window() is None
    assert await policy.is_disputed(1) and not await policy.is_disputed(2)
    assert await policy.is_voter(VOTER) and not await policy.is_voter(PROVIDER)
    assert await policy.has_voted(1, VOTER) and not await policy.has_voted(2, VOTER)


async def test_writes_refused() -> None:
    node, ports = make()
    node.set_job(funded())
    for call in (
        ports.registry.set_agent_uri(1, "data:,"),
        ports.registry.set_metadata(1, "k", "v"),
        ports.policy.dispute(1),
        ports.policy.vote_reject(1),
        ports.storage.upload({"a": 1}, "a.json"),
    ):
        with pytest.raises(ChainIntegrationError) as info:
            await call
        assert info.value.code == "read_only" and "read_only" in str(info.value)
    result = await ports.commerce.submit_result(1, "x")
    assert not result.success and result.error_code == "read_only"
    assert not any(m.startswith("eth_send") for m, _ in node.requests)


def test_ports_requires_addresses() -> None:
    with pytest.raises(ValueError, match="policy"):
        jsonrpc.ports(
            RPC_URL, chain_id=97, addresses={"identity_registry": "0x1", "commerce": "0x2"}
        )


def test_abi_selectors_match_known_values() -> None:
    assert abi.load(abi.ERC20).selector("balanceOf") == "0x70a08231"
    assert abi.load(abi.IDENTITY).selector("ownerOf") == "0x6352211e"
    assert abi.load(abi.IDENTITY).selector("tokenURI") == "0xc87b56dd"


@pytest.mark.parametrize(
    "address", ["64:ff9b::a00:1", "2002:a00:1::1", "::ffff:10.0.0.1", "100.64.0.1"]
)
def test_is_public_ip_unwraps_ipv4_in_ipv6(address: str) -> None:
    # the same guard as the service and artifact fetches (NAT64 / 6to4 / mapped unwrapped)
    assert not is_public_ip(address)


async def test_ssrf_guard_rejects_internal_names() -> None:
    with pytest.raises(ChainIntegrationError) as info:
        await check_public_url("https://metadata.internal/x", public_resolver)
    assert info.value.code == "blocked_url"


async def test_downloader_pins_the_checked_address() -> None:
    """No DNS rebinding: our own client connects to the address that passed the check."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"]))
        if request.url.path == "/r.json":
            return httpx.Response(302, headers={"location": "/final.json"})
        return httpx.Response(200, json={"ok": True})

    storage = ReadOnlyStorage(resolver=public_resolver)
    assert storage._pin
    storage._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await storage.download("https://agents.example/r.json") == {"ok": True}
    addresses = await public_resolver("agents.example")
    assert seen == [(addresses[0], "agents.example")] * 2


def test_ports_downloader_has_its_own_client() -> None:
    ports = jsonrpc.ports(
        "http://127.0.0.1:8545",
        chain_id=97,
        addresses={"identity_registry": "0x1", "commerce": "0x2", "policy": "0x3"},
    )
    assert ports.storage._pin  # the RPC client may reach a local node; documents may not
