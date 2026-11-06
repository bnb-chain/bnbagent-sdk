from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from web3 import Web3

from bnbagent.erc8183.schema import DeliverableManifest
from bnbagent.erc8183.types import JobStatus
from tests.test_erc8183_job_ops import COMMERCE, ME, _inject_client, _make_ops


def manifest(job_id=1):
    return DeliverableManifest(
        version=1,
        job_id=job_id,
        chain_id=97,
        contracts={"commerce": COMMERCE},
        response={"content": "ready"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["content", "job", "chain", "commerce", None])
async def test_response_verifies_commitment_and_job_context(tamper):
    original = manifest()
    data = original.to_dict()
    if tamper == "content":
        data["response"] = {"content": "altered"}
    if tamper == "job":
        data["job_id"] = 2
    if tamper == "chain":
        data["chain_id"] = 56
    if tamper == "commerce":
        data["contracts"] = {"commerce": "0x" + "33" * 20}
    storage = SimpleNamespace(download=AsyncMock(return_value=data))
    ops = _make_ops(storage=storage)
    client = _inject_client(ops)
    client.commerce.address = COMMERCE
    client.get_deliverable_url.return_value = "ipfs://committed"
    # Context mismatches remain invalid even if this foreign manifest's hash is committed.
    committed = (
        DeliverableManifest.from_dict(data).manifest_hash()
        if tamper != "content"
        else original.manifest_hash()
    )
    ops.get_job = AsyncMock(
        return_value={
            "success": True,
            "provider": ME,
            "status": JobStatus.SUBMITTED,
            "deliverable": Web3.to_hex(committed),
        }
    )
    result = await ops.get_response(1)
    assert bool(result.get("success")) == (tamper is None)
