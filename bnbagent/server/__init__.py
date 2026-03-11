"""
Server-side components for building agent servers.

Provides simplified wrappers for common agent operations:

EIP-8183 Agentic Commerce Protocol:
- ACPJobOps: Simplified job lifecycle operations (submit, verify)
- ACPMiddleware: FastAPI/Starlette middleware for job verification
- create_acp_middleware: Factory function for middleware creation

For settlement operations, use OOv3EvaluatorClient directly:
- settle_job(job_id): Settle after liveness period
- is_settleable(job_id): Check if settlement is possible
- get_assertion_info(job_id): Query assertion status
"""

from .acp_job_ops import ACPJobOps
from .acp_middleware import ACPMiddleware, create_acp_middleware

__all__ = [
    # EIP-8183 Agentic Commerce Protocol
    "ACPJobOps",
    "ACPMiddleware",
    "create_acp_middleware",
]
