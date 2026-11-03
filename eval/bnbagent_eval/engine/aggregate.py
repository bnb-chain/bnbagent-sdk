"""Aggregation, pass rules, verdict precedence and stage actions."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from ..types.request import Stage
from ..types.result import (
    Aggregate,
    CaseResult,
    MetricAggregate,
    MetricResult,
    Verdict,
    VerdictStatus,
)
from ..types.suite import AggregationMode, PassRule

STAGE_ACTIONS: dict[str, dict[str, str | None]] = {
    "dev": {"pass": None, "fail": None, "inconclusive": None, "pending_human": None},
    "registration": {
        "pass": "publishable",
        "fail": "not_publishable",
        "inconclusive": "not_publishable",
        "pending_human": "not_publishable",
    },
    "pre_submit": {
        "pass": "submit",
        "fail": "retry_or_decline",
        "inconclusive": None,
        "pending_human": None,
    },  # inconclusive: PassRule.on_inconclusive
    "review": {
        "pass": "no_action",
        "fail": "dispute",
        "inconclusive": "no_action",
        "pending_human": "no_action",
    },
    "vote": {
        "pass": "abstain",
        "fail": "vote_reject",
        "inconclusive": "abstain",
        "pending_human": "abstain",
    },
}


class Welford:
    """Streaming mean / variance / min / max."""

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.min = math.inf
        self.max = -math.inf

    def add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)
        self.min = min(self.min, x)
        self.max = max(self.max, x)

    @property
    def std(self) -> float:
        """Population standard deviation."""
        return math.sqrt(self._m2 / self.n) if self.n else 0.0


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation percentile (``q`` in [0, 100])."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def combine(results: Iterable[MetricResult], mode: AggregationMode) -> float | None:
    """Combine the scored results of one case under ``mode``."""
    scored = [r for r in results if r.status == "scored" and r.score is not None]
    if not scored:
        return None
    scores = [r.score for r in scored]
    if mode == "min":
        return min(scores)
    if mode == "max":
        return max(scores)
    if mode == "mean":
        return sum(scores) / len(scores)
    total = sum(r.weight for r in scored)
    if total == 0:
        return None
    return sum(r.score * r.weight for r in scored) / total


def _is_failed(r: MetricResult) -> bool:
    return r.status == "scored" and r.passed is False


def _is_unscored(r: MetricResult) -> bool:
    return r.status in ("error", "skipped")


def case_passed(
    results: Sequence[MetricResult], score: float | None, rule: PassRule
) -> bool | None:
    """All required metrics pass and the case score reaches ``min_aggregate``."""
    if any(r.required and (_is_failed(r) or _is_unscored(r)) for r in results):
        return False
    if score is None:
        return None
    return score >= rule.min_aggregate


def build_case(
    case_id: str, results: Sequence[MetricResult], rule: PassRule, runs=()
) -> CaseResult:
    score = combine(results, rule.aggregation)
    return CaseResult(
        case_id=case_id,
        metrics=tuple(results),
        score=score,
        passed=case_passed(results, score, rule),
        runs=tuple(runs),
    )


def aggregate(cases: Sequence[CaseResult]) -> Aggregate:
    by_name: dict[str, list[MetricResult]] = {}
    for case in cases:
        for r in case.metrics:
            by_name.setdefault(r.name, []).append(r)
    metric_aggs = []
    for name, results in by_name.items():
        w = Welford()
        values: list[float] = []
        passed = judged = 0
        for r in results:
            if r.status == "scored" and r.score is not None:
                w.add(r.score)
            if r.value is not None:
                values.append(r.value)
            if r.passed is not None:
                judged += 1
                passed += int(r.passed)
        metric_aggs.append(
            MetricAggregate(
                name=name,
                metric_id=results[0].metric_id,
                count=len(results),
                scored=w.n,
                errors=sum(r.status == "error" for r in results),
                skipped=sum(r.status == "skipped" for r in results),
                mean=w.mean if w.n else None,
                std=w.std if w.n else None,
                min=w.min if w.n else None,
                max=w.max if w.n else None,
                pass_rate=passed / judged if judged else None,
                value_mean=sum(values) / len(values) if values else None,
                value_p50=percentile(values, 50),
                value_p95=percentile(values, 95),
                value_p99=percentile(values, 99),
                unit=results[0].unit,
            )
        )
    case_scores = [c.score for c in cases if c.score is not None]
    judged_cases = [c for c in cases if c.passed is not None]
    all_results = [r for c in cases for r in c.metrics]
    unscored = sum(_is_unscored(r) for r in all_results)
    return Aggregate(
        score=sum(case_scores) / len(case_scores) if case_scores else None,
        pass_rate=(sum(c.passed for c in judged_cases) / len(judged_cases))
        if judged_cases
        else None,
        error_rate=unscored / len(all_results) if all_results else 0.0,
        cases=len(cases),
        metrics=tuple(metric_aggs),
    )


def decide(agg: Aggregate, cases: Sequence[CaseResult], rule: PassRule, stage: Stage) -> Verdict:
    """Verdict precedence."""
    results = [r for c in cases for r in c.metrics]
    status: VerdictStatus
    reasons: list[str] = []
    failed_required = sorted({r.name for r in results if r.required and _is_failed(r)})
    unscored_required = sorted({r.name for r in results if r.required and _is_unscored(r)})
    exhausted = sorted(
        {
            r.error.code
            for r in results
            if r.error and r.error.code in ("deadline", "budget_exhausted")
        }
    )
    pending = sorted({r.name for r in results if r.status == "pending_human"})
    if failed_required:
        status, reasons = "fail", [f"required metric failed: {n}" for n in failed_required]
    elif unscored_required or agg.error_rate > rule.max_error_rate or exhausted:
        status = "inconclusive"
        reasons = [f"required metric not scored: {n}" for n in unscored_required]
        if agg.error_rate > rule.max_error_rate:
            reasons.append(f"error rate {agg.error_rate:.3f} > max {rule.max_error_rate:.3f}")
        reasons += [f"stopped early: {code}" for code in exhausted]
    elif pending:
        status, reasons = "pending_human", [f"awaiting human review: {n}" for n in pending]
    elif agg.score is None:
        status, reasons = "inconclusive", ["no metric produced a score"]
    elif abs(agg.score - rule.min_aggregate) < rule.inconclusive_band:
        status = "inconclusive"
        reasons = [
            f"aggregate {agg.score:.3f} within ±{rule.inconclusive_band} of {rule.min_aggregate}"
        ]
    elif agg.score >= rule.min_aggregate and (agg.pass_rate or 0.0) >= rule.min_pass_rate:
        status = "pass"
    else:
        status = "fail"
        if agg.score < rule.min_aggregate:
            reasons.append(f"aggregate {agg.score:.3f} < {rule.min_aggregate}")
        if (agg.pass_rate or 0.0) < rule.min_pass_rate:
            reasons.append(f"pass rate {agg.pass_rate or 0.0:.3f} < {rule.min_pass_rate}")
    action = STAGE_ACTIONS[stage][status]
    if stage == "pre_submit" and status in ("inconclusive", "pending_human"):
        action = {"retry": "retry_or_decline", "decline": "decline", "submit": "submit"}[
            rule.on_inconclusive
        ]
    return Verdict(status=status, action=action, reasons=tuple(reasons))
