"""``quality.clarity``: is the response clear and well structured (AgentSight ClarityEvaluator)."""

from __future__ import annotations

from ...judges.prompts.quality import clarity as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Clarity(QualityJudge):
    templates = prompts.VERSIONS
    output_or_conversation = True
    spec = MetricSpec(
        id="quality.clarity",
        category="quality",
        description="The response is clear, well structured and unambiguous (5-tier judge).",
        methods=(native_method("output", optional=("input", "expected", "conversation")),),
        params_schema=judge_params(),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:ClarityEvaluator",),
    )
