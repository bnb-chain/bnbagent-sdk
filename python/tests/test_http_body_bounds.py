import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bnbagent.utils.request_body import RequestBodyTooLarge, read_json_body


@pytest.mark.asyncio
async def test_http_body_limit_applies_to_stream_without_content_length():
    async def chunks():
        yield b'{"task":"'
        yield b"x" * 32
        yield b'"}'

    request = SimpleNamespace(headers={}, stream=chunks)
    with pytest.raises(RequestBodyTooLarge):
        await read_json_body(request, max_bytes=16)


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_without_reading_the_stream():
    request = SimpleNamespace(headers={"content-length": "20000"}, stream=MagicMock())
    with pytest.raises(RequestBodyTooLarge):
        await read_json_body(request)
    request.stream.assert_not_called()


@pytest.mark.asyncio
async def test_body_deadline_cancels_a_stalled_stream(monkeypatch):
    async def chunks():
        await asyncio.sleep(10)
        yield b"{}"

    request = SimpleNamespace(headers={}, stream=chunks)
    original_wait_for = asyncio.wait_for

    async def fast_wait(coro, timeout):
        return await original_wait_for(coro, 0.01)

    monkeypatch.setattr("bnbagent.utils.request_body.asyncio.wait_for", fast_wait)
    with pytest.raises(TimeoutError):
        await read_json_body(request)
