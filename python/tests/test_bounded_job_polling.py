import pytest

from tests.test_erc8183_job_ops import _inject_client, _make_ops


@pytest.mark.asyncio
async def test_global_job_counter_never_allocates_or_fetches_history_at_once():
    ops = _make_ops()
    client = _inject_client(ops)
    client.commerce.job_counter.return_value = 2**100
    client.commerce.get_jobs_batch.return_value = []
    await ops.get_pending_jobs()
    assert client.commerce.get_jobs_batch.call_args.args[0] == list(range(1, 257))
    await ops.get_pending_jobs()
    assert client.commerce.get_jobs_batch.call_args.args[0] == list(range(257, 513))
