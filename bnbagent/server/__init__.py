"""
Server-side components for building APEX agent servers.

Provides simplified wrappers for common agent operations:
- APEXJobOps: Async job lifecycle operations (submit, verify, discover)
- APEXMiddleware: FastAPI/Starlette middleware for job verification
- create_apex_middleware: Factory function for middleware creation

For settlement operations, use APEXEvaluatorClient directly:
- settle_job(job_id): Settle after liveness period
- is_settleable(job_id): Check if settlement is possible
- get_assertion_info(job_id): Query assertion status
"""

from .apex_job_ops import APEXJobOps
from .apex_middleware import APEXMiddleware, create_apex_middleware

__all__ = [
    "APEXJobOps",
    "APEXMiddleware",
    "create_apex_middleware",
]
