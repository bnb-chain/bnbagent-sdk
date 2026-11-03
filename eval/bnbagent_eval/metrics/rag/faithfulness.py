"""``rag.faithfulness``: the answer is supported by the retrieved contexts.

Native method = AgentSight FactualCorrectnessEvaluator: the score is the judge's overall 5-tier
rating; per-claim verdicts (with their 5-tier scores) and ``avg_claim_score`` are details.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...judges.prompts import FIVE_TIER, PromptTemplate
from ...judges.prompts.rag import faithfulness as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register
from ._common import combine


@register
class Faithfulness(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="rag.faithfulness",
        category="rag",
        description="Every claim of the answer is supported by the retrieved contexts.",
        methods=(native_method("output", "contexts", optional=("input",)),),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=(
            "agentsight:FactualCorrectnessEvaluator",
            "ragas:faithfulness",
            "deepeval:faithfulness",
            "agentcore:Builtin.Faithfulness",
        ),
    )

    def score_parsed(self, parsed: Mapping[str, Any], template: PromptTemplate) -> float:
        return FIVE_TIER[parsed["overall_score"]]

    def claims(self, parsed: Mapping[str, Any], template: PromptTemplate) -> dict[str, Any]:
        verdicts = template.extra["claim_verdicts"]
        claims = [
            {"claim": c["claim"], "verdict": c["verdict"], "score": verdicts[c["verdict"]]}
            for c in parsed.get("atomic_claims_analysis", [])
        ]
        avg = sum(c["score"] for c in claims) / len(claims) if claims else 0.0
        return {"atomic_claims_analysis": claims, "avg_claim_score": avg}

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scored = [
            (self.score_parsed(s.parsed, template), self.claims(s.parsed, template))
            for s in samples
        ]
        return combine(
            samples,
            scored,
            provenance,
            reason_key="reasoning_summary",
            label_key="overall_score",
            mode=mode,
        )
