"""``quality.relevance``: does the response address the query (AgentSight RelevanceEvaluator)."""

from __future__ import annotations

from ...judges.prompts.quality import relevance as prompts
from ..base import MetricSpec
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register


@register
class Relevance(JudgeMetric):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.relevance",
        category="quality",
        description="The response directly addresses the query and its intent (5-tier judge).",
        methods=(native_method("output", optional=("input", "expected", "conversation")),),
        params_schema=judge_params(),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=(
            "agentsight:RelevanceEvaluator",
            "ragas:answer_relevancy",
            "deepeval:answer_relevancy",
            "agentcore:Builtin.ResponseRelevance",
        ),
    )
