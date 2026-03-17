"""
APEXMiddleware — FastAPI middleware for APEX job verification.

Provides automatic job verification for agent endpoints:
- Extracts X-Job-Id header
- Verifies job exists on-chain and is in FUNDED status
- Validates provider matches the agent
- Checks expiry

Error codes:
- 402: Missing X-Job-Id header
- 403: Provider mismatch
- 404: Job not found on-chain
- 408: Job expired
- 409: Job not in FUNDED status

Example:
    from bnbagent.apex.server import APEXMiddleware, APEXJobOps

    job_ops = APEXJobOps(
        rpc_url="https://bsc-testnet.bnbchain.org",
        erc8183_address="0x...",
        private_key="0x...",
    )

    app.add_middleware(
        APEXMiddleware,
        job_ops=job_ops,
        skip_paths=["/status", "/health", "/.well-known/"],
    )
"""

import asyncio
import json
import logging
from typing import List, Optional

from ..client import APEXStatus

logger = logging.getLogger(__name__)

JOB_ID_HEADER = "x-job-id"
JOB_VERIFY_TIMEOUT = 30  # seconds

DEFAULT_SKIP_PATHS = [
    "/status",
    "/health",
    "/metrics",
    "/.well-known/",
    "/negotiate",
]


class APEXMiddleware:
    """
    FastAPI/Starlette middleware for ERC-8183 job verification.

    Verifies that incoming requests have valid job IDs and that the jobs
    are in the correct state (FUNDED) for processing.
    """

    def __init__(
        self,
        app,
        job_ops: "APEXJobOps",
        skip_paths: Optional[List[str]] = None,
    ):
        """
        Initialize the middleware.

        Args:
            app: The ASGI application
            job_ops: APEXJobOps instance for verification
            skip_paths: List of path prefixes/segments to skip verification
        """
        self.app = app
        self._job_ops = job_ops
        self._skip_paths = skip_paths if skip_paths is not None else DEFAULT_SKIP_PATHS

        logger.info(
            f"[APEXMiddleware] Initialized with skip_paths={self._skip_paths}"
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

        # Only verify unsafe methods (POST, PUT, PATCH, DELETE)
        if method in ("GET", "HEAD", "OPTIONS"):
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

        try:
            result = await asyncio.wait_for(
                self._job_ops.verify_job(job_id),
                timeout=JOB_VERIFY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await self._send_error(send, 504, "Job verification timed out")
            return
        except Exception as e:
            logger.error(f"[APEXMiddleware] verify_job error: {e}")
            await self._send_error(send, 502, f"Job verification failed: {e}")
            return

        if not result["valid"]:
            await self._send_error(
                send, result.get("error_code", 400), result.get("error", "Job verification failed")
            )
            return

        logger.info(
            f"[APEXMiddleware] Verified job {job_id} "
            f"(provider={result['job']['provider']}, status={result['job']['status'].name})"
        )

        await self.app(scope, receive, send)

    def _should_skip(self, path: str) -> bool:
        """Check if path should skip verification using prefix matching.

        A skip entry matches when the path starts with it AND the next
        character (if any) is ``/`` or the path ends exactly there
        (with or without a trailing slash).
        """
        path_lower = path.lower().rstrip("/")
        for skip in self._skip_paths:
            prefix = skip.lower().rstrip("/")
            if path_lower == prefix or path_lower.startswith(prefix + "/"):
                return True
        return False

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
    job_ops: "APEXJobOps",
    skip_paths: Optional[List[str]] = None,
):
    """
    Factory function to create APEXMiddleware for use with app.add_middleware().

    Example:
        from bnbagent.apex.server import create_apex_middleware, APEXJobOps

        job_ops = APEXJobOps(...)
        app.add_middleware(APEXMiddleware, job_ops=job_ops)
    """
    def middleware_factory(app):
        return APEXMiddleware(
            app,
            job_ops=job_ops,
            skip_paths=skip_paths,
        )
    return middleware_factory


from .job_ops import APEXJobOps
