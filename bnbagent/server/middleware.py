"""
ApexMiddleware — FastAPI middleware for job verification.

Provides automatic job verification for agent endpoints:
- Extracts X-Job-Id header
- Verifies job exists on-chain and is in processable phase
- Validates agentId matches the route
- Checks deadlines
- Optionally auto-accepts jobs

Error codes:
- 402: Missing X-Job-Id header
- 403: Agent ID mismatch
- 404: Job not found on-chain
- 408: Deadline passed
- 409: Job already processed (Asserting, Completed, Disputed)
- 410: Job cancelled or refunded

Example:
    from bnbagent.server import ApexMiddleware
    from bnbagent import JobVerifier, ApexClient

    verifier = JobVerifier(
        apex_client=client,
        agent_routes="blockchain-news:42,translation:67",
    )

    app.add_middleware(
        ApexMiddleware,
        job_verifier=verifier,
        skip_paths=["/status", "/health", "/.well-known/"],
        auto_accept=True,
    )
"""

import asyncio
import json
import logging
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

JOB_ID_HEADER = "x-job-id"

DEFAULT_SKIP_PATHS = [
    "/status",
    "/health",
    "/metrics",
    "/.well-known/",
    "/admin/",
    "/negotiate",
    "/submit-result",
    "/reject-job",
]


class ApexMiddleware:
    """
    FastAPI/Starlette middleware for job verification.

    Verifies that incoming requests have valid job IDs and that the jobs
    are in the correct state for processing.
    """

    def __init__(
        self,
        app,
        job_verifier: "JobVerifier",
        skip_paths: Optional[List[str]] = None,
        auto_accept: bool = True,
        auto_mark_used: bool = True,
    ):
        """
        Initialize the middleware.

        Args:
            app: The ASGI application
            job_verifier: JobVerifier instance for verification
            skip_paths: List of path prefixes/segments to skip verification
            auto_accept: Whether to automatically accept jobs in PaymentLocked phase
            auto_mark_used: Deprecated, ignored. On-chain phase is used for replay protection.
        """
        self.app = app
        self._verifier = job_verifier
        self._skip_paths = skip_paths if skip_paths is not None else DEFAULT_SKIP_PATHS
        self._auto_accept = auto_accept
        self._auto_mark_used = auto_mark_used

        logger.info(
            f"[ApexMiddleware] Initialized with skip_paths={self._skip_paths}, "
            f"auto_accept={auto_accept}, auto_mark_used={auto_mark_used}"
        )

    async def __call__(self, scope, receive, send):
        """ASGI middleware interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        if self._should_skip(path):
            await self.app(scope, receive, send)
            return

        if method != "POST":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        job_id_bytes = headers.get(JOB_ID_HEADER.encode(), b"")
        job_id_str = job_id_bytes.decode() if job_id_bytes else ""

        if not job_id_str:
            await self._send_error(
                send, 402, "Job verification required. Include X-Job-Id header."
            )
            return

        try:
            job_id = int(job_id_str.strip())
        except ValueError:
            await self._send_error(send, 400, "Invalid job ID format: must be an integer.")
            return

        result = self._verifier.verify(
            job_id=job_id,
            request_path=path,
            check_deadline=True,
            check_replay=True,
        )

        if not result.valid:
            await self._send_error(send, result.error_code, result.error or "Job verification failed")
            return

        logger.info(
            f"[ApexMiddleware] Verified job {job_id} "
            f"(agent={result.agent_id}, phase={result.phase.name if result.phase else 'unknown'})"
        )

        response_started = False
        response_status = 0

        async def send_wrapper(message):
            nonlocal response_started, response_status
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message.get("status", 200)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if response_started and 200 <= response_status < 300:
            if self._auto_mark_used:
                self._verifier.mark_used(job_id)

            if self._auto_accept and result.needs_accept:
                asyncio.create_task(self._accept_job_async(job_id))

    def _should_skip(self, path: str) -> bool:
        """Check if path should skip verification."""
        path_lower = path.lower()
        return any(skip.lower() in path_lower for skip in self._skip_paths)

    async def _accept_job_async(self, job_id: int) -> None:
        """Accept job in background."""
        try:
            result = self._verifier.accept_job(job_id)
            tx_hash = result.get("transactionHash", "")
            logger.info(f"[ApexMiddleware] acceptJob({job_id}) success: {tx_hash}")
        except Exception as e:
            logger.error(f"[ApexMiddleware] acceptJob({job_id}) error: {e}")

    async def _send_error(self, send, status_code: int, message: str) -> None:
        """Send error response."""
        body = json.dumps({"error": message}).encode()
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


def create_apex_middleware(
    job_verifier: "JobVerifier",
    skip_paths: Optional[List[str]] = None,
    auto_accept: bool = True,
    auto_mark_used: bool = True,
):
    """
    Factory function to create ApexMiddleware for use with app.add_middleware().

    Example:
        app.add_middleware(
            BaseHTTPMiddleware,
            dispatch=create_apex_middleware(verifier).dispatch
        )

    Or use the class directly:
        app.add_middleware(ApexMiddleware, job_verifier=verifier)
    """
    def middleware_factory(app):
        return ApexMiddleware(
            app,
            job_verifier=job_verifier,
            skip_paths=skip_paths,
            auto_accept=auto_accept,
            auto_mark_used=auto_mark_used,
        )
    return middleware_factory


from ..job_verifier import JobVerifier
