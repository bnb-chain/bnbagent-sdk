"""OpenTelemetry spans: ``eval.run`` -> ``eval.case`` -> ``eval.metric`` (``[exporters]``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..._optional import require
from ...types.request import EvalResponse
from ._common import metric_stats, thresholds

TRACER_NAME = "bnbagent_eval"


def _attrs(values: Mapping[str, Any]) -> dict[str, Any]:
    """OTel attributes may not be None; drop those."""
    return {k: v for k, v in values.items() if v is not None}


def _ns(seconds: float | None) -> int | None:
    return None if seconds is None else int(seconds * 1e9)


def export(
    response: EvalResponse,
    *,
    tracer_provider: Any = None,
    tracer_name: str = TRACER_NAME,
    include_text: bool = False,
    metric_spans: bool = True,
) -> Any:
    """Emit one span tree for ``response`` and return the (ended) root span.

    Uses ``tracer_provider`` if given, else the global provider. Attributes are ``eval.*``;
    metric reasons are only attached with ``include_text=True``.
    """
    trace = require("opentelemetry.trace", "exporters")
    tracer = (
        tracer_provider.get_tracer(tracer_name)
        if tracer_provider is not None
        else trace.get_tracer(tracer_name)
    )
    prov, agg, verdict = response.provenance, response.aggregate, response.verdict
    start, end = _ns(prov.started_at), _ns(prov.finished_at)
    root_attrs: dict[str, Any] = {
        "eval.stage": response.stage,
        "eval.suite_hash": response.suite_hash,
        "eval.result_hash": response.result_hash,
        "eval.response_hash": response.response_hash,
        "eval.request_hash": prov.request_hash,
        "eval.package_version": prov.package_version,
        "eval.schema_version": response.schema_version,
        "eval.verdict": verdict.status,
        "eval.action": verdict.action,
        "eval.score": agg.score,
        "eval.pass_rate": agg.pass_rate,
        "eval.error_rate": agg.error_rate,
        "eval.cases": agg.cases,
        "eval.cost.judge_calls": response.cost.judge_calls,
        "eval.cost.tokens": response.cost.tokens,
    }
    th = thresholds(response)
    for m in agg.metrics:
        for key, val in metric_stats(m, th.get(m.metric_id)).items():
            root_attrs[f"eval.metric.{m.metric_id}.{key}"] = val
    root = tracer.start_span("eval.run", start_time=start, attributes=_attrs(root_attrs))
    if verdict.status == "fail":
        root.set_status(trace.Status(trace.StatusCode.ERROR, "verdict: fail"))
    root_ctx = trace.set_span_in_context(root)
    for case in response.results:
        cspan = tracer.start_span(
            "eval.case",
            context=root_ctx,
            start_time=start,
            attributes=_attrs(
                {
                    "eval.suite_hash": response.suite_hash,
                    "eval.result_hash": response.result_hash,
                    "eval.case_id": case.case_id,
                    "eval.case.score": case.score,
                    "eval.case.passed": case.passed,
                    "eval.case.metrics": len(case.metrics),
                }
            ),
        )
        if metric_spans:
            case_ctx = trace.set_span_in_context(cspan)
            for m in case.metrics:
                attrs = {
                    "eval.case_id": case.case_id,
                    "eval.metric_id": m.metric_id,
                    "eval.metric.name": m.name,
                    "eval.metric.method": m.method,
                    "eval.metric.status": m.status,
                    "eval.metric.score": m.score,
                    "eval.metric.value": m.value,
                    "eval.metric.unit": m.unit,
                    "eval.metric.passed": m.passed,
                    "eval.metric.threshold": m.threshold,
                    "eval.metric.required": m.required,
                    "eval.metric.error_code": m.error.code if m.error else None,
                    "eval.metric.version": m.provenance.metric_version if m.provenance else None,
                }
                if include_text:
                    attrs["eval.metric.reason"] = m.reason
                mspan = tracer.start_span(
                    "eval.metric", context=case_ctx, start_time=start, attributes=_attrs(attrs)
                )
                if m.status == "error":
                    mspan.set_status(
                        trace.Status(
                            trace.StatusCode.ERROR, attrs["eval.metric.error_code"] or "error"
                        )
                    )
                mspan.end(end_time=end)
        cspan.end(end_time=end)
    root.end(end_time=end)
    return root
