"""``quality.completeness``: does the response address every aspect of the question
(AgentSight CompletenessEvaluator).

One judge call rates question completeness. When the case has retrieval contexts, a second call
(context-aware system prompt) rates how completely the response uses them, and the score is the
mean of the two.
"""

from __future__ import annotations

from ...judges.prompts.quality import completeness as prompts
from ...judges.sampling import median_low
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge, merge_provenance


def score_to_label(score: float) -> str:
    """Label for an averaged score (``CompletenessEvaluator._score_to_label``)."""
    for floor, label in (
        (0.9, "Excellent"),
        (0.65, "Good"),
        (0.4, "Average"),
        (0.15, "Below Average"),
    ):
        if score >= floor:
            return label
    return "Poor"


@register
class Completeness(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.completeness",
        category="quality",
        description="The response addresses all aspects of the question (and uses the contexts).",
        methods=(
            native_method("output", optional=("input", "expected", "conversation", "contexts")),
        ),
        params_schema=judge_params(),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:CompletenessEvaluator",),
    )

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        fields = self.fields(case, ctx, mode)
        q_samples, q_prov = await self.ask(case, ctx, template, mode, fields, tag="question")
        q_scores = [self.score_parsed(s.parsed, template) for s in q_samples]
        if not case.contexts:
            q = median_low(q_scores)
            return self.outcome(
                q_samples,
                q_scores,
                q_prov,
                template,
                mode=mode,
                question_completeness=q,
                has_context=False,
            )
        c_template = prompts.CONTEXT_VERSIONS[template.version]
        c_samples, c_prov = await self.ask(case, ctx, c_template, "context", fields, tag="context")
        c_scores = [self.score_parsed(s.parsed, c_template) for s in c_samples]
        q, c = median_low(q_scores), median_low(c_scores)
        q_reply = q_samples[q_scores.index(q)].parsed
        c_reply = c_samples[c_scores.index(c)].parsed
        score = (q + c) / 2
        details = {
            "mode": mode,
            "label": score_to_label(score),
            "question_completeness": q,
            "context_completeness": c,
            "missing_elements": list(q_reply.get("missing_elements") or [])
            + [f"[context] {e}" for e in c_reply.get("missing_elements") or []],
            "has_context": True,
        }
        if len(q_samples) > 1:
            details["sample_scores"] = {"question": q_scores, "context": c_scores}
        reason = (
            f"Question completeness: {q_reply.get('reasoning')} | "
            f"Context completeness: {c_reply.get('reasoning')}"
        )
        return Outcome(
            score=score,
            reason=reason,
            details=details,
            provenance=merge_provenance(q_prov, c_prov),
        )
