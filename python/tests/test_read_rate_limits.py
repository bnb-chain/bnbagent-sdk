import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from bnbagent.utils import SlidingWindowLimiter


@pytest.mark.asyncio
async def test_job_detail_shares_the_public_read_budget(monkeypatch):
    path = Path(__file__).parents[1] / "examples/agent-server/src/erc8183_server.py"
    spec = importlib.util.spec_from_file_location("rate_limited_example", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    job_ops = SimpleNamespace(
        get_job=AsyncMock(return_value={"success": True}),
        get_response=AsyncMock(return_value={"success": True}),
    )
    state = SimpleNamespace(
        job_ops=job_ops, config=SimpleNamespace(), negotiation_handler=MagicMock()
    )
    app = FastAPI()
    app.include_router(
        module._create_erc8183_routes(state, response_limiter=SlidingWindowLimiter(1, 60)),
        prefix="/erc8183",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/erc8183/job/1")).status_code == 200
        assert (await client.get("/erc8183/job/2")).status_code == 429
        assert (await client.get("/erc8183/job/2/response")).status_code == 429
    job_ops.get_job.assert_awaited_once()
    job_ops.get_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_a2a_status_is_limited_before_another_rpc_call():
    # Compile the real route without the example module's wallet/RPC startup.
    import ast

    from bnbagent.utils import RateLimitExceeded
    from tests.test_erc8183_job_ops import _job

    path = Path(__file__).parents[1] / "examples/a2a-agent/src/server.py"
    node = next(
        item
        for item in ast.parse(path.read_text()).body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "a2a_endpoint"
    )
    node.decorator_list = []
    rpc = MagicMock()
    rpc.get_job.return_value = _job()
    context = {
        "Request": object,
        "client": rpc,
        "read_limiter": SlidingWindowLimiter(1, 60),
        "global_read_limiter": SlidingWindowLimiter(10, 60),
        "RateLimitExceeded": RateLimitExceeded,
        "_extract_data_part": lambda message: message["parts"][0]["data"],
        "_agent_message": lambda data: data,
        "_rpc_error": lambda req_id, code, message, status=200: SimpleNamespace(
            status_code=status
        ),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), context)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": {"parts": [{"data": {"skill": "erc8183-job-status", "job_id": 1}}]}},
    }
    request = SimpleNamespace(
        client=SimpleNamespace(host="peer"), json=AsyncMock(return_value=body)
    )
    assert isinstance(await context["a2a_endpoint"](request), dict)
    assert (await context["a2a_endpoint"](request)).status_code == 429
    rpc.get_job.assert_called_once()
