"""Results: per metric, per run, per case; aggregates; verdict; cost."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._serde import Serializable
from .case import AppMetrics

MetricStatus = Literal["scored", "skipped", "error", "pending_human"]
VerdictStatus = Literal["pass", "fail", "inconclusive", "pending_human"]

# Error codes used in ErrorInfo.code.
ERROR_CODES = frozenset(
    {
        "missing_input",
        "metric_error",
        "judge_parse_error",
        "judge_unavailable",
        "budget_exhausted",
        "deadline",
        "vendor_nan",
        "missing_extra",
        "sandbox_unavailable",
        "policy_denied",
        "target_error",
        "truncated",
    }
)


@dataclass(frozen=True)
class ErrorInfo(Serializable):
    code: str
    message: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class MetricProvenance(Serializable):
    metric_version: str
    method: str
    prompt_hash: str | None = None
    judge: Mapping[str, Any] | None = None  # provider, model, params
    samples: int | None = None
    judge_source: str | None = (
        None  # "live" | "cache:<db_hash>"  (volatile: excluded from result_hash)
    )
    truncated: bool = False


@dataclass(frozen=True)
class MetricResult(Serializable):
    metric_id: str
    name: str
    method: str
    score: float | None
    value: float | None = None
    unit: str | None = None
    passed: bool | None = None
    threshold: float | None = None
    required: bool = False
    weight: float = 1.0
    reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None
    status: MetricStatus = "scored"
    provenance: MetricProvenance | None = None


@dataclass(frozen=True)
class RunResult(Serializable):
    run_index: int
    output_hash: str | None = None
    app_metrics: AppMetrics | None = None
    error: str | None = None


@dataclass(frozen=True)
class CaseResult(Serializable):
    case_id: str
    metrics: tuple[MetricResult, ...]
    score: float | None = None
    passed: bool | None = None
    runs: tuple[RunResult, ...] = ()


@dataclass(frozen=True)
class MetricAggregate(Serializable):
    name: str
    metric_id: str
    count: int
    scored: int
    errors: int
    skipped: int
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    pass_rate: float | None = None
    value_mean: float | None = None
    value_p50: float | None = None
    value_p95: float | None = None
    value_p99: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class Aggregate(Serializable):
    score: float | None
    pass_rate: float | None
    error_rate: float
    cases: int
    metrics: tuple[MetricAggregate, ...] = ()


@dataclass(frozen=True)
class Verdict(Serializable):
    status: VerdictStatus
    action: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True)
class Cost(Serializable):
    judge_calls: int = 0
    tokens: int = 0
    usd_estimate: float = 0.0
