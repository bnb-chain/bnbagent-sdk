"""Helpers shared by the exporters (no third-party imports)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.request import EvalResponse
from ...types.result import MetricAggregate


def thresholds(response: EvalResponse) -> dict[str, float]:
    """First threshold seen per metric_id (thresholds are per metric config)."""
    out: dict[str, float] = {}
    for case in response.results:
        for m in case.metrics:
            if m.threshold is not None:
                out.setdefault(m.metric_id, m.threshold)
    return out


def metric_stats(m: MetricAggregate, threshold: float | None) -> dict[str, float]:
    """AgentSight-style per-metric numbers (``avg_score``, ``min_score``, ...); None dropped."""
    stats: dict[str, float | None] = {
        "avg_score": m.mean,
        "min_score": m.min,
        "max_score": m.max,
        "std_score": m.std,
        "pass_rate": m.pass_rate,
        "count": float(m.count),
        "scored": float(m.scored),
        "errors": float(m.errors),
        "skipped": float(m.skipped),
        "threshold": threshold,
        "value_mean": m.value_mean,
        "value_p50": m.value_p50,
        "value_p95": m.value_p95,
        "value_p99": m.value_p99,
    }
    return {k: float(v) for k, v in stats.items() if v is not None}


def _is_content_key(key: str) -> bool:
    # Same convention as ``privacy.is_content_key`` (reporting may not import privacy).
    return key == "excerpt" or key.endswith("_text")


def _strip(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip(v) for k, v in value.items() if not _is_content_key(str(k))}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def report_dict(response: EvalResponse, *, include_text: bool) -> dict[str, Any]:
    """The JSON report; without ``include_text`` reasons and content detail keys are dropped."""
    doc = response.to_dict()
    if include_text:
        return doc
    for case in doc.get("results", []):
        for m in case.get("metrics", []):
            m.pop("reason", None)
            if "details" in m:
                m["details"] = _strip(m["details"])
    return doc
