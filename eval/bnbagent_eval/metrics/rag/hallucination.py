"""``rag.hallucination``: share of contexts the response contradicts (DeepEval hallucination).

The judge gives one yes (agrees) / no (contradicts) verdict per context; the raw score is the
share of "no" verdicts (higher is worse). The method is ``inverted``, so the result score is
``1 - raw`` and ``details.raw`` keeps the hallucination rate.
"""

from __future__ import annotations

from ...judges.prompts.rag import hallucination as prompts
from ...types.case import EvalCase
from ..base import Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params
from ..registry import register
from ._common import combine


@register
class Hallucination(JudgeMetric):
    templates = prompts.VERSIONS
    include_input = False
    spec = MetricSpec(
        id="rag.hallucination",
        category="rag",
        description="The response does not contradict the provided contexts (inverted rate).",
        methods=(
            MethodSpec(
                "native",
                Kind.JUDGE,
                required_inputs=frozenset({"output", "contexts"}),
                inverted=True,
            ),
        ),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=("deepeval:hallucination",),
    )

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        samples, provenance = await self.ask(
            case, ctx, template, mode, self.fields(case, ctx, mode)
        )
        scored = []
        for s in samples:
            verdicts = s.parsed["verdicts"]
            contradicted = sum(1 for v in verdicts if v["verdict"].strip().lower() == "no")
            scored.append(
                (
                    contradicted / len(verdicts),
                    {"hallucinated_contexts": contradicted, "num_verdicts": len(verdicts)},
                )
            )
        return combine(samples, scored, provenance, higher_is_worse=True, mode=mode)
