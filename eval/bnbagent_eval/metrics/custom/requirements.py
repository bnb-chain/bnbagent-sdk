"""``custom.requirements_compliance``: weighted per-requirement compliance scores.

Native method = ETK agent_as_judge ``requirements_compliance`` in one judge call: requirements
come from the ``requirements`` param or are extracted from the input by the judge; each gets a
0-1 score and an optional weight (0-1000); the score is the weighted mean rounded to 3 decimals
(ETK ``AgentVerdict._derive_score``). A listed requirement the judge skips scores 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.custom import requirements as prompts
from ...judges.sampling import median_low
from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register


def requirement_list(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize the ``requirements`` param to ``{id, text, weight}`` (ids default to REQ-n)."""
    out = []
    for i, r in enumerate(params.get("requirements") or (), 1):
        if isinstance(r, str):
            r = {"text": r}
        out.append(
            {
                "id": str(r.get("id") or f"REQ-{i}"),
                "text": r["text"],
                "weight": None if r.get("weight") is None else float(r["weight"]),
            }
        )
    return out


def weighted_mean(rows: Sequence[Mapping[str, Any]]) -> float:
    total = sum(r["weight"] for r in rows)
    if total == 0:
        raise ValueError("cannot derive overall score: the weights of all scores sum to zero")
    return round(sum(r["weight"] * r["score"] for r in rows) / total, 3)


def score_reply(
    parsed: Mapping[str, Any], given: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Per-requirement rows of one reply (given requirements are matched by id)."""
    entries = parsed["requirements"]

    def row(e: Mapping[str, Any] | None, req: Mapping[str, Any] | None) -> dict[str, Any]:
        weight = req["weight"] if req and req["weight"] is not None else None
        if weight is None:
            weight = float(e.get("weight", 1.0)) if e else 1.0
        return {
            "id": req["id"] if req else str(e.get("id", "")),
            "description": req["text"] if req else e.get("description", ""),
            "score": float(e["score"]) if e else 0.0,
            "weight": weight,
            "rationale": e.get("rationale") if e else None,
            "assessed": e is not None,
        }

    if not given:
        return [row(e, None) for e in entries]
    by_id = {str(e.get("id", "")).strip().upper(): e for e in entries}
    return [row(by_id.get(r["id"].strip().upper()), r) for r in given]


@register
class RequirementsCompliance(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="custom.requirements_compliance",
        category="custom",
        description="Weighted mean of per-requirement compliance scores (0-1 judge).",
        methods=(native_method("output", optional=("input", "expected")),),
        params_schema=judge_params(
            {
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "anyOf": [
                            {"type": "string", "minLength": 1},
                            {
                                "type": "object",
                                "required": ["text"],
                                "properties": {
                                    "id": {"type": "string", "minLength": 1},
                                    "text": {"type": "string", "minLength": 1},
                                    "weight": {"type": "number", "minimum": 0, "maximum": 1000},
                                },
                                "additionalProperties": False,
                            },
                        ]
                    },
                }
            }
        ),
        default_threshold=0.5,
        sources=("etk:agent_as_judge.requirements_compliance",),
    )

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        base = super().template_for(params)
        given = requirement_list(params)
        return prompts.with_requirements(base, given) if given else base

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        given = requirement_list(ctx.params)
        if not given and not case.has("input"):
            return Outcome(
                None,
                status="skipped",
                error=ErrorInfo(
                    "missing_input", "requirements param or input (to extract them) is required"
                ),
            )
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        rows = [score_reply(s.parsed, given) for s in samples]
        scores = [weighted_mean(r) for r in rows]
        score = median_low(scores)
        i = scores.index(score)
        details: dict[str, Any] = {
            "mode": mode,
            "requirements_source": "param" if given else "extracted",
            "requirements": rows[i],
            "issues": list(samples[i].parsed.get("issues", [])),
        }
        if len(samples) > 1:
            details["sample_scores"] = scores
        return Outcome(
            score=score,
            reason=samples[i].parsed.get("summary"),
            details=details,
            provenance=provenance,
        )
