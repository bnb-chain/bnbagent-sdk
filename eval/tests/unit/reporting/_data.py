"""A hand-built EvalResponse covering scored / error / skipped results and risky text."""

from __future__ import annotations

from bnbagent_eval.types import EvalResponse
from bnbagent_eval.types.request import Provenance
from bnbagent_eval.types.result import (
    Aggregate,
    CaseResult,
    Cost,
    ErrorInfo,
    MetricAggregate,
    MetricProvenance,
    MetricResult,
    Verdict,
)

SECRET = "the user said: my card is 4111"


def response() -> EvalResponse:
    prov = MetricProvenance(metric_version="1", method="rule", judge_source="live")
    a1 = MetricResult(
        metric_id="text.exact",
        name="text.exact",
        method="rule",
        score=1.0,
        passed=True,
        threshold=0.5,
        reason=SECRET,
        details={"output_text": SECRET, "n": 3},
        provenance=prov,
    )
    a2 = MetricResult(
        metric_id="perf.latency",
        name="perf.latency",
        method="rule",
        score=-0.5,
        value=120.0,
        unit="ms",
        reason="=HYPERLINK(1)",
    )
    b1 = MetricResult(
        metric_id="text.exact",
        name="text.exact",
        method="rule",
        score=None,
        status="error",
        error=ErrorInfo(code="missing_input", message="no output"),
        threshold=0.5,
    )
    b2 = MetricResult(
        metric_id="perf.latency", name="perf.latency", method="rule", score=None, status="skipped"
    )
    results = (
        CaseResult(case_id="a", metrics=(a1, a2), score=0.25, passed=True),
        CaseResult(case_id="=cmd|' /C calc'!A0", metrics=(b1, b2), score=None, passed=False),
    )
    agg = Aggregate(
        score=0.25,
        pass_rate=0.5,
        error_rate=0.25,
        cases=2,
        metrics=(
            MetricAggregate(
                name="text.exact",
                metric_id="text.exact",
                count=2,
                scored=1,
                errors=1,
                skipped=0,
                mean=1.0,
                std=0.0,
                min=1.0,
                max=1.0,
                pass_rate=1.0,
            ),
            MetricAggregate(
                name="perf.latency",
                metric_id="perf.latency",
                count=2,
                scored=1,
                errors=0,
                skipped=1,
                mean=-0.5,
                std=0.0,
                min=-0.5,
                max=-0.5,
                value_mean=120.0,
                value_p50=120.0,
                value_p95=120.0,
                value_p99=120.0,
                unit="ms",
            ),
        ),
    )
    return EvalResponse(
        stage="dev",
        suite_hash="0xsuite",
        results=results,
        aggregate=agg,
        verdict=Verdict(status="fail", reasons=("required metric failed",)),
        provenance=Provenance(
            package_version="0.0.0",
            suite_hash="0xsuite",
            request_hash="0xreq",
            started_at=100.0,
            finished_at=101.5,
        ),
        result_hash="0xresult",
        response_hash="0xresponse",
        cost=Cost(judge_calls=2, tokens=50),
    )
