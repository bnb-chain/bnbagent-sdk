"""``rag.context_relevance``: retrieved contexts are relevant to the query.

Native method = AgentSight ContextQualityEvaluator: score = ``overall_relevance_score`` (0-5,
``raw_range``) / 5; per-context verdicts are details. ``rag.context_precision`` shares the prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.rag import context_quality as prompts
from ...types.case import EvalCase
from ..base import Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, Sample, judge_params
from ..registry import register
from ._common import agentsight_contexts, combine


class ContextQualityMetric(JudgeMetric):
    """One AgentSight ContextQuality judge call; subclasses derive their score from it."""

    templates = prompts.VERSIONS
    label_key = "retrieval_quality_label"

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        return {**super().fields(case, ctx, mode), "contexts": agentsight_contexts(case)}

    @staticmethod
    def context_analysis(parsed: Mapping[str, Any], template: PromptTemplate) -> list[dict]:
        verdicts = template.extra["relevance_verdicts"]
        return [
            {
                "context_id": c["context_id"],
                "verdict": c["verdict"],
                "score": verdicts[c["verdict"]],
                "reasoning": c.get("reasoning", ""),
            }
            for c in parsed.get("context_analysis", [])
        ]

    def score_sample(
        self, case: EvalCase, ctx: MetricContext, sample: Sample, template: PromptTemplate
    ) -> tuple[float, dict[str, Any]]:
        raise NotImplementedError

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scored = [self.score_sample(case, ctx, s, template) for s in samples]
        return combine(
            samples,
            scored,
            provenance,
            reason_key="score_reasoning",
            label_key=self.label_key,
            num_contexts=len(case.contexts),
        )


@register
class ContextRelevance(ContextQualityMetric):
    spec = MetricSpec(
        id="rag.context_relevance",
        category="rag",
        description="Retrieved contexts are relevant to the query (0-5 judge rating / 5).",
        methods=(
            MethodSpec(
                "native",
                Kind.JUDGE,
                required_inputs=frozenset({"input", "contexts"}),
                raw_range=(0.0, 5.0),
            ),
        ),
        params_schema=judge_params(),
        default_threshold=0.6,
        sources=(
            "agentsight:ContextQualityEvaluator",
            "ragas:context_relevancy",
            "deepeval:contextual_relevancy",
        ),
    )

    def score_sample(
        self, case: EvalCase, ctx: MetricContext, sample: Sample, template: PromptTemplate
    ) -> tuple[float, dict[str, Any]]:
        raw = float(sample.parsed["overall_relevance_score"])
        return raw, {"context_analysis": self.context_analysis(sample.parsed, template)}
