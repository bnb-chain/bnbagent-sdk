"""``state.startup`` must not be awaited — regression guard for a mount deadlock.

``create_erc8183_app`` exposes ``app.state.startup`` so a parent app can launch
the funded-job poll loop itself (Starlette does not propagate lifespan events
into mounted sub-apps). It is a *sync* lambda that returns the poll loop's
``asyncio.Task``, and that loop only exits on shutdown — so ``await``-ing it
never returns. A parent lifespan that awaited it would never reach ``yield``,
and uvicorn would come up without ever serving a request.

``service_mount.py`` did exactly that until this was fixed. These tests pin the
contract that made it a bug.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from erc8183_server import create_erc8183_app


def _fake_state():
    """Same shape as the stub in test_routes_poll.py — no chain, no wallet."""
    ops = MagicMock()
    ops.agent_address = "0x" + "aa" * 20
    ops.get_pending_jobs = AsyncMock(return_value={"success": True, "jobs": []})
    state = MagicMock()
    state.job_ops = ops
    state.payment_token = ""
    state.payment_token_decimals = 18
    return state


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        "erc8183_server.create_erc8183_state",
        lambda config: _fake_state(),
    )
    return create_erc8183_app(
        config=MagicMock(),
        on_job=lambda job: "done",
        funded_poll_interval=0.02,
    )


def test_startup_is_a_sync_callable(app):
    """The callable is sync, so ``await startup()`` awaits its *return value*."""
    startup = app.state.startup
    assert callable(startup)
    assert not inspect.iscoroutinefunction(startup)


def test_startup_returns_a_still_running_task(app):
    """It hands back the live poll loop — which is why awaiting it blocks."""

    async def drive():
        task = app.state.startup()
        assert isinstance(task, asyncio.Task)
        await asyncio.sleep(0)  # give the loop a chance to start
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())


def test_awaiting_startup_never_returns(app):
    """The deadlock itself: mount code must call startup() without awaiting."""

    async def drive():
        task = app.state.startup()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(drive())
