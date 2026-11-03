"""Metric framework: specs, context, and the BaseMetric template method."""

from __future__ import annotations

import enum
import inspect
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .._jsonschema import validate as validate_schema
from ..config import EvalConfig
from ..exceptions import JudgeError, JudgeParseError, JudgeThrottled, MissingExtraError
from ..judges.base import JudgeModel
from ..types.case import EvalCase
from ..types.options import RunOptions
from ..types.result import ErrorInfo, MetricProvenance, MetricResult, MetricStatus
from ..types.suite import MetricConfig

__all__ = [
    "INPUT_NAMES",
    "BaseMetric",
    "Kind",
    "MethodSpec",
    "MetricContext",
    "MetricSpec",
    "Outcome",
]

logger = logging.getLogger(__name__)

# Input names a MethodSpec may require (EvalCase fields plus derived inputs).
INPUT_NAMES = frozenset(
    {
        "input",
        "output",
        "expected",
        "contexts",
        "conversation",
        "trajectory",
        "app_metrics",
        "artifacts",
        "tx",
        "runs",
    }
)


class Kind(str, enum.Enum):
    RULE = "rule"
    JUDGE = "judge"
    MODEL = "model"
    VENDOR = "vendor"


@dataclass(frozen=True)
class MethodSpec:
    name: str
    kind: Kind
    required_inputs: frozenset[str] = frozenset()
    optional_inputs: frozenset[str] = frozenset()
    inverted: bool = False  # source polarity is "higher is worse"
    raw_range: tuple[float, float] = (0.0, 1.0)
    extra: str | None = None  # pip extra providing ``extra_module``
    extra_module: str | None = None  # importable module checked at resolution
    service_allowed: bool = True

    def __post_init__(self) -> None:
        unknown = (self.required_inputs | self.optional_inputs) - INPUT_NAMES
        if unknown:
            raise ValueError(f"unknown input names {sorted(unknown)}")
        lo, hi = self.raw_range
        if not hi > lo:
            raise ValueError("raw_range must be (low, high) with high > low")


@dataclass(frozen=True)
class MetricSpec:
    id: str
    category: str
    description: str
    methods: tuple[MethodSpec, ...]
    params_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    default_threshold: float | None = 0.5
    unit: str | None = None
    version: str = "1"
    aliases: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    # Scores its ``value`` against MetricConfig.value_min / value_max / value_target.
    value_limits: bool = False

    def method(self, name: str | None = None) -> MethodSpec:
        if name is None:
            return self.methods[0]
        for m in self.methods:
            if m.name == name:
                return m
        raise KeyError(f"metric {self.id} has no method {name!r} (available: {self.method_names})")

    @property
    def method_names(self) -> list[str]:
        return [m.name for m in self.methods]

    def validate_params(self, params: Mapping[str, Any]) -> list[str]:
        return validate_schema(dict(params), self.params_schema, "params")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "default_threshold": self.default_threshold,
            "unit": self.unit,
            "value_limits": self.value_limits,
            "aliases": list(self.aliases),
            "sources": list(self.sources),
            "params_schema": dict(self.params_schema),
            "methods": [
                {
                    "name": m.name,
                    "kind": m.kind.value,
                    "required_inputs": sorted(m.required_inputs),
                    "optional_inputs": sorted(m.optional_inputs),
                    "inverted": m.inverted,
                    "raw_range": list(m.raw_range),
                    "extra": m.extra,
                    "service_allowed": m.service_allowed,
                }
                for m in self.methods
            ],
        }


@dataclass
class MetricContext:
    """Everything a metric needs besides the case."""

    config: MetricConfig
    method: MethodSpec
    params: Mapping[str, Any]
    options: RunOptions = field(default_factory=RunOptions)
    judge: JudgeModel | None = None
    budget: Any = None  # engine.budget.BudgetTracker (judge metrics)
    deadline: float | None = None
    language: str | None = None
    reader: Any = None  # ports.ChainReaderPort (onchain.*)
    cache: Any = None  # judges.cache.JudgeCache
    storage: Any = None  # ports.StoragePort (artifact fetching, human review, ...)
    stage: str = "dev"
    runs: tuple[EvalCase, ...] = ()  # every run of this case (for ops.consistency / reliability)
    eval_config: EvalConfig = field(default_factory=EvalConfig)
    clock: Callable[[], float] = time.time
    logger: logging.Logger = logger


@dataclass(frozen=True)
class Outcome:
    """What a metric method returns. ``score`` is on the method's raw scale (``raw_range``)."""

    score: float | None
    value: float | None = None
    unit: str | None = None
    reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    status: MetricStatus = "scored"
    error: ErrorInfo | None = None
    passed: bool | None = None  # value-based metrics: pass/fail on value limits (no threshold)
    threshold: float | None = None  # method-specific default threshold (e.g. per strategy)
    provenance: Mapping[str, Any] = field(default_factory=dict)  # prompt_hash, judge, samples, ...


