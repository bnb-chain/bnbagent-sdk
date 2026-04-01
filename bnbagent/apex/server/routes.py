"""
FastAPI application factory for APEX agents.

Provides:
- create_apex_app(): Create a self-contained FastAPI sub-app with APEX endpoints
- _create_apex_routes(): Internal — build an APIRouter with APEX endpoints
"""

from __future__ import annotations

import asyncio
import inspect
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
from .job_ops import APEXJobOps

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
        wallet_provider=config.wallet_provider,
    )

    return APEXState(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
    )


def _create_apex_routes(
    state: APEXState,
    on_submit: Callable[[int, str, dict], Any] | None = None,
) -> APIRouter:
    """Create an APIRouter with APEX endpoints (internal).

    Used by ``create_apex_app()`` to build the route layer.

    Args:
        state: APEXState with config, job_ops, and negotiation_handler.
        on_submit: Optional callback after successful submit.
                   Called with (job_id, response_content, metadata)

    Returns:
        APIRouter with /submit, /job/{id}, /job/{id}/verify, /negotiate, /status, /health endpoints
    """
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


def create_apex_app(
    config: APEXConfig | None = None,
    on_job: Callable[..., Any] | None = None,
    on_submit: Callable[[int, str, dict], Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
    job_timeout: float | None = None,
    task_metadata: dict[str, Any] | None = None,
    prefix: str = "/apex",
) -> FastAPI:
    """Create a complete FastAPI application with APEX endpoints.

    **Standalone** (default) — run directly with ``uvicorn``::

        app = create_apex_app(on_job=execute_job)
        # Routes at /apex/submit, /apex/status, /apex/job/execute, etc.
        # Root / endpoint with service info

    **Mounted on a parent app** — pass ``prefix=""`` so the mount path
    controls the prefix instead::

        parent = FastAPI()
        apex_app = create_apex_app(on_job=execute_job, prefix="")
        parent.mount("/apex", apex_app)
        # Routes at /apex/submit, /apex/status, /apex/job/execute, etc.

    When ``on_job`` is provided, a one-time startup scan processes all
    pending funded jobs, and a ``POST /job/execute`` endpoint is added
    for client-initiated job execution with timeout support.

    The ``on_job`` callback supports four signatures::

        def on_job(job: dict) -> str                    # sync
        async def on_job(job: dict) -> str              # async
        def on_job(job: dict) -> tuple[str, dict]       # sync + per-job metadata
        async def on_job(job: dict) -> tuple[str, dict] # async + per-job metadata

    Without ``on_job``, the app only exposes HTTP endpoints (negotiate, submit,
    job query, etc.) and you must handle job discovery yourself.

    Args:
        config: APEXConfig instance (default: loads from env)
        on_job: Job handler called for each funded job. The SDK handles
                verification and submission automatically.
        on_submit: Optional callback after successful submit (lower-level;
                   prefer ``on_job`` for most use cases)
        on_job_skipped: Optional callback when a job fails verification and is
                        skipped. Called with ``(job_dict, reason_string)``.
                        Supports both sync and async callables.
        job_timeout: Seconds to wait before returning 202 Accepted from
                     ``/job/execute`` (default: env ``JOB_TIMEOUT`` or 120.0).
                     The job continues in the background after timeout.
        task_metadata: Default metadata attached to every submission
        prefix: URL prefix for APEX routes (default: ``"/apex"``).
                Use ``""`` when mounting as a sub-app so the mount path
                controls the prefix.

    Returns:
        FastAPI application instance
    """
    state = create_apex_state(config)

    effective_timeout = job_timeout or float(os.getenv("JOB_TIMEOUT", "120.0"))

    # Shared set for dedup between startup scan and /job/execute endpoint
    processing_jobs: set[int] = set()

    # Background tasks tracked for graceful shutdown
    background_tasks: set[asyncio.Task] = set()

    is_async = inspect.iscoroutinefunction(on_job) if on_job else False

    # ── Shared job execution logic ────────────────────────────────────────

    async def _execute_job_internal(job_id: int) -> dict:
        """Verify → on_job → submit_result. Used by startup scan and /job/execute."""
        verification = await state.job_ops.verify_job(job_id)
        if not verification.get("valid"):
            reason = verification.get("error", "unknown")
            if on_job_skipped:
                try:
                    if inspect.iscoroutinefunction(on_job_skipped):
                        await on_job_skipped(verification.get("job", {"jobId": job_id}), reason)
                    else:
                        await asyncio.to_thread(
                            on_job_skipped, verification.get("job", {"jobId": job_id}), reason
                        )
                except Exception as e:
                    logger.error(f"[APEX] on_job_skipped callback error: {e}")
            return {"success": False, "error": reason}

        job = verification["job"]

        # Call user's job handler
        if is_async:
            task_result = await on_job(job)
        else:
            task_result = await asyncio.to_thread(on_job, job)

        # Parse result: str or (str, dict)
        if isinstance(task_result, tuple):
            response_content, job_metadata = task_result
        else:
            response_content = task_result
            job_metadata = None

        # Merge metadata: defaults ← per-job overrides
        merged_meta = dict(task_metadata) if task_metadata else {}
        if job_metadata:
            merged_meta.update(job_metadata)

        submission = await state.job_ops.submit_result(
            job_id=job_id,
            response_content=response_content,
            metadata=merged_meta or None,
        )

        if submission.get("success"):
            submission["response_content"] = response_content
            logger.info(f"[APEX] Job #{job_id} submitted! TX: {submission.get('txHash')}")

            # Initiate UMA assertion so the liveness window starts immediately
            assertion = await state.job_ops.initiate_assertion(job_id)
            if assertion.get("success"):
                logger.info(f"[APEX] Job #{job_id} assertion initiated! TX: {assertion.get('txHash')}")
                submission["assertionTxHash"] = assertion.get("txHash")
            else:
                logger.error(f"[APEX] Job #{job_id} assertion failed: {assertion.get('error')}")
        else:
            logger.error(f"[APEX] Job #{job_id} submission failed: {submission.get('error')}")

        return submission

    # ── Startup scan ──────────────────────────────────────────────────────

    async def _startup_scan_worker():
        """One-time scan: process all pending funded jobs."""
        try:
            result = await state.job_ops.get_pending_jobs()
            if not result.get("success"):
                logger.warning(f"[APEX] Startup scan error: {result.get('error')}")
                return

            jobs = result.get("jobs", [])
            logger.info(f"[APEX] Startup scan found {len(jobs)} pending job(s)")

            for job in jobs:
                job_id = job["jobId"]
                if job_id in processing_jobs:
                    continue
                processing_jobs.add(job_id)
                try:
                    await _execute_job_internal(job_id)
                except Exception as e:
                    logger.error(f"[APEX] Startup scan job #{job_id} failed: {e}")
                finally:
                    processing_jobs.discard(job_id)
        except Exception as e:
            logger.error(f"[APEX] Startup scan failed: {e}")

    async def _run_startup_scan():
        """Launch startup scan as a background task."""
        task = asyncio.create_task(_startup_scan_worker())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    # ── Lifespan (standalone mode) ────────────────────────────────────────

    @asynccontextmanager
    async def apex_lifespan(app: FastAPI):
        if on_job:
            await _run_startup_scan()
        yield
        # Shutdown: cancel all remaining background tasks
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    apex_app = FastAPI(
        title="APEX Agent",
        description="APEX (Agent Payment Exchange Protocol) Agent",
        lifespan=apex_lifespan,
    )

    router = _create_apex_routes(state=state, on_submit=on_submit)
    apex_app.include_router(router, prefix=prefix)

    # Add /job/execute endpoint when on_job is provided
    if on_job:
        process_path = f"{prefix}/job/execute" if prefix else "/job/execute"

        @apex_app.post(process_path)
        async def process_job(request: Request):
            """Client-initiated job execution with timeout.

            Executes the job via on_job. If the job completes within
            ``job_timeout`` seconds, returns 200 with the full result.
            If it times out, returns 202 Accepted and the job continues
            in the background.
            """
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)

            raw_job_id = body.get("job_id")
            if raw_job_id is None:
                return JSONResponse({"error": "job_id is required"}, status_code=400)

            job_id = int(raw_job_id)

            # Per-request timeout override
            req_timeout = effective_timeout
            if body.get("timeout") is not None:
                try:
                    req_timeout = float(body["timeout"])
                except (TypeError, ValueError):
                    pass

            if job_id in processing_jobs:
                return JSONResponse(
                    {"error": "Job already being processed"}, status_code=409
                )

            processing_jobs.add(job_id)

            async def _job_wrapper():
                try:
                    return await _execute_job_internal(job_id)
                finally:
                    processing_jobs.discard(job_id)

            task = asyncio.create_task(_job_wrapper())
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

            done, _ = await asyncio.wait({task}, timeout=req_timeout)

            if done:
                # Completed within timeout → 200/500 with full result
                result = task.result()
                status_code = 200 if result.get("success") else 500
                return JSONResponse(result, status_code=status_code)
            else:
                # Timeout → 202 Accepted, task continues in background
                return JSONResponse(
                    {
                        "status": "accepted",
                        "job_id": job_id,
                        "message": (
                            "Job accepted, processing in background. "
                            "Use GET /job/{id}/response to retrieve the result."
                        ),
                    },
                    status_code=202,
                )

    # Standalone mode: add root endpoint with service info
    if prefix:

        @apex_app.get("/")
        async def root():
            endpoints = {
                "submit": f"{prefix}/submit",
                "job": f"{prefix}/job/{{job_id}}",
                "response": f"{prefix}/job/{{job_id}}/response",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "negotiate": f"{prefix}/negotiate",
                "status": f"{prefix}/status",
                "health": f"{prefix}/health",
            }
            if on_job:
                endpoints["process"] = f"{prefix}/job/execute"
            return {
                "service": "APEX Agent",
                "agent_address": state.job_ops.agent_address,
                "endpoints": endpoints,
            }

    # Store state for external access
    apex_app.state.apex = state

    # Expose startup scan for gateway (mounted mode where lifespan doesn't propagate)
    if on_job:
        apex_app.state.startup = _run_startup_scan

    return apex_app
