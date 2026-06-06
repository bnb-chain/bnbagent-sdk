"""FastAPI factory for ERC-8183 provider agents.

- ``create_erc8183_app(...)`` — build a FastAPI sub-app with the ERC-8183 endpoints
  (negotiate / status / health / job read-only).
- When ``on_job`` is provided, a background poll loop scans on-chain for
  newly funded jobs assigned to this provider and dispatches each through
  ``on_job`` → ``submit_result`` without exposing an external trigger.
- Settle is permissionless on-chain and is delegated to operator scripts;
  the agent server does not auto-settle and does not expose a settle endpoint.
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
from ...storage import LocalStorageProvider, StorageProvider
from ..config import ERC8183_ENV_PREFIX, ERC8183Config
from ..negotiation import NegotiationHandler
from .job_ops import ERC8183JobOps
from .rate_limit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

# Ceiling on transient-failure retries per job in the funded poll loop.
# Each attempt re-runs the (potentially expensive) on_job handler, so
# retries are bounded instead of running until the job expires.
_MAX_JOB_ATTEMPTS = 5


@dataclass
class ERC8183State:
    """Shared state for ERC-8183 routes."""

    config: ERC8183Config
    job_ops: ERC8183JobOps
    negotiation_handler: NegotiationHandler
    payment_token: str = ""
    payment_token_decimals: int = 18

    def __repr__(self) -> str:
        return (
            f"ERC8183State("
            f"agent_address='{self.job_ops.agent_address}', "
            f"commerce='{self.config.effective_commerce_address}')"
        )


def create_erc8183_state(config: ERC8183Config | None = None) -> ERC8183State:
    """Build ``ERC8183State`` from config (env fallback) with sensible defaults."""
    if config is None:
        config = ERC8183Config.from_env()

    if config.wallet_provider is None:
        raise ValueError(
            "ERC8183Config.wallet_provider is required to build ERC8183State. "
            "Pass a wallet_provider= or set WALLET_PASSWORD (+ PRIVATE_KEY)."
        )

    storage = config.storage or LocalStorageProvider()

    if isinstance(storage, StorageProvider) and storage.uses_file_url and not config.agent_url:
        raise ValueError(
            f"ERC8183_AGENT_URL must be set when using {type(storage).__name__}. "
            "Set it to the agent's public base URL including /erc8183 "
            "(e.g. http://localhost:8003/erc8183)."
        )

    job_ops = ERC8183JobOps(
        config.wallet_provider,
        network=config.effective_network,
        storage_provider=storage,
        service_price=int(config.service_price),
        agent_url=config.agent_url,
    )

    # Fetch payment token + decimals once at startup so /status responses
    # don't cost an RPC per request. Non-fatal if lookup fails (e.g. RPC
    # down during boot); we degrade to unknown and let later calls retry.
    currency = ""
    decimals = 18
    try:
        currency = job_ops.erc8183_client.payment_token
        decimals = job_ops.erc8183_client.token_decimals()
    except Exception as exc:
        logger.warning(f"[ERC-8183] payment_token lookup failed: {exc}")

    # Bind the negotiation signature to this chain + commerce contract so the
    # provider_sig cannot be replayed across networks. Pulls both fields from
    # the live ERC-8183 client to avoid duplicating network config here.
    negotiation_handler = NegotiationHandler(
        service_price=config.service_price,
        currency=currency,
        wallet_provider=config.wallet_provider,
        chain_id=job_ops.erc8183_client.network.chain_id,
        verifying_contract=job_ops.erc8183_client.commerce.address,
    )

    return ERC8183State(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
        payment_token=currency,
        payment_token_decimals=decimals,
    )


def _build_negotiate_limiter() -> SlidingWindowLimiter:
    """Read ERC8183_NEGOTIATE_RATE_LIMIT / ERC8183_NEGOTIATE_RATE_WINDOW from env."""
    raw_max = get_env("NEGOTIATE_RATE_LIMIT", "120", prefix=ERC8183_ENV_PREFIX) or "120"
    raw_window = (
        get_env("NEGOTIATE_RATE_WINDOW", "60.0", prefix=ERC8183_ENV_PREFIX) or "60.0"
    )
    try:
        max_requests = int(raw_max)
    except ValueError:
        logger.warning(
            f"[ERC-8183] ERC8183_NEGOTIATE_RATE_LIMIT={raw_max!r} invalid, using 120"
        )
        max_requests = 120
    try:
        window_seconds = float(raw_window)
    except ValueError:
        logger.warning(
            f"[ERC-8183] ERC8183_NEGOTIATE_RATE_WINDOW={raw_window!r} invalid, using 60.0"
        )
        window_seconds = 60.0
    raw_max_keys = (
        get_env("RATE_LIMIT_MAX_KEYS", "10000", prefix=ERC8183_ENV_PREFIX) or "10000"
    )
    try:
        max_keys = int(raw_max_keys)
    except ValueError:
        logger.warning(
            f"[ERC-8183] ERC8183_RATE_LIMIT_MAX_KEYS={raw_max_keys!r} invalid, using 10000"
        )
        max_keys = 10_000
    return SlidingWindowLimiter(
        max_requests=max_requests, window_seconds=window_seconds, max_keys=max_keys
    )


def _create_erc8183_routes(state: ERC8183State) -> APIRouter:
    router = APIRouter(tags=["ERC-8183"])
    negotiate_limiter = _build_negotiate_limiter()

    @router.get("/job/{job_id}")
    async def get_job(job_id: int):
        result = await state.job_ops.get_job(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=result.get("error_code", 500))
        if "status" in result and hasattr(result["status"], "value"):
            result["status"] = result["status"].value
        return JSONResponse(result)

    @router.get("/job/{job_id}/response")
    async def get_job_response(job_id: int):
        result = await state.job_ops.get_response(job_id)
        if not result.get("success"):
            # get_response distinguishes "no response exists" (404) from
            # "deliverable exists but temporarily unresolvable" (503).
            return JSONResponse(result, status_code=result.get("error_code") or 404)
        return JSONResponse(result)

    @router.get("/job/{job_id}/verify")
    async def verify_job(job_id: int):
        result = await state.job_ops.verify_job(job_id)
        return JSONResponse(
            result,
            status_code=200 if result.get("valid") else result.get("error_code", 400),
        )

    @router.post("/negotiate")
    async def negotiate(request: Request):
        client_ip = request.client.host if request.client else "unknown"
        negotiate_limiter.check(client_ip)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or "terms" not in body:
            return JSONResponse(
                {
                    "error": (
                        "Request must include 'terms' with"
                        " deliverables, quality_standards"
                    )
                },
                status_code=400,
            )
        try:
            result = state.negotiation_handler.negotiate(body)
            return JSONResponse(result.to_dict())
        except Exception as exc:
            logger.error(f"[ERC-8183] Negotiation failed: {exc}")
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
        return {"status": "ok", "service": "ERC-8183 Agent"}

    return router


def create_erc8183_app(
    config: ERC8183Config | None = None,
    on_job: Callable[..., Any] | None = None,
    on_job_skipped: Callable[[dict, str], Any] | None = None,
    task_metadata: dict[str, Any] | None = None,
    prefix: str = "/erc8183",
    funded_poll_interval: float | None = None,
) -> FastAPI:
    """Create a FastAPI application for an ERC-8183 provider agent.

    Parameters
    ----------
    on_job
        Job handler invoked for each pending funded job. One of::

            def on_job(job: dict) -> str
            async def on_job(job: dict) -> str
            def on_job(job: dict) -> tuple[str, dict]    # per-job metadata
            async def on_job(job: dict) -> tuple[str, dict]

        When set, a background poll loop scans on-chain for newly funded jobs
        and dispatches each through ``on_job``. The SDK handles verification
        and submission internally.
    funded_poll_interval
        Seconds between funded-job poll passes. Falls back to the
        ``ERC8183_FUNDED_POLL_INTERVAL`` env var (default ``30``).
    """
    state = create_erc8183_state(config)
    effective_poll_interval = funded_poll_interval or float(
        get_env("FUNDED_POLL_INTERVAL", "30.0", prefix=ERC8183_ENV_PREFIX) or "30.0"
    )

    processing_jobs: set[int] = set()
    background_tasks: set[asyncio.Task] = set()
    stop_event = asyncio.Event()
    is_async_on_job = inspect.iscoroutinefunction(on_job) if on_job else False

    async def _execute_job_internal(job_id: int) -> dict:
        verification = await state.job_ops.verify_job(job_id)
        if not verification.get("valid"):
            reason = verification.get("error", "unknown")
            error_code = verification.get("error_code")
            logger.warning(
                f"[ERC-8183] Job #{job_id} skipped: {reason}"
                + (f" (error_code={error_code})" if error_code is not None else "")
            )
            if on_job_skipped:
                try:
                    target = verification.get("job", {"jobId": job_id})
                    if inspect.iscoroutinefunction(on_job_skipped):
                        await on_job_skipped(target, reason)
                    else:
                        await asyncio.to_thread(on_job_skipped, target, reason)
                except Exception as exc:
                    logger.error(f"[ERC-8183] on_job_skipped callback error: {exc}")
            return {"success": False, "error": reason, "error_code": error_code}

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
            logger.info(f"[ERC-8183] Job #{job_id} submitted, tx={submission.get('txHash')}")
        else:
            logger.error(f"[ERC-8183] Job #{job_id} submission failed: {submission.get('error')}")
        return submission

    # Transiently-failed jobs queued for retry: job_id -> failed attempts so
    # far. get_pending_jobs reports each FUNDED job only once (cursor-based),
    # so a job that fails here would otherwise be lost until process restart.
    retry_attempts: dict[int, int] = {}

    async def _attempt_job(job_id: int) -> None:
        if job_id in processing_jobs:
            return
        processing_jobs.add(job_id)
        try:
            submission = await _execute_job_internal(job_id)
            if submission.get("success"):
                retry_attempts.pop(job_id, None)
                return
            code = submission.get("error_code")
            if isinstance(code, int) and 400 <= code < 500:
                # Permanent (not provider, expired, under-priced, oversize,
                # no longer FUNDED, ...) — retrying cannot succeed.
                retry_attempts.pop(job_id, None)
                return
            _schedule_retry(job_id, submission.get("error"))
        except Exception as exc:
            logger.error(f"[ERC-8183] Funded-poll job #{job_id} failed: {exc}")
            _schedule_retry(job_id, exc)
        finally:
            processing_jobs.discard(job_id)

    def _schedule_retry(job_id: int, reason) -> None:
        attempts = retry_attempts.get(job_id, 0) + 1
        if attempts >= _MAX_JOB_ATTEMPTS:
            retry_attempts.pop(job_id, None)
            logger.error(
                f"[ERC-8183] Job #{job_id} giving up after {attempts} attempts: {reason}"
            )
            return
        retry_attempts[job_id] = attempts
        logger.warning(
            f"[ERC-8183] Job #{job_id} attempt {attempts}/{_MAX_JOB_ATTEMPTS} failed; "
            f"will retry next poll: {reason}"
        )

    async def _funded_poll_loop():
        logger.info(
            f"[ERC-8183] Funded-job poll loop starting (interval={effective_poll_interval:.1f}s)"
        )
        try:
            while True:
                try:
                    # Retries first, so a job failing below is not re-attempted
                    # within the same tick. _execute_job_internal re-verifies,
                    # dropping jobs that left FUNDED with a permanent 4xx.
                    for job_id in list(retry_attempts):
                        await _attempt_job(job_id)

                    result = await state.job_ops.get_pending_jobs()
                    if result.get("success"):
                        jobs = result.get("jobs", [])
                        if jobs:
                            logger.info(
                                f"[ERC-8183] Funded-poll picked up {len(jobs)} pending job(s)"
                            )
                        for job in jobs:
                            await _attempt_job(job["jobId"])
                    else:
                        logger.warning(
                            f"[ERC-8183] Funded-poll error: {result.get('error')}"
                        )
                except Exception as exc:
                    logger.error(f"[ERC-8183] Funded-poll iteration failed: {exc}")

                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=effective_poll_interval
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.info("[ERC-8183] Funded-job poll loop cancelled")
            raise

    def _spawn(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return task

    @asynccontextmanager
    async def erc8183_lifespan(_: FastAPI):
        if on_job:
            _spawn(_funded_poll_loop())
        yield
        stop_event.set()
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    erc8183_app = FastAPI(
        title="ERC-8183 Agent",
        description="ERC-8183 provider agent (AgenticCommerce + Router + OptimisticPolicy)",
        lifespan=erc8183_lifespan,
    )

    router = _create_erc8183_routes(state=state)
    erc8183_app.include_router(router, prefix=prefix)

    if prefix:

        @erc8183_app.get("/")
        async def root():
            endpoints = {
                "job": f"{prefix}/job/{{job_id}}",
                "response": f"{prefix}/job/{{job_id}}/response",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "negotiate": f"{prefix}/negotiate",
                "status": f"{prefix}/status",
                "health": f"{prefix}/health",
            }
            return {
                "service": "ERC-8183 Agent",
                "agent_address": state.job_ops.agent_address,
                "endpoints": endpoints,
            }

    erc8183_app.state.erc8183 = state
    if on_job:
        erc8183_app.state.startup = lambda: _spawn(_funded_poll_loop())

    return erc8183_app