class StopScoring(Exception):
    """Raised inside a metric to stop with ``status=skipped`` (deadline / budget / truncation)."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class BaseMetric:
    """Template method: input check → deadline → dispatch → normalize → threshold → result.

    Subclasses set ``spec`` and implement ``_evaluate_<method>(case, ctx)`` (sync or async)
    returning an :class:`Outcome` or a raw float. Exceptions never escape ``evaluate``.
    """

    spec: ClassVar[MetricSpec]

    def has_input(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if name == "runs":
            return len(ctx.runs) >= 2
        if name == "conversation" and not case.conversation and case.trajectory is not None:
            # Turns derived from the trajectory's agent steps also count as a conversation.
            from ..adapters.traces.render import session_messages

            return len(session_messages(case.trajectory)) > 0
        return case.has(name)

    async def evaluate(self, case: EvalCase, ctx: MetricContext) -> MetricResult:
        method = ctx.method
        missing = sorted(n for n in method.required_inputs if not self.has_input(n, case, ctx))
        if missing:
            return self._result(
                ctx,
                Outcome(
                    None,
                    status="skipped",
                    error=ErrorInfo(
                        "missing_input", f"missing required input(s): {', '.join(missing)}"
                    ),
                ),
            )
        if ctx.deadline is not None and ctx.clock() >= ctx.deadline:
            return self._result(
                ctx,
                Outcome(
                    None,
                    status="skipped",
                    error=ErrorInfo("deadline", "deadline reached before scoring"),
                ),
            )
        handler = getattr(self, f"_evaluate_{method.name}", None)
        if handler is None:
            from .registry import method_handler  # extra (vendor) methods; avoids an import cycle

            extra = method_handler(self.spec.id, method.name)
            if extra is not None:
                handler = lambda c, x, _h=extra: _h(self, c, x)  # noqa: E731
        if handler is None:
            return self._result(
                ctx,
                Outcome(
                    None,
                    status="error",
                    error=ErrorInfo("metric_error", f"method {method.name!r} is not implemented"),
                ),
            )
        try:
            raw = handler(case, ctx)
            if inspect.isawaitable(raw):
                raw = await raw
        except StopScoring as stop:
            return self._result(
                ctx, Outcome(None, status="skipped", error=ErrorInfo(stop.code, str(stop)))
            )
        except JudgeParseError as exc:
            return self._result(
                ctx, Outcome(None, status="error", error=ErrorInfo("judge_parse_error", str(exc)))
            )
        except MissingExtraError as exc:  # an optional module imported at run time
            return self._result(
                ctx, Outcome(None, status="error", error=ErrorInfo("missing_extra", str(exc)))
            )
        except JudgeError as exc:
            retryable = isinstance(exc, JudgeThrottled)
            return self._result(
                ctx,
                Outcome(
                    None,
                    status="error",
                    error=ErrorInfo(
                        "judge_unavailable", f"{type(exc).__name__}: {exc}", retryable=retryable
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - metric failures are data, not exceptions
            ctx.logger.debug("metric %s failed", self.spec.id, exc_info=True)
            return self._result(
                ctx,
                Outcome(
                    None,
                    status="error",
                    error=ErrorInfo("metric_error", f"{type(exc).__name__}: {exc}"),
                ),
            )
        outcome = raw if isinstance(raw, Outcome) else Outcome(score=_as_float(raw))
        # A NaN score would clamp to 0 and an infinite one to 1 unnoticed (and canonical JSON
        # rejects both when the result is hashed).
        bad = [
            f"{name} {x!r}"
            for name, x in (("score", outcome.score), ("value", outcome.value))
            if isinstance(x, float) and not math.isfinite(x)
        ]
        if bad:
            return self._result(
                ctx,
                Outcome(
                    None, status="error", error=ErrorInfo("metric_error", "non-finite " + bad[0])
                ),
            )
        return self._result(ctx, outcome)

    def normalize(self, raw: float, method: MethodSpec) -> float:
        lo, hi = method.raw_range
        score = (raw - lo) / (hi - lo)
        if method.inverted:
            score = 1.0 - score
        return min(1.0, max(0.0, score))

    def _result(self, ctx: MetricContext, outcome: Outcome) -> MetricResult:
        cfg, method = ctx.config, ctx.method
        details = dict(outcome.details)
        score: float | None = None
        if outcome.status == "scored" and outcome.score is not None:
            score = self.normalize(outcome.score, method)
            if method.inverted or method.raw_range != (0.0, 1.0):
                details.setdefault("raw", outcome.score)
        threshold = cfg.threshold
        if threshold is None:
            threshold = (
                outcome.threshold if outcome.threshold is not None else self.spec.default_threshold
            )
        passed = None
        if (
            score is not None
            and cfg.threshold is None
            and outcome.threshold is None
            and outcome.passed is not None
        ):
            passed = outcome.passed
        elif score is not None and threshold is not None:
            passed = score >= threshold
        return MetricResult(
            metric_id=self.spec.id,
            name=cfg.key,
            method=method.name,
            score=score,
            value=outcome.value,
            unit=outcome.unit or self.spec.unit,
            passed=passed,
            threshold=threshold,
            required=cfg.required,
            weight=cfg.weight,
            reason=outcome.reason,
            details=details,
            error=outcome.error,
            status=outcome.status,  # "scored" with score=None is an informational result
            provenance=MetricProvenance(
                metric_version=self.spec.version, method=method.name, **dict(outcome.provenance)
            ),
        )


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value)
