"""``rag.groundedness``: the response is supported by the contexts (RAGAS groundedness)."""

from __future__ import annotations

from ...judges.prompts.rag import groundedness as prompts
from ..base import MetricSpec
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register


@register
class Groundedness(JudgeMetric):
    templates = prompts.VERSIONS
    include_input = False
    spec = MetricSpec(
        id="rag.groundedness",
        category="rag",
        description="The response is grounded in the retrieved contexts (5-tier judge).",
        methods=(native_method("output", "contexts"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=("ragas:response_groundedness",),
    )
