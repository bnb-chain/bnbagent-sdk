"""Service configuration (``EVAL_SERVER_*`` environment variables)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import get_env, get_env_number
from .policy import PolicyLimits

METHOD_KINDS = ("rule", "judge", "model", "vendor")


def parse_kinds(raw: str) -> tuple[str, ...]:
    """``"rule,judge,vendor"`` → ``("rule", "judge", "vendor")``; unknown kinds raise."""
    kinds = tuple(dict.fromkeys(k.strip().lower() for k in raw.split(",") if k.strip()))
    unknown = [k for k in kinds if k not in METHOD_KINDS]
    if unknown or not kinds:
        raise ValueError(
            f"allowed kinds must be a comma-separated subset of {','.join(METHOD_KINDS)}: "
            f"got {raw!r}"
        )
    return kinds


@dataclass(frozen=True)
class ServiceConfig:
    name: str = "bnbagent-eval"
    base_url: str | None = None  # public URL (agent card); default: the request's base URL
    store_path: str = ".eval-cache/service.db"
    no_retention: bool = False
    job_ttl_s: float = 24 * 3600
    idempotency_ttl_s: float = 24 * 3600
    credit_ttl_s: float = 30 * 86400
    eval_timeout_s: float = 600.0
    rate_per_s: float = 5.0  # token bucket per client IP
    rate_burst: int = 20
    max_running_jobs: int = 64  # concurrent async jobs (503 beyond)
    limits: PolicyLimits = field(default_factory=PolicyLimits)
    allowed_kinds: tuple[str, ...] = ("rule", "judge")  # opt in to "vendor" / "model"

    @classmethod
    def from_env(cls) -> ServiceConfig:
        d = PolicyLimits()
        limits = PolicyLimits(
            max_request_bytes=get_env_number("SERVER_MAX_REQUEST_BYTES", d.max_request_bytes, int),
            max_cases=get_env_number("SERVER_MAX_CASES", d.max_cases, int),
            max_runs=get_env_number("SERVER_MAX_RUNS", d.max_runs, int),
            max_judge_calls=get_env_number("SERVER_MAX_JUDGE_CALLS", d.max_judge_calls, int),
        )
        no_retention = (get_env("SERVER_NO_RETENTION") or "").lower() in ("1", "true", "yes")
        return cls(
            base_url=get_env("SERVER_BASE_URL"),
            store_path=get_env("SERVER_STORE", cls.store_path) or cls.store_path,
            no_retention=no_retention,
            rate_per_s=get_env_number("SERVER_RATE_PER_S", cls.rate_per_s),
            rate_burst=get_env_number("SERVER_RATE_BURST", cls.rate_burst, int),
            job_ttl_s=get_env_number("SERVER_JOB_TTL_S", cls.job_ttl_s),
            idempotency_ttl_s=get_env_number("SERVER_IDEMPOTENCY_TTL_S", cls.idempotency_ttl_s),
            credit_ttl_s=get_env_number("SERVER_CREDIT_TTL_S", cls.credit_ttl_s),
            eval_timeout_s=get_env_number("SERVER_EVAL_TIMEOUT_S", cls.eval_timeout_s),
            limits=limits,
            allowed_kinds=parse_kinds(get_env("SERVER_ALLOWED_KINDS") or "rule,judge"),
        )
