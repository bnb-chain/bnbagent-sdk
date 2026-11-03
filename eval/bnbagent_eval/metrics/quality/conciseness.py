"""``quality.conciseness``: verbosity, redundancy and repetition (AgentSight ConcisenessEvaluator).

One judge call returns four ratings. The score is the weighted mean of the verbosity (0.4),
redundancy (0.3) and repetition (0.3) ratings, with weights normalized to sum to 1 as in the
source; the overall rating is reported as ``label`` and ``overall_score`` but not scored.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.quality import conciseness as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge

DIMENSIONS = ("verbosity", "redundancy", "repetition")
_WEIGHT = {"type": "number", "minimum": 0}


def weights(params: Mapping[str, Any]) -> dict[str, float]:
    raw = {d: float(params.get(f"{d}_weight", prompts.DEFAULT_WEIGHTS[d])) for d in DIMENSIONS}
    total = math.fsum(raw.values())
    if total <= 0:
        return dict(prompts.DEFAULT_WEIGHTS)
    return {d: w / total for d, w in raw.items()}


def sub_scores(parsed: Mapping[str, Any], template: PromptTemplate) -> dict[str, float]:
    labels = template.extra["sub_labels"]
    return {key: labels[key][parsed[key]] for key in labels}


@register
class Conciseness(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    label_field = "overall_score"
    include_input = False  # source: build_prompt(..., include_input=False)
    spec = MetricSpec(
        id="quality.conciseness",
        category="quality",
        description="The response has no filler, redundancy or repetition (weighted judge).",
        methods=(native_method("output", optional=("expected", "conversation")),),
        params_schema=judge_params({f"{d}_weight": _WEIGHT for d in DIMENSIONS}),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:ConcisenessEvaluator", "agentcore:Builtin.Conciseness"),
    )

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        w = weights(ctx.params)
        subs = [sub_scores(s.parsed, template) for s in samples]
        scores = [
            round(math.fsum(w[d] * sub[f"{d}_score"] for d in DIMENSIONS), 12) for sub in subs
        ]
        out = self.outcome(samples, scores, provenance, template, mode=mode, weights=w)
        chosen = subs[scores.index(out.score)]
        return Outcome(
            score=out.score,
            reason=out.reason,
            details={**out.details, **chosen},
            provenance=out.provenance,
        )
