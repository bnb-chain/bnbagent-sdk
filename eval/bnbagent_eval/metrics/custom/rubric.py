"""``custom.rubric``: weighted yes / no / not_applicable verdicts on a list of rubrics.

Native method = AgentSight RubricBasedEvaluator: per rubric the judge answers yes (1) / no (0) /
not_applicable (excluded). With ``samples`` > 1 each rubric is decided by majority vote
(MajorityVotePerInvocationResultsAggregator: yes only if yes votes outnumber no votes; no votes at
all = not applicable). Score = weighted share of "yes" among applicable rubrics (weights from
ETK, default 1); ``None`` when no rubric is applicable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...exceptions import SuiteError
from ...judges.prompts import PromptTemplate
from ...judges.prompts.custom import rubric as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register

YES, NO, NA = "yes", "no", "not_applicable"
_VERDICTS = {
    "yes": YES,
    "pass": YES,
    "true": YES,
    "no": NO,
    "fail": NO,
    "false": NO,
    "nr": NA,
    "n/r": NA,
    "na": NA,
    "n/a": NA,
    "not_applicable": NA,
    "not applicable": NA,
}


def normalize_verdict(verdict: Any) -> str:
    """AgentSight ``JsonAutoRaterResponseParser._normalize_verdict``; unknown = not applicable."""
    return _VERDICTS.get(str(verdict or "").strip().lower(), NA)


def rubric_list(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    rubrics = [
        {"id": str(r["id"]).strip(), "text": r["text"], "weight": float(r.get("weight", 1.0))}
        for r in params.get("rubrics") or ()
    ]
    if not rubrics:
        raise SuiteError("custom.rubric requires a non-empty rubrics param")
    ids = [r["id"] for r in rubrics]
    if len(set(ids)) != len(ids):
        raise SuiteError("custom.rubric: rubric ids must be unique")
    return rubrics


def sample_verdicts(parsed: Mapping[str, Any], rubrics: Sequence[Mapping]) -> dict[str, tuple]:
    """rubric id -> (verdict, rationale) for one reply; matched by id, else by property text."""
    by_text = {r["text"].strip().lower(): r["id"] for r in rubrics}
    ids = {r["id"] for r in rubrics}
    out: dict[str, tuple] = {}
    for e in parsed.get("evaluations", []):
        rid = str(e.get("rubric_id", "")).strip()
        if rid not in ids:
            rid = by_text.get(str(e.get("property", "")).strip().lower(), "")
        if rid and rid not in out:
            out[rid] = (normalize_verdict(e.get("verdict")), e.get("rationale"))
    return out


def majority(votes: Sequence[tuple[str, Any]]) -> tuple[str, Any]:
    """AgentSight majority vote: yes iff yes > no; no if any no and no >= yes; else N/A."""
    yes = [v for v in votes if v[0] == YES]
    no = [v for v in votes if v[0] == NO]
    if not yes and not no:
        return NA, next((v[1] for v in votes if v[1]), None)
    winner = yes if len(yes) > len(no) else no
    return winner[0]


def weighted_yes(rows: Sequence[Mapping[str, Any]]) -> float | None:
    applicable = [r for r in rows if r["verdict"] != NA]
    total = sum(r["weight"] for r in applicable)
    if not applicable or total == 0:
        return None
    return sum(r["weight"] for r in applicable if r["verdict"] == YES) / total


@register
class Rubric(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="custom.rubric",
        category="custom",
        description="Weighted share of rubrics the response satisfies (yes / no / N/A judge).",
        methods=(native_method("output", optional=("input", "expected", "conversation")),),
        params_schema={
            **judge_params(
                {
                    "rubrics": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id", "text"],
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "text": {"type": "string", "minLength": 1},
                                "weight": {"type": "number", "minimum": 0, "maximum": 1000},
                            },
                            "additionalProperties": False,
                        },
                    }
                }
            ),
            "required": ["rubrics"],
        },
        default_threshold=0.5,
        sources=("agentsight:RubricBasedEvaluator", "etk:agent_as_judge(weighted scores)"),
    )

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        return prompts.with_rubrics(super().template_for(params), rubric_list(params))

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        rubrics = rubric_list(ctx.params)
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        per_sample = [sample_verdicts(s.parsed, rubrics) for s in samples]
        rows = []
        for r in rubrics:
            votes = [v.get(r["id"], (NA, None)) for v in per_sample]
            verdict, rationale = majority(votes)
            rows.append(
                {
                    **r,
                    "verdict": verdict,
                    "rationale": rationale,
                    "votes": {k: sum(1 for v in votes if v[0] == k) for k in (YES, NO, NA)},
                }
            )
        score = weighted_yes(rows)
        details: dict[str, Any] = {
            "mode": mode,
            "rubrics": rows,
            "applicable": sum(1 for r in rows if r["verdict"] != NA),
        }
        if len(samples) > 1:
            details["sample_scores"] = [
                weighted_yes([{**r, "verdict": v.get(r["id"], (NA, None))[0]} for r in rubrics])
                for v in per_sample
            ]
        reason = "\n".join(
            f"[{r['id']}] {'PASS' if r['verdict'] == YES else 'FAIL'}: {r['rationale']}"
            for r in rows
            if r["rationale"] and r["verdict"] != NA
        )
        if score is None:
            reason = reason or "no rubric is applicable"
        return Outcome(score=score, reason=reason or None, details=details, provenance=provenance)
