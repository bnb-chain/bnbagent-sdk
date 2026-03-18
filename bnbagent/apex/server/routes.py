"""
FastAPI application factory for APEX agents.

Provides:
- create_apex_app(): Create a complete FastAPI app with APEX endpoints
- create_apex_routes(): Create an APIRouter to mount in existing apps
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ...storage import LocalStorageProvider
from ..config import APEXConfig
from ..negotiation import NegotiationHandler
from .job_ops import APEXJobOps, run_job_loop

logger = logging.getLogger(__name__)


@dataclass
class APEXState:
    """Shared state for APEX operations.

    Initialized once and shared across all route handlers.
    """

    config: APEXConfig
    job_ops: APEXJobOps
    negotiation_handler: NegotiationHandler

    def __repr__(self) -> str:
        """Safe repr that hides sensitive data."""
        return (
            f"APEXState("
            f"agent_address='{self.job_ops.agent_address}', "
            f"erc8183='{self.config.effective_erc8183_address}')"
        )


def create_apex_state(config: APEXConfig | None = None) -> APEXState:
    """Create APEXState with all necessary components.

    Args:
        config: APEXConfig instance. If None, loads from env vars.

    Returns:
        APEXState with job_ops and negotiation_handler initialized.
    """
    if config is None:
        config = APEXConfig.from_env()

    # Resolve storage: explicit > config.storage > local fallback
    storage = config.storage or LocalStorageProvider()

    job_ops = APEXJobOps(
        rpc_url=config.effective_rpc_url,
        erc8183_address=config.effective_erc8183_address,
        storage_provider=storage,
        chain_id=config.effective_chain_id,
        wallet_provider=config.wallet_provider,
    )

    negotiation_handler = NegotiationHandler(
        base_price=config.agent_price,
        currency=config.effective_payment_token,
    )

    return APEXState(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
    )


def create_apex_routes(
    config: APEXConfig | None = None,
    state: APEXState | None = None,
    on_submit: Callable[[int, str, dict], Any] | None = None,
) -> APIRouter:
    """Create an APIRouter with APEX endpoints.

    Can be mounted to an existing FastAPI app:

        app.include_router(create_apex_routes(), prefix="/apex")

    Args:
        config: APEXConfig instance (default: loads from env)
        state: Pre-created APEXState (default: creates from config)
        on_submit: Optional callback after successful submit.
                   Called with (job_id, response_content, metadata)

    Returns:
        APIRouter with /submit, /job/{id}, /job/{id}/verify, /negotiate endpoints
    """
    # Resolve config and state
    if state is None:
        if config is None:
            config = APEXConfig.from_env()
        state = create_apex_state(config)

    router = APIRouter(tags=["APEX"])

    @router.post("/submit")
    async def submit_result(request: Request):
        """Submit job result on-chain."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        job_id = body.get("job_id")
        response_content = body.get("response_content", "")
        metadata = body.get("metadata")

        if job_id is None:
            return JSONResponse({"error": "job_id is required"}, status_code=400)

        result = await state.job_ops.submit_result(
            job_id=int(job_id),
            response_content=response_content,
            metadata=metadata,
        )

        # Call callback if provided and successful
        if result.get("success") and on_submit:
            try:
                on_submit(int(job_id), response_content, metadata or {})
            except Exception as e:
                logger.warning(f"[APEX] on_submit callback error: {e}")

        status_code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=status_code)

    @router.get("/job/{job_id}")
    async def get_job(job_id: int):
        """Get job details from chain."""
        result = await state.job_ops.get_job(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
        if "deliverable" in result and isinstance(result["deliverable"], bytes):
            result["deliverable"] = "0x" + result["deliverable"].hex()
        if "description" in result and isinstance(result["description"], bytes):
            result["description"] = result["description"].decode("utf-8", errors="replace")
        if "status" in result and hasattr(result["status"], "value"):
            result["status"] = result["status"].value
        return JSONResponse(result)

    @router.get("/job/{job_id}/verify")
    async def verify_job(job_id: int):
        """Verify if a job can be processed by this agent."""
        result = await state.job_ops.verify_job(job_id)
        status_code = 200 if result.get("valid") else 400
        return JSONResponse(result, status_code=status_code)

    @router.post("/negotiate")
    async def negotiate(request: Request):
        """Process negotiation request."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        if not isinstance(body, dict) or "terms" not in body:
            return JSONResponse(
                {
                    "error": (
                        "Request must include 'terms' with"
                        " service_type, deliverables,"
                        " quality_standards"
                    )
                },
                status_code=400,
            )

        try:
            result = state.negotiation_handler.negotiate(body)
            return JSONResponse(result.to_dict())
        except Exception as e:
            logger.error(f"[APEX] Negotiation failed: {e}")
            return JSONResponse({"error": "Negotiation failed"}, status_code=500)

    @router.get("/status")
    async def status():
        """Agent status endpoint."""
        return {
            "status": "ok",
            "agent_address": state.job_ops.agent_address,
            "erc8183_address": state.config.effective_erc8183_address,
        }

    return router


def create_apex_app(
    config: APEXConfig | None = None,
    prefix: str = "",
    on_task: Callable[..., Any] | None = None,
    on_submit: Callable[[int, str, dict], Any] | None = None,
    poll_interval: int | None = None,
    task_metadata: dict[str, Any] | None = None,
    middleware: bool = True,
    skip_paths: list[str] | None = None,
) -> FastAPI:
    """Create a complete FastAPI application with APEX endpoints.

    The simplest way to deploy an APEX agent::

        async def my_task(job: dict) -> str:
            return f"Processed: {job['description']}"

        app = create_apex_app(on_task=my_task)

    Run with: ``uvicorn myagent:app``

    When ``on_task`` is provided, the app automatically polls for funded jobs
    in the background, verifies them, calls your handler, and submits results
    on-chain. You only write the business logic.

    The ``on_task`` callback supports four signatures::

        def on_task(job: dict) -> str                    # sync
        async def on_task(job: dict) -> str              # async
        def on_task(job: dict) -> tuple[str, dict]       # sync + per-job metadata
        async def on_task(job: dict) -> tuple[str, dict] # async + per-job metadata

    Without ``on_task``, the app only exposes HTTP endpoints (negotiate, submit,
    job query, etc.) and you must handle job discovery yourself.

    Middleware is **enabled by default** (secure-by-default). All POST/PUT/DELETE
    requests must include a valid ``X-Job-Id`` header whose on-chain job is
    FUNDED and assigned to this agent. Safe methods (GET/HEAD/OPTIONS) and
    standard paths (``/health``, ``/status``, ``/negotiate``, etc.) are always
    allowed.

    Args:
        config: APEXConfig instance (default: loads from env)
        prefix: URL prefix for APEX routes (default: no prefix)
        on_task: Task handler called for each funded job. The SDK handles
                 discovery, verification, and submission automatically.
        on_submit: Optional callback after successful submit (lower-level;
                   prefer ``on_task`` for most use cases)
        poll_interval: Seconds between polling cycles (default: env POLL_INTERVAL or 10)
        task_metadata: Default metadata attached to every submission
        middleware: Enable APEXMiddleware for job verification (default: True)
        skip_paths: Additional paths to skip verification (merged with defaults)

    Returns:
        FastAPI application instance
    """
    if config is None:
        config = APEXConfig.from_env()

    state = create_apex_state(config)

    # Resolve poll interval: explicit > env > default
    effective_interval = poll_interval or int(os.getenv("POLL_INTERVAL", "10"))

    # Build lifespan — manages background job loop if on_task is provided
    if on_task:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            task = asyncio.create_task(
                run_job_loop(
                    job_ops=state.job_ops,
                    on_task=on_task,
                    poll_interval=effective_interval,
                    metadata=task_metadata,
                )
            )
            logger.info(
                "[APEX] Job loop started: poll_interval=%ds", effective_interval
            )
            yield
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    else:
        lifespan = None

    app = FastAPI(
        title="APEX Agent",
        description="APEX (Agent Payment Exchange Protocol) Agent",
        lifespan=lifespan,
    )

    router = create_apex_routes(config=config, state=state, on_submit=on_submit)
    app.include_router(router, prefix=prefix if prefix else "")

    # Middleware — enabled by default for secure-by-default posture.
    # When a prefix is used, auto-generate prefixed skip paths so that
    # e.g. /api/negotiate is also skipped, not just /negotiate.
    if middleware:
        from .middleware import DEFAULT_SKIP_PATHS, APEXMiddleware

        effective_skip = list(DEFAULT_SKIP_PATHS)
        if prefix:
            effective_skip.extend(f"{prefix}{p}" for p in DEFAULT_SKIP_PATHS)
        if skip_paths:
            effective_skip.extend(skip_paths)

        app.add_middleware(APEXMiddleware, job_ops=state.job_ops, skip_paths=effective_skip)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "APEX Agent"}

    @app.get("/")
    async def root():
        return {
            "service": "APEX Agent",
            "agent_address": state.job_ops.agent_address,
            "endpoints": {
                "submit": f"{prefix}/submit",
                "job": f"{prefix}/job/{{job_id}}",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "negotiate": f"{prefix}/negotiate",
                "status": f"{prefix}/status",
                "health": "/health",
            },
        }

    logger.info(
        "[APEX] Agent created: address=%s, erc8183=%s, middleware=%s, job_loop=%s",
        state.job_ops.agent_address,
        config.effective_erc8183_address,
        "enabled" if middleware else "disabled",
        "enabled" if on_task else "disabled",
    )

    return app
