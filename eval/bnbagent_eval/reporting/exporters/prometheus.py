"""Prometheus gauges and counters, optionally pushed to a Pushgateway (``[exporters]``).

Metrics go to the ``registry`` passed in, or to a fresh ``CollectorRegistry`` (never the global
default registry unless you pass ``prometheus_client.REGISTRY`` yourself). Collectors are reused
when the same registry is exported to again, so counters accumulate across runs.
"""

from __future__ import annotations

import re
import weakref
from typing import Any

from ..._optional import require
from ...types.request import EvalResponse
from ._common import metric_stats, thresholds

DEFAULT_PREFIX = "bnbagent_eval"
DEFAULT_JOB = "bnbagent-eval"
RUN_LABELS = ("stage", "suite_hash")
METRIC_LABELS = ("stage", "suite_hash", "metric_id")

# (name suffix, help, labels) of gauges; the per-metric ones follow AgentSight's names.
_RUN_GAUGES = {
    "score": "Aggregate score",
    "pass_rate": "Aggregate pass rate",
    "error_rate": "Aggregate error rate",
    "cases": "Number of cases",
    "verdict_pass": "1 if the verdict is pass, else 0",
}
_METRIC_GAUGES = {
    "avg_score": "Average score",
    "min_score": "Minimum score",
    "max_score": "Maximum score",
    "std_score": "Score standard deviation",
    "pass_rate": "Pass rate",
    "count": "Evaluation count",
    "threshold": "Pass threshold",
    "value_mean": "Mean raw value",
    "value_p50": "Raw value p50",
    "value_p95": "Raw value p95",
    "value_p99": "Raw value p99",
}
_COUNTERS = {
    "runs": ("Evaluations exported", RUN_LABELS),
    "judge_calls": ("Judge calls", RUN_LABELS),
    "tokens": ("Judge tokens", RUN_LABELS),
    "metric_errors": ("Metric results with status error", METRIC_LABELS),
    "metric_skipped": ("Metric results with status skipped", METRIC_LABELS),
}

_collectors: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()


def _sanitize(prefix: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_:]", "_", prefix)
    return name if re.match(r"[a-zA-Z_:]", name) else "_" + name


def _get(prom: Any, registry: Any, prefix: str) -> dict[str, Any]:
    cache = _collectors.setdefault(registry, {})
    key = prefix
    if key not in cache:
        made: dict[str, Any] = {}
        for suffix, doc in _RUN_GAUGES.items():
            made[f"run.{suffix}"] = prom.Gauge(
                f"{prefix}_{suffix}", doc, RUN_LABELS, registry=registry
            )
        for suffix, doc in _METRIC_GAUGES.items():
            made[f"metric.{suffix}"] = prom.Gauge(
                f"{prefix}_metric_{suffix}", doc, METRIC_LABELS, registry=registry
            )
        for suffix, (doc, labels) in _COUNTERS.items():
            # prometheus_client appends ``_total`` to counter names itself.
            made[f"counter.{suffix}"] = prom.Counter(
                f"{prefix}_{suffix}", doc, labels, registry=registry
            )
        cache[key] = made
    return cache[key]


def export(
    response: EvalResponse,
    *,
    registry: Any = None,
    prefix: str = DEFAULT_PREFIX,
    pushgateway: str | None = None,
    job: str = DEFAULT_JOB,
    grouping_key: dict[str, str] | None = None,
    include_text: bool = False,  # accepted for symmetry; Prometheus never carries text
) -> Any:
    """Set gauges / increment counters for ``response``; return the registry used."""
    del include_text
    prom = require("prometheus_client", "exporters")
    if registry is None:
        registry = prom.CollectorRegistry()
    c = _get(prom, registry, _sanitize(prefix))
    run = {"stage": response.stage, "suite_hash": response.suite_hash}
    agg = response.aggregate
    for suffix, value in (
        ("score", agg.score),
        ("pass_rate", agg.pass_rate),
        ("error_rate", agg.error_rate),
        ("cases", agg.cases),
        ("verdict_pass", 1.0 if response.verdict.passed else 0.0),
    ):
        if value is not None:
            c[f"run.{suffix}"].labels(**run).set(value)
    c["counter.runs"].labels(**run).inc()
    c["counter.judge_calls"].labels(**run).inc(response.cost.judge_calls)
    c["counter.tokens"].labels(**run).inc(response.cost.tokens)
    th = thresholds(response)
    for m in agg.metrics:
        labels = {**run, "metric_id": m.metric_id}
        stats = metric_stats(m, th.get(m.metric_id))
        for suffix in _METRIC_GAUGES:
            if suffix in stats:
                c[f"metric.{suffix}"].labels(**labels).set(stats[suffix])
        c["counter.metric_errors"].labels(**labels).inc(m.errors)
        c["counter.metric_skipped"].labels(**labels).inc(m.skipped)
    if pushgateway:
        prom.push_to_gateway(
            pushgateway,
            job=job,
            registry=registry,
            grouping_key=grouping_key
            or {"stage": response.stage, "suite_hash": response.suite_hash},
        )
    return registry
