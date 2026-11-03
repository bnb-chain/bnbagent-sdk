"""Comparing and combining evaluations from several evaluators.

- ``compare(a, b)``: are two evaluations of the same subject under the same suite in agreement?
  Rule metrics must match exactly (they replay deterministically); judge metrics within a
  tolerance (default 0.15, overridable per metric name).
- ``aggregate_evaluations([...])``: combine several evaluators: ``median`` of aggregate scores,
  or ``quorum:k`` (the verdict needs at least k evaluators to agree on it).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..types.request import EvalResponse
from ..types.result import MetricResult, VerdictStatus

DEFAULT_TOLERANCES = {"rule": 0.0, "judge": 0.15}


@dataclass(frozen=True)
class MetricAgreement:
    case_id: str
    name: str
    a: float | None
    b: float | None
    tolerance: float
    agree: bool


@dataclass(frozen=True)
class Comparison:
    comparable: bool
    reasons: tuple[str, ...] = ()
    verdict_agree: bool = False
    metrics: tuple[MetricAgreement, ...] = ()

    @property
    def agreement_rate(self) -> float | None:
        if not self.metrics:
            return None
        return sum(m.agree for m in self.metrics) / len(self.metrics)

    @property
    def agree(self) -> bool:
        return self.comparable and self.verdict_agree and all(m.agree for m in self.metrics)


def _kind(result: MetricResult) -> str:
    prov = result.provenance
    return "judge" if prov is not None and prov.prompt_hash else "rule"


def compare(
    a: EvalResponse, b: EvalResponse, tolerances: Mapping[str, float] | None = None
) -> Comparison:
    tol = {**DEFAULT_TOLERANCES, **dict(tolerances or {})}
    reasons = []
    if a.suite_hash != b.suite_hash:
        reasons.append("different suite_hash")
    if a.subject != b.subject:
        reasons.append("different subject")
    if reasons:
        return Comparison(comparable=False, reasons=tuple(reasons))
    b_index = {(c.case_id, m.name): m for c in b.results for m in c.metrics}
    rows, missing = [], []
    for case in a.results:
        for m in case.metrics:
            other = b_index.get((case.case_id, m.name))
            if other is None:
                missing.append(f"{case.case_id}/{m.name}")
                continue
            t = tol.get(m.name, tol[_kind(m)])
            if m.score is None or other.score is None:
                agree = m.score is None and other.score is None and m.status == other.status
            else:
                agree = abs(m.score - other.score) <= t + 1e-9
            rows.append(MetricAgreement(case.case_id, m.name, m.score, other.score, t, agree))
    if missing:
        reasons.append(f"results missing in b: {', '.join(missing[:5])}")
    return Comparison(
        comparable=not missing,
        reasons=tuple(reasons),
        verdict_agree=a.verdict.status == b.verdict.status,
        metrics=tuple(rows),
    )


@dataclass(frozen=True)
class CombinedEvaluation:
    rule: str
    evaluators: int
    score: float | None
    status: VerdictStatus
    votes: Mapping[str, int] = field(default_factory=dict)
    disagreements: tuple[str, ...] = ()


def aggregate_evaluations(
    responses: Sequence[EvalResponse], rule: str = "median"
) -> CombinedEvaluation:
    """Combine evaluations of the same subject under the same suite."""
    if not responses:
        raise ValueError("no evaluations to combine")
    first = responses[0]
    for r in responses[1:]:
        if r.suite_hash != first.suite_hash or r.subject != first.subject:
            raise ValueError("evaluations must share suite_hash and subject")
    scores = [r.aggregate.score for r in responses if r.aggregate.score is not None]
    score = statistics.median(scores) if scores else None
    votes: dict[str, int] = {}
    for r in responses:
        votes[r.verdict.status] = votes.get(r.verdict.status, 0) + 1
    if rule == "median":
        ordered = sorted(
            responses, key=lambda r: (r.aggregate.score is None, r.aggregate.score or 0.0)
        )
        status = ordered[(len(ordered) - 1) // 2].verdict.status  # lower median evaluator
    elif rule.startswith("quorum:"):
        k = int(rule.split(":", 1)[1])
        if not 1 <= k <= len(responses):
            raise ValueError(f"quorum k must be within 1..{len(responses)}")
        winners = [s for s, n in votes.items() if n >= k]
        status = winners[0] if len(winners) == 1 else "inconclusive"
    else:
        raise ValueError(f"unknown rule {rule!r} (use 'median' or 'quorum:<k>')")
    pairs = [compare(first, r) for r in responses[1:]]
    disagreements = tuple(f"{m.case_id}/{m.name}" for c in pairs for m in c.metrics if not m.agree)
    return CombinedEvaluation(
        rule=rule,
        evaluators=len(responses),
        score=score,
        status=status,
        votes=votes,
        disagreements=disagreements,
    )
