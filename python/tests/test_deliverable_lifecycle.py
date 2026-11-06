from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
async def test_failed_submission_file_needs_chain_commit_before_retrieval():
    storage = SimpleNamespace(download=AsyncMock(return_value=manifest().to_dict()))
    ops = _make_ops(storage=storage)
    client = _inject_client(ops)
    client.commerce.address = COMMERCE
    ops._deliverable_urls[1] = "ipfs://staged"
    ops.get_job = AsyncMock(
        return_value={"success": True, "provider": ME, "status": JobStatus.FUNDED}
    )
    assert (await ops.get_response(1))["error_code"] == "not_found"
    storage.download.assert_not_called()


@pytest.mark.asyncio
async def test_failed_submit_never_publishes_its_staged_url():
    storage = SimpleNamespace(upload=AsyncMock(return_value="ipfs://staged"), download=AsyncMock())
    ops = _make_ops(storage=storage)
    client = _inject_client(ops)
    client.commerce.address = COMMERCE
    client.policy.address = ME
    ops.verify_job = AsyncMock(return_value={"valid": True})
    client.submit.side_effect = RuntimeError("submit failed")
    assert not (await ops.submit_result(1, "private"))["success"]
    assert 1 not in ops._deliverable_urls
