"""Latency, cost, token usage and reliability (feed the ERC-8004 public metrics)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ...types.suite import MetricConfig
from ...types.trajectory import LlmStep, ToolStep
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register

_OPTIONAL = frozenset({"app_metrics", "trajectory"})


def value_score(value: float, cfg: MetricConfig) -> tuple[float | None, bool | None]:
    """Score a lower-is-better value against value_target / value_max / value_min."""
    tgt, vmax, vmin = cfg.value_target, cfg.value_max, cfg.value_min
    if vmin is not None and value < vmin:
        return 0.0, False
    if vmax is None:
        return (1.0, True) if vmin is not None else (None, None)
    if tgt is not None and tgt < vmax:
        if value <= tgt:
            return 1.0, True
        if value >= vmax:
            return 0.0, value <= vmax
        return 1.0 - (value - tgt) / (vmax - tgt), True
    return (1.0, True) if value <= vmax else (0.0, False)


def _missing(what: str) -> Outcome:
    return Outcome(
        None, status="skipped", error=ErrorInfo("missing_input", f"no {what} available")
    )


def _value_outcome(
    value: float | None, unit: str, ctx: MetricContext, what: str, **details: Any
) -> Outcome:
    if value is None:
        return _missing(what)
    score, passed = value_score(value, ctx.config)
    return Outcome(score=score, value=value, unit=unit, passed=passed, details=details)


def _spec(
    metric_id: str,
    description: str,
    unit: str,
    params: Mapping[str, Any],
    sources,
    version: str = "1",
):
    return MetricSpec(
        id=metric_id,
        category="ops",
        description=description,
        methods=(MethodSpec("rule", Kind.RULE, optional_inputs=_OPTIONAL),),
        params_schema={
            "type": "object",
            "properties": dict(params),
            "additionalProperties": False,
        },
        default_threshold=None,
        unit=unit,
        sources=tuple(sources),
        value_limits=True,
        version=version,
    )


def _steps(case: EvalCase):
    return list(case.trajectory.steps()) if case.trajectory else []


def _sum_durations(steps, kind) -> float | None:
    durations = [s.duration_ms for s in steps if isinstance(s, kind) and s.duration_ms is not None]
    return sum(durations) if durations else None


def trajectory_span_ms(case: EvalCase) -> float | None:
    steps = [s for s in _steps(case) if s.start_ms is not None and s.end_ms is not None]
    if not steps:
        return None
    return max(s.end_ms for s in steps) - min(s.start_ms for s in steps)


@register
class Latency(BaseMetric):
    spec = _spec(
        "ops.latency",
        "Response latency (total, TTFT, inference, tool or other time); aggregated p50/p95/p99.",
        "ms",
        {"component": {"enum": ["total", "ttft", "inference", "tool", "other"]}},
        ("agentsight:SpanMetrics", "bnbagent-eval:new"),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        component = ctx.params.get("component", "total")
        am, steps = case.app_metrics, _steps(case)
        total = am.latency_ms if am and am.latency_ms is not None else trajectory_span_ms(case)
        inference = _sum_durations(steps, LlmStep)
        tool = _sum_durations(steps, ToolStep)
        if component == "total":
            value = total
        elif component == "ttft":
            value = am.ttft_ms if am else None
        elif component == "inference":
            value = inference
        elif component == "tool":
            value = tool
        else:
            value = None if total is None else max(0.0, total - (inference or 0) - (tool or 0))
        return _value_outcome(value, "ms", ctx, f"{component} latency", component=component)


def _usage_totals(case: EvalCase) -> dict[str, float]:
    """Token / cost totals: each figure from app_metrics if reported there, else summed over the
    trajectory steps' usage (app_metrics with only ``cost_usd`` keep the trajectory's tokens)."""
    am = case.app_metrics
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd")
    summed: dict[str, float] = {}
    for step in _steps(case):
        if step.usage is None:
            continue
        for k in keys:
            v = getattr(step.usage, k)
            if v is not None:
                summed[k] = summed.get(k, 0) + v
    reported = {k: getattr(am, k) for k in keys if am is not None and getattr(am, k) is not None}
    return {**summed, **reported}


def _priced_cost(case: EvalCase, table: Mapping[str, Mapping[str, float]]) -> float | None:
    """Cost from tokens × price table (per 1k tokens; key = model id or '*')."""

    def price(model: str | None, inp: float, out: float) -> float | None:
        row = table.get(model or "*") or table.get("*")
        if row is None:
            return None
        return (inp * row.get("input_per_1k", 0) + out * row.get("output_per_1k", 0)) / 1000

    llm_steps = [s for s in _steps(case) if isinstance(s, LlmStep) and s.usage is not None]
    if llm_steps:
        parts = [
            price(s.model, s.usage.input_tokens or 0, s.usage.output_tokens or 0)
            for s in llm_steps
        ]
        return None if any(p is None for p in parts) else sum(parts)
    totals = _usage_totals(case)
    if "input_tokens" not in totals and "output_tokens" not in totals:
        return None
    return price(
        case.metadata.get("model"), totals.get("input_tokens", 0), totals.get("output_tokens", 0)
    )


@register
class Cost(BaseMetric):
    spec = _spec(
        "ops.cost",
        "Cost per call in USD (reported, or tokens × price table).",
        "usd",
        {
            "price_table": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "input_per_1k": {"type": "number", "minimum": 0},
                        "output_per_1k": {"type": "number", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            }
        },
        (
            "agentsight:SpanMetrics.cost",
            "agentsight:bedrock_pricing",
            "bnbagent-eval:new",
        ),
        version="2",  # v2: per-figure fallback from app_metrics to trajectory usage
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        totals = _usage_totals(case)
        value = totals.get("cost_usd")
        source = "reported"
        if value is None and "price_table" in ctx.params:
            value, source = _priced_cost(case, ctx.params["price_table"]), "price_table"
        return _value_outcome(value, "usd", ctx, "cost", source=source)


@register
class TokenUsage(BaseMetric):
    spec = _spec(
        "ops.token_usage",
        "Token usage (total, input, output, cache read or cache write).",
        "tokens",
        {"component": {"enum": ["total", "input", "output", "cache_read", "cache_write"]}},
        ("agentsight:SpanMetrics.tokens", "agentsight:insights.cache_hit_rate"),
        version="2",  # v2: per-figure fallback from app_metrics to trajectory usage
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        component = ctx.params.get("component", "total")
        t = _usage_totals(case)
        if component == "total":
            parts = [t.get("input_tokens"), t.get("output_tokens")]
            value = None if all(p is None for p in parts) else float(sum(p or 0 for p in parts))
        else:
            raw = t.get(f"{component}_tokens")
            value = None if raw is None else float(raw)
        return _value_outcome(
            value, "tokens", ctx, f"{component} token usage", component=component
        )


@register
class Reliability(BaseMetric):
    spec = MetricSpec(
        id="ops.reliability",
        category="ops",
        description="Share of target runs that completed without error (1 - error rate).",
        methods=(
            MethodSpec("rule", Kind.RULE, optional_inputs=frozenset({"runs", "app_metrics"})),
        ),
        params_schema={"type": "object", "additionalProperties": False},
        default_threshold=1.0,
        sources=("bnbagent-eval:new",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        runs = ctx.runs or (case,)
        errors = [r.app_metrics.error for r in runs if r.app_metrics and r.app_metrics.error]
        rate = len(errors) / len(runs)
        return Outcome(
            score=1.0 - rate,
            value=rate,
            unit="error_rate",
            details={"runs": len(runs), "errors": len(errors)},
        )
