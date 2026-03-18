"""Tests for APEXMiddleware — ASGI middleware for job verification."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bnbagent.apex.client import APEXStatus
from bnbagent.apex.server.middleware import (
    DEFAULT_SKIP_PATHS,
    JOB_ID_HEADER,
    APEXMiddleware,
    create_apex_middleware,
)


def _make_scope(path="/submit", method="POST", headers=None):
    """Create a minimal ASGI scope."""
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.encode(), v.encode()))
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": raw_headers,
    }


def _make_middleware(verify_result=None, skip_paths=None):
    """Create middleware with mocked job_ops."""
    job_ops = AsyncMock()
    if verify_result is None:
        verify_result = {
            "valid": True,
            "job": {
                "provider": "0xAgent",
                "status": APEXStatus.FUNDED,
            },
        }
    job_ops.verify_job.return_value = verify_result

    app = AsyncMock()
    middleware = APEXMiddleware(app, job_ops=job_ops, skip_paths=skip_paths)
    return middleware, app, job_ops


class TestAPEXMiddleware:
    @pytest.mark.asyncio
    async def test_skips_non_http(self):
        middleware, app, _ = _make_middleware()
        scope = {"type": "websocket", "path": "/ws"}
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once_with(scope, receive, send)

    @pytest.mark.asyncio
    async def test_skips_get(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(method="GET")
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_head(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(method="HEAD")
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_options(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(method="OPTIONS")
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_configured_paths(self):
        middleware, app, _ = _make_middleware()
        for path in DEFAULT_SKIP_PATHS:
            scope = _make_scope(path=path, method="POST")
            receive = AsyncMock()
            send = AsyncMock()
            await middleware(scope, receive, send)
        assert app.call_count == len(DEFAULT_SKIP_PATHS)

    @pytest.mark.asyncio
    async def test_missing_job_id_returns_402(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(headers={})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        # Check 402 was sent
        start_call = send.call_args_list[0][0][0]
        assert start_call["status"] == 402
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_job_id_format_returns_400(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(headers={JOB_ID_HEADER: "not-a-number"})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        start_call = send.call_args_list[0][0][0]
        assert start_call["status"] == 400
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_job_passes_through(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(headers={JOB_ID_HEADER: "42"})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_job_returns_error_code(self):
        verify_result = {"valid": False, "error": "Job not funded", "error_code": 409}
        middleware, app, _ = _make_middleware(verify_result=verify_result)
        scope = _make_scope(headers={JOB_ID_HEADER: "42"})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        start_call = send.call_args_list[0][0][0]
        assert start_call["status"] == 409
        app.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_504(self):
        middleware, app, job_ops = _make_middleware()

        # Make verify_job hang
        async def slow_verify(job_id):
            await asyncio.sleep(100)
            return {"valid": True}

        job_ops.verify_job.side_effect = slow_verify

        # Reduce timeout for test
        import bnbagent.apex.server.middleware as mw_module

        original_timeout = mw_module.JOB_VERIFY_TIMEOUT
        mw_module.JOB_VERIFY_TIMEOUT = 0.01
        try:
            scope = _make_scope(headers={JOB_ID_HEADER: "42"})
            receive = AsyncMock()
            send = AsyncMock()
            await middleware(scope, receive, send)
            start_call = send.call_args_list[0][0][0]
            assert start_call["status"] == 504
        finally:
            mw_module.JOB_VERIFY_TIMEOUT = original_timeout

    @pytest.mark.asyncio
    async def test_exception_returns_502(self):
        middleware, app, job_ops = _make_middleware()
        job_ops.verify_job.side_effect = RuntimeError("unexpected")
        scope = _make_scope(headers={JOB_ID_HEADER: "42"})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        start_call = send.call_args_list[0][0][0]
        assert start_call["status"] == 502

    @pytest.mark.asyncio
    async def test_custom_skip_paths(self):
        middleware, app, _ = _make_middleware(skip_paths=["/custom"])
        scope = _make_scope(path="/custom", method="POST")
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()

    @pytest.mark.asyncio
    async def test_json_error_format(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(headers={})
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        # Second send call has the body
        body_call = send.call_args_list[1][0][0]
        parsed = json.loads(body_call["body"])
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_prefix_path_matching(self):
        middleware, app, _ = _make_middleware()
        scope = _make_scope(path="/health/deep", method="POST")
        receive = AsyncMock()
        send = AsyncMock()
        await middleware(scope, receive, send)
        app.assert_called_once()  # /health is in DEFAULT_SKIP_PATHS


class TestFactory:
    def test_returns_middleware(self):
        job_ops = AsyncMock()
        factory = create_apex_middleware(job_ops)
        app = MagicMock()
        middleware = factory(app)
        assert isinstance(middleware, APEXMiddleware)

    def test_passes_skip_paths(self):
        job_ops = AsyncMock()
        factory = create_apex_middleware(job_ops, skip_paths=["/custom"])
        app = MagicMock()
        middleware = factory(app)
        assert "/custom" in middleware._skip_paths
