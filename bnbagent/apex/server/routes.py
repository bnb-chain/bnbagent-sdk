"""FastAPI factory for APEX provider agents.

- ``create_apex_app(...)`` — build a FastAPI sub-app with the APEX endpoints
  (negotiate / submit / status / job / settle) and optionally an
  ``/job/execute`` endpoint driven by a user-provided ``on_job`` callback.
- An optional auto-settle background loop drives ``router.settle(...)`` for
  this provider's submitted jobs once the dispute window elapses.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ...core.config import get_env
from ...storage import LocalStorageProvider
from ..config import APEX_ENV_PREFIX, APEXConfig
from ..negotiation import NegotiationHandler
from .job_ops import APEXJobOps, run_auto_settle_loop

logger = logging.getLogger(__name__)


@dataclass
class APEXState:
    """Shared state for APEX routes."""

    config: APEXConfig
    job_ops: APEXJobOps
    negotiation_handler: NegotiationHandler
    payment_token: str = ""
    payment_token_decimals: int = 18

    def __repr__(self) -> str:
        return (
            f"APEXState("
            f"agent_address='{self.job_ops.agent_address}', "
            f"commerce='{self.config.effective_commerce_address}')"
        )


def create_apex_state(config: APEXConfig | None = None) -> APEXState:
    """Build ``APEXState`` from config (env fallback) with sensible defaults."""
    if config is None:
        config = APEXConfig.from_env()

    if config.wallet_provider is None:
        raise ValueError(
            "APEXConfig.wallet_provider is required to build APEXState. "
            "Pass a wallet_provider= or set WALLET_PASSWORD (+ PRIVATE_KEY)."
        )

    storage = config.storage or LocalStorageProvider()

    job_ops = APEXJobOps(
        config.wallet_provider,
        network=config.effective_network,
        storage_provider=storage,
        service_price=int(config.service_price),
    )

    # Fetch payment token + decimals once at startup so /status responses
    # don't cost an RPC per request. Non-fatal if lookup fails (e.g. RPC
    # down during boot); we degrade to unknown and let later calls retry.
    currency = ""
    decimals = 18
    try:
        currency = job_ops.apex_client.payment_token
        decimals = job_ops.apex_client.token_decimals()
    except Exception as exc:
        logger.warning(f"[APEX] payment_token lookup failed: {exc}")

    negotiation_handler = NegotiationHandler(
        service_price=config.service_price,
        currency=currency,
        wallet_provider=config.wallet_provider,
    )

    return APEXState(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
        payment_token=currency,
        payment_token_decimals=decimals,
    )


def _create_apex_routes(
    state: APEXState,
    on_submit: Callable[[int, str, dict], Any] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["APEX"])

    @router.post("/submit")
    async def submit_result(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        job_id = body.get("job_id")
        if job_id is None:
            return JSONResponse({"error": "job_id is required"}, status_code=400)
        response_content = body.get("response_content", "")
        metadata = body.get("metadata")
        result = await state.job_ops.submit_result(
            job_id=int(job_id),
            response_content=response_content,
            metadata=metadata,
        )
        if result.get("success") and on_submit:
            try:
                on_submit(int(job_id), response_content, metadata or {})
            except Exception as exc:
                logger.warning(f"[APEX] on_submit callback error: {exc}")
        return JSONResponse(result, status_code=200 if result.get("success") else 500)

    @router.get("/job/{job_id}")
    async def get_job(job_id: int):
        result = await state.job_ops.get_job(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
        if "status" in result and hasattr(result["status"], "value"):
            result["status"] = result["status"].value
        return JSONResponse(result)

    @router.get("/job/{job_id}/response")
    async def get_job_response(job_id: int):
        result = await state.job_ops.get_response(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=404)
        return JSONResponse(result)

    @router.get("/job/{job_id}/verify")
    async def verify_job(job_id: int):
        result = await state.job_ops.verify_job(job_id)
        return JSONResponse(result, status_code=200 if result.get("valid") else 400)

    @router.post("/job/{job_id}/settle")
    async def settle_job(job_id: int):
        """Manually trigger permissionless ``router.settle`` for a job.

        Exposed for operators; the auto-settle loop handles the common case.
        """
        try:
            result = await asyncio.to_thread(state.job_ops.apex_client.settle, job_id)
            return JSONResponse({"success": True, "txHash": result["transactionHash"]})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @router.post("/negotiate")
    async def negotiate(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or "terms" not in body:
            return JSONResponse(
                {
                    "error": (
                        "Request must include 'terms' with service_type,"
                        " deliverables, quality_standards"
                    )
                },
                status_code=400,
            )
        try:
            result = state.negotiation_handler.negotiate(body)
            return JSONResponse(result.to_dict())
        except Exception as exc:
            logger.error(f"[APEX] Negotiation failed: {exc}")
            return JSONResponse({"error": "Negotiation failed"}, status_code=500)

    @router.get("/status")
    async def status():
        return {
            "status": "ok",
            "agent_address": state.job_ops.agent_address,
            "commerce_address": state.config.effective_commerce_address,
            "router_address": state.config.effective_router_address,
            "policy_address": state.config.effective_policy_address,
            "service_price": state.config.service_price,
            "currency": state.payment_token,
            "decimals": state.payment_token_decimals,
        }

    @router.get("/health")
    async def health():
        return {"status": "ok", "service": "APEX Agent"}

    return router


def create_apex_app(
    config: APEXConfig | None = None,
    on_job: Callable[..., Any] | None = None,
    on_submit: Callable[[int, str, dict], Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
    exec_timeout: float | None = None,
    task_metadata: dict[str, Any] | None = None,
    prefix: str = "/apex",
    auto_settle: bool = True,
    auto_settle_interval: float = 30.0,
) -> FastAPI:
    """Create a FastAPI application for an APEX provider agent.

    Parameters
    ----------
    on_job
        Job handler invoked for each pending funded job. One of::

            def on_job(job: dict) -> str
            async def on_job(job: dict) -> str
            def on_job(job: dict) -> tuple[str, dict]    # per-job metadata
            async def on_job(job: dict) -> tuple[str, dict]

        The SDK handles verification, submission, and tracking for auto-settle.
    exec_timeout
        ``/job/execute`` callback timeout in seconds. Falls back to the
        ``APEX_EXEC_TIMEOUT`` env var (default ``120``). This only caps
        how long the HTTP request blocks before returning ``202 Accepted``
        — on-chain ``job.expiredAt`` is a separate, stricter bound.
    auto_settle
        If True (default), spawn a background task that calls
        ``router.settle(jobId)`` for this agent's submitted jobs once the
        dispute window elapses. The permissionless settle keeps funds
        flowing without relying on the client to settle.
    auto_settle_interval
        Seconds between auto-settle passes.
    """
    state = create_apex_state(config)
    # /job/execute callback timeout (seconds). Only bounds how long the HTTP
    # request blocks — on-chain job.expiredAt is a separate, stricter bound.
    effective_exec_timeout = exec_timeout or float(
        get_env("EXEC_TIMEOUT", "120.0", prefix=APEX_ENV_PREFIX) or "120.0"
    )

    processing_jobs: set[int] = set()
    background_tasks: set[asyncio.Task] = set()
    stop_event = asyncio.Event()
    is_async_on_job = inspect.iscoroutinefunction(on_job) if on_job else False

    async def _execute_job_internal(job_id: int) -> dict:
        verification = await state.job_ops.verify_job(job_id)
        if not verification.get("valid"):
            reason = verification.get("error", "unknown")
            if on_job_skipped:
                try:
                    target = verification.get("job", {"jobId": job_id})
                    if inspect.iscoroutinefunction(on_job_skipped):
                        await on_job_skipped(target, reason)
                    else:
                        await asyncio.to_thread(on_job_skipped, target, reason)
                except Exception as exc:
                    logger.error(f"[APEX] on_job_skipped callback error: {exc}")
            return {"success": False, "error": reason}

        job = verification["job"]

        if is_async_on_job:
            task_result = await on_job(job)
        else:
            task_result = await asyncio.to_thread(on_job, job)

        if isinstance(task_result, tuple):
            response_content, job_metadata = task_result
        else:
            response_content, job_metadata = task_result, None

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
            logger.info(f"[APEX] Job #{job_id} submitted, tx={submission.get('txHash')}")
        else:
            logger.error(f"[APEX] Job #{job_id} submission failed: {submission.get('error')}")
        return submission

    async def _startup_scan_worker():
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
                except Exception as exc:
                    logger.error(f"[APEX] Startup scan job #{job_id} failed: {exc}")
                finally:
                    processing_jobs.discard(job_id)
        except Exception as exc:
            logger.error(f"[APEX] Startup scan failed: {exc}")

    def _spawn(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    @asynccontextmanager
    async def apex_lifespan(_: FastAPI):
        if on_job:
            _spawn(_startup_scan_worker())
        if auto_settle:
            _spawn(run_auto_settle_loop(state.job_ops, auto_settle_interval, stop_event=stop_event))
        yield
        stop_event.set()
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    apex_app = FastAPI(
        title="APEX Agent",
        description="APEX v1 provider agent (AgenticCommerce + Router + OptimisticPolicy)",
        lifespan=apex_lifespan,
    )

    router = _create_apex_routes(state=state, on_submit=on_submit)
    apex_app.include_router(router, prefix=prefix)

    if on_job:
        process_path = f"{prefix}/job/execute" if prefix else "/job/execute"

        @apex_app.post(process_path)
        async def process_job(request: Request):
            try:
                body = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            raw_job_id = body.get("job_id")
            if raw_job_id is None:
                return JSONResponse({"error": "job_id is required"}, status_code=400)
            job_id = int(raw_job_id)

            req_timeout = effective_exec_timeout
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

            async def _wrapper():
                try:
                    return await _execute_job_internal(job_id)
                finally:
                    processing_jobs.discard(job_id)

            task = _spawn(_wrapper())
            done, _ = await asyncio.wait({task}, timeout=req_timeout)
            if done:
                result = task.result()
                return JSONResponse(result, status_code=200 if result.get("success") else 500)
            return JSONResponse(
                {
                    "status": "accepted",
                    "job_id": job_id,
                    "message": (
                        "Job accepted, processing in background."
                        " Use GET /job/{id}/response to retrieve the result."
                    ),
                },
                status_code=202,
            )

    if prefix:

        @apex_app.get("/")
        async def root():
            endpoints = {
                "submit": f"{prefix}/submit",
                "job": f"{prefix}/job/{{job_id}}",
                "response": f"{prefix}/job/{{job_id}}/response",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "settle": f"{prefix}/job/{{job_id}}/settle",
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

    apex_app.state.apex = state
    if on_job:
        apex_app.state.startup = lambda: _spawn(_startup_scan_worker())

    return apex_app
