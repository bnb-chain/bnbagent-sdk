"""
FastAPI application factory for APEX agents.

Provides:
- APEX: Extension class — one-line mount for existing apps
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
        service_price=int(config.service_price),
        payment_token_decimals=config.payment_token_decimals,
    )

    negotiation_handler = NegotiationHandler(
        service_price=config.service_price,
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

        app.include_router(create_apex_routes(), prefix="/your-prefix")

    Args:
        config: APEXConfig instance (default: loads from env)
        state: Pre-created APEXState (default: creates from config)
        on_submit: Optional callback after successful submit.
                   Called with (job_id, response_content, metadata)

    Returns:
        APIRouter with /submit, /job/{id}, /job/{id}/verify, /negotiate, /status, /health endpoints
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

    @router.get("/job/{job_id}/response")
    async def get_job_response(job_id: int):
        """Get stored deliverable response for a job."""
        result = await state.job_ops.get_response(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=404)
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
            "service_price": state.config.service_price,
            "currency": state.config.effective_payment_token,
            "decimals": state.config.payment_token_decimals,
        }

    @router.get("/health")
    async def health():
        """Health check for load balancers and monitoring."""
        return {"status": "ok", "service": "APEX Agent"}

    return router


class APEX:
    """APEX extension for FastAPI — one-line mount.

    Bundles routes, middleware, and background job loop into a single object
    that can be mounted onto any existing FastAPI application::

        apex = APEX(on_job=execute_job)
        apex.mount(app, prefix="/apex")

    This is equivalent to manually wiring ``create_apex_routes()``,
    ``APEXMiddleware``, and ``run_job_loop()`` — but without the boilerplate.
    """

    def __init__(
        self,
        config: APEXConfig | None = None,
        on_job: Callable[..., Any] | None = None,
        on_submit: Callable[[int, str, dict], Any] | None = None,
        on_job_skipped: Callable[[dict, str], Any] | None = None,
        poll_interval: int | None = None,
        task_metadata: dict[str, Any] | None = None,
        middleware: bool = True,
        skip_paths: list[str] | None = None,
    ):
        if config is None:
            config = APEXConfig.from_env()
        self._config = config
        self._state = create_apex_state(config)
        self._on_job = on_job
        self._on_submit = on_submit
        self._on_job_skipped = on_job_skipped
        self._poll_interval = poll_interval
        self._task_metadata = task_metadata
        self._middleware = middleware
        self._skip_paths = skip_paths
        self._mounted = False
        self._job_loop_task: asyncio.Task | None = None

    @property
    def state(self) -> APEXState:
        return self._state

    @property
    def job_ops(self) -> APEXJobOps:
        return self._state.job_ops

    async def startup(self) -> None:
        """Start the background job loop.

        Call this from your own ``lifespan`` context manager if the app uses one,
        since FastAPI ignores ``on_startup`` hooks when ``lifespan`` is set::

            @asynccontextmanager
            async def lifespan(app):
                await apex.startup()
                yield
                await apex.shutdown()

        If the app does **not** use ``lifespan``, :meth:`mount` registers these
        hooks automatically via ``app.router.on_startup`` / ``on_shutdown``.
        """
        if not self._on_job:
            return
        interval = self._poll_interval or int(os.getenv("POLL_INTERVAL", "10"))
        self._job_loop_task = asyncio.create_task(
            run_job_loop(
                job_ops=self._state.job_ops,
                on_job=self._on_job,
                poll_interval=interval,
                metadata=self._task_metadata,
                on_job_skipped=self._on_job_skipped,
            )
        )
        logger.info("[APEX] Job loop started: poll_interval=%ds", interval)

    async def shutdown(self) -> None:
        """Stop the background job loop. See :meth:`startup`."""
        if self._job_loop_task:
            self._job_loop_task.cancel()
            try:
                await self._job_loop_task
            except asyncio.CancelledError:
                pass
            self._job_loop_task = None

    def mount(self, app: FastAPI, prefix: str = "/apex") -> None:
        """Mount APEX onto *app*: routes, middleware, and job-loop lifecycle.

        Args:
            app: The FastAPI application to mount onto.
            prefix: URL prefix for all APEX routes (default ``/apex``).

        Raises:
            RuntimeError: If called more than once on the same ``APEX`` instance.
        """
        if self._mounted:
            raise RuntimeError("APEX already mounted")
        self._mounted = True

        # 1. Routes
        router = create_apex_routes(state=self._state, on_submit=self._on_submit)
        app.include_router(router, prefix=prefix)

        # 2. Middleware
        if self._middleware:
            from .middleware import DEFAULT_SKIP_PATHS, APEXMiddleware

            effective_skip = list(DEFAULT_SKIP_PATHS)
            effective_skip.extend(f"{prefix}{p}" for p in DEFAULT_SKIP_PATHS)
            if self._skip_paths:
                effective_skip.extend(self._skip_paths)
            app.add_middleware(
                APEXMiddleware,
                job_ops=self._state.job_ops,
                skip_paths=effective_skip,
            )

        # 3. Job loop lifecycle — wrap the app's lifespan so it works
        #    regardless of whether the user set a custom lifespan or not.
        if self._on_job:
            original_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def _apex_lifespan(app: FastAPI):
                await self.startup()
                async with original_lifespan(app) as state:
                    yield state
                await self.shutdown()

            app.router.lifespan_context = _apex_lifespan

        logger.info(
            "[APEX] Mounted: prefix=%s, middleware=%s, job_loop=%s",
            prefix,
            "enabled" if self._middleware else "disabled",
            "enabled" if self._on_job else "disabled",
        )


def create_apex_app(
    config: APEXConfig | None = None,
    on_job: Callable[..., Any] | None = None,
    on_submit: Callable[[int, str, dict], Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
    poll_interval: int | None = None,
    task_metadata: dict[str, Any] | None = None,
    middleware: bool = True,
    skip_paths: list[str] | None = None,
) -> FastAPI:
    """Create a complete FastAPI application with APEX endpoints.

    The simplest way to deploy an APEX agent::

        async def execute_job(job: dict) -> str:
            return f"Processed: {job['description']}"

        app = create_apex_app(on_job=execute_job)

    Run with: ``uvicorn myagent:app``

    When ``on_job`` is provided, the app automatically polls for funded jobs
    in the background, verifies them, calls your handler, and submits results
    on-chain. You only write the business logic.

    The ``on_job`` callback supports four signatures::

        def on_job(job: dict) -> str                    # sync
        async def on_job(job: dict) -> str              # async
        def on_job(job: dict) -> tuple[str, dict]       # sync + per-job metadata
        async def on_job(job: dict) -> tuple[str, dict] # async + per-job metadata

    Without ``on_job``, the app only exposes HTTP endpoints (negotiate, submit,
    job query, etc.) and you must handle job discovery yourself.

    Middleware is **enabled by default** (secure-by-default). All POST/PUT/DELETE
    requests must include a valid ``X-Job-Id`` header whose on-chain job is
    FUNDED and assigned to this agent. Safe methods (GET/HEAD/OPTIONS) and
    standard paths (``/status``, ``/negotiate``, ``/health``, etc.) are always
    allowed.

    Routes are mounted at ``/apex/*``. For custom prefixes, use
    ``create_apex_routes()`` with ``app.include_router(prefix="/your-prefix")``.

    Args:
        config: APEXConfig instance (default: loads from env)
        on_job: Job handler called for each funded job. The SDK handles
                discovery, verification, and submission automatically.
        on_submit: Optional callback after successful submit (lower-level;
                   prefer ``on_job`` for most use cases)
        on_job_skipped: Optional callback when a job fails verification and is
                        skipped. Called with ``(job_dict, reason_string)``.
                        Supports both sync and async callables.
        poll_interval: Seconds between polling cycles (default: env POLL_INTERVAL or 10)
        task_metadata: Default metadata attached to every submission
        middleware: Enable APEXMiddleware for job verification (default: True)
        skip_paths: Additional paths to skip verification (merged with defaults)

    Returns:
        FastAPI application instance
    """
    apex = APEX(
        config=config,
        on_job=on_job,
        on_submit=on_submit,
        on_job_skipped=on_job_skipped,
        poll_interval=poll_interval,
        task_metadata=task_metadata,
        middleware=middleware,
        skip_paths=skip_paths,
    )

    app = FastAPI(
        title="APEX Agent",
        description="APEX (Agent Payment Exchange Protocol) Agent",
    )
    apex.mount(app)

    prefix = "/apex"

    @app.get("/")
    async def root():
        return {
            "service": "APEX Agent",
            "agent_address": apex.state.job_ops.agent_address,
            "endpoints": {
                "submit": f"{prefix}/submit",
                "job": f"{prefix}/job/{{job_id}}",
                "response": f"{prefix}/job/{{job_id}}/response",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "negotiate": f"{prefix}/negotiate",
                "status": f"{prefix}/status",
                "health": f"{prefix}/health",
            },
        }

    return app
