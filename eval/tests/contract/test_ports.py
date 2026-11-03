"""Port contract suite: every implementation (fakes, jsonrpc, bnbagent) behaves the same."""

from __future__ import annotations

import pytest

from bnbagent_eval.ports import (
    ChainReaderPort,
    CommercePort,
    DeliverableView,
    JobView,
    PolicyPort,
    RegistryPort,
    StoragePort,
)

from .port_worlds import VOTER, WORLDS

CLIENT = "0x" + "c1" * 20
PROVIDER = "0x" + "d2" * 20
REGISTRATION = {
    "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "name": "demo-agent",
    "description": "test agent",
    "services": [{"name": "web", "endpoint": "https://agent.example.com"}],
}


@pytest.fixture(params=sorted(WORLDS))
def world(request):
    return WORLDS[request.param]()


def need(world, *caps: str) -> None:
    missing = [c for c in caps if c not in world.capabilities]
    if missing:
        pytest.skip(f"{world.name} lacks {missing}")


def funded_job(job_id: int = 1) -> JobView:
    return JobView(
        job_id=job_id,
        client=CLIENT,
        provider=PROVIDER,
        status="funded",
        expired_at=2_000_000_000,
        description='{"task": "summarize"}',
    )


async def test_ports_satisfy_protocols(world) -> None:
    p = world.ports
    for port, proto in (
        (p.registry, RegistryPort),
        (p.commerce, CommercePort),
        (p.policy, PolicyPort),
        (p.storage, StoragePort),
        (p.reader, ChainReaderPort),
    ):
        if port is not None:
            assert isinstance(port, proto), (type(port), proto)


async def test_registry_read(world) -> None:
    need(world, "registry.read")
    world.add_agent(5, REGISTRATION, owner=CLIENT)
    reg = world.ports.registry
    assert (await reg.read_registration(5))["name"] == "demo-agent"
    assert (await reg.get_agent_uri(5)) is not None
    assert (await reg.read_registration(404)) is None
    assert (await reg.owner_of(5)).lower() == CLIENT.lower()


async def test_registry_write(world) -> None:
    need(world, "registry.read", "registry.write")
    world.add_agent(5, REGISTRATION, owner=CLIENT)
    reg = world.ports.registry
    tx = await reg.set_metadata(5, "eval.metrics.demo", "ipfs://x#0xabc")
    assert isinstance(tx, str) and tx
    assert await reg.get_metadata(5, "eval.metrics.demo") == "ipfs://x#0xabc"
    await reg.set_agent_uri(5, "data:application/json;base64,e30=")
    assert await reg.get_agent_uri(5) == "data:application/json;base64,e30="


async def test_commerce_read(world) -> None:
    need(world, "commerce.read")
    world.add_job(funded_job())
    job = await world.ports.commerce.get_job(1)
    assert (job.job_id, job.status) == (1, "funded")
    assert job.client.lower() == CLIENT.lower() and job.provider.lower() == PROVIDER.lower()
    assert await world.ports.commerce.get_deliverable(1) is None


async def test_commerce_submit_and_verify(world) -> None:
    need(world, "commerce.read", "commerce.write")
    world.add_job(funded_job())
    commerce = world.ports.commerce
    result = await commerce.submit_result(1, "the summary", {"eval": {"score": 1.0}})
    assert result.success and result.deliverable_url
    job = await commerce.get_job(1)
    assert job.status == "submitted" and job.deliverable
    deliverable = await commerce.get_deliverable(1)
    assert isinstance(deliverable, DeliverableView) and deliverable.verified
    assert deliverable.content == "the summary"
    assert deliverable.metadata["eval"] == {"score": 1.0}


async def test_commerce_submit_wrong_status(world) -> None:
    need(world, "commerce.read", "commerce.write")
    world.add_job(JobView(job_id=2, client=CLIENT, provider=PROVIDER, status="open"))
    result = await world.ports.commerce.submit_result(2, "x")
    assert not result.success and result.error_code


async def test_policy(world) -> None:
    need(world, "policy.read")
    policy = world.ports.policy
    assert await policy.dispute_window() > 0
    assert await policy.is_voter(VOTER) and not await policy.is_voter(PROVIDER)
    assert not await policy.is_disputed(1)
    if "policy.write" in world.capabilities:
        world.add_job(funded_job())
        await policy.dispute(1)
        assert await policy.is_disputed(1)


async def test_storage_roundtrip(world) -> None:
    need(world, "storage")
    storage = world.ports.storage
    uri = await storage.upload({"a": 1, "b": [1, 2]}, "doc.json")
    assert await storage.exists(uri)
    assert await storage.download(uri) == {"a": 1, "b": [1, 2]}


async def test_reader(world) -> None:
    need(world, "reader")
    tx = "0x" + "aa" * 32
    world.set_receipt(tx, {"status": 1, "gasUsed": 21000, "blockNumber": 900, "logs": []})
    world.set_balance(CLIENT, 5 * 10**18)
    reader = world.ports.reader
    receipt = await reader.get_receipt(tx)
    assert receipt["status"] == 1 and receipt["gasUsed"] == 21000
    assert await reader.get_receipt("0x" + "bb" * 32) is None
    assert await reader.get_balance(CLIENT) == 5 * 10**18
    assert isinstance(await reader.block_number(), int)
