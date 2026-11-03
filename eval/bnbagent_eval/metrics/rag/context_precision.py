"""``rag.context_precision``: share of retrieved contexts that are relevant to the query.

Native method = AgentSight ContextQualityEvaluator precision: contexts whose verdict score is
>= ``relevance_threshold`` (default 0.5, as in the source) over the number of contexts. The
judge's own estimate is kept as ``context_precision_llm``.
"""

from __future__ import annotations

from typing import Any

from ...judges.prompts import PromptTemplate
from ...types.case import EvalCase
from ..base import Kind, MethodSpec, MetricContext, MetricSpec
from ..judge_base import Sample, judge_params
from ..registry import register
from .context_relevance import ContextQualityMetric

DEFAULT_RELEVANCE_THRESHOLD = 0.5


@register
class ContextPrecision(ContextQualityMetric):
    spec = MetricSpec(
        id="rag.context_precision",
        category="rag",
        description="Share of retrieved contexts judged relevant to the query.",
        methods=(
            MethodSpec("native", Kind.JUDGE, required_inputs=frozenset({"input", "contexts"})),
        ),
        params_schema=judge_params(
            {"relevance_threshold": {"type": "number", "minimum": 0, "maximum": 1}}
        ),
        default_threshold=0.6,
        sources=(
            "agentsight:ContextQualityEvaluator",
            "ragas:LLMContextPrecisionWithoutReference",
            "deepeval:contextual_precision",
        ),
    )

    def score_sample(
        self, case: EvalCase, ctx: MetricContext, sample: Sample, template: PromptTemplate
    ) -> tuple[float, dict[str, Any]]:
        threshold = float(ctx.params.get("relevance_threshold", DEFAULT_RELEVANCE_THRESHOLD))
        analysis = self.context_analysis(sample.parsed, template)
        relevant = sum(1 for c in analysis if c["score"] >= threshold)
        n = len(case.contexts)
        return relevant / n if n else 0.0, {
            "context_analysis": analysis,
            "relevant_context_count": relevant,
            "relevance_threshold": threshold,
            "context_precision_llm": sample.parsed.get("context_precision"),
        }
