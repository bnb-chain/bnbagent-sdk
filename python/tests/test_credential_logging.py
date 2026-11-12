from unittest.mock import MagicMock, patch


import pytest


from tests.test_erc8183_job_ops import _inject_client, _make_ops


@pytest.mark.asyncio
async def test_rpc_failure_logs_do_not_include_endpoint_credentials(caplog):
    ops = _make_ops()
    client = _inject_client(ops)
    client.get_job.side_effect = RuntimeError("https://rpc.example/private-api-key")
    await ops.get_job(1)
    assert "private-api-key" not in caplog.text


def test_paymaster_success_debug_logs_redact_credentials(caplog):
    import logging

    from bnbagent.core.paymaster import Paymaster

    caplog.set_level(logging.DEBUG)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "ok"}
    with patch("bnbagent.core.paymaster.requests.post", return_value=response):
        payer = Paymaster("https://rpc.example/private-api-key", debug=True)
        payer._make_rpc_request("eth_chainId", [])
    assert "private-api-key" not in caplog.text
