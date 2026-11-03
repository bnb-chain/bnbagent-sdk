"""``rag.context_recall``: the contexts contain what the expected answer needs (RAGAS/DeepEval).

Score = share of expected-answer statements the judge attributes to the retrieved contexts.
"""

from __future__ import annotations

from ...judges.prompts.rag import context_recall as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register
from ._common import combine, share


@register
class ContextRecall(JudgeMetric):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="rag.context_recall",
        category="rag",
        description="The retrieved contexts contain the information of the expected answer.",
        methods=(native_method("input", "contexts", "expected"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=("ragas:context_recall", "deepeval:contextual_recall"),
    )

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scored = []
        for s in samples:
            flags = [st["attributed"].strip().lower() == "yes" for st in s.parsed["statements"]]
            scored.append(
                (share(flags), {"attributed_count": sum(flags), "num_statements": len(flags)})
            )
        return combine(samples, scored, provenance, mode=mode)
