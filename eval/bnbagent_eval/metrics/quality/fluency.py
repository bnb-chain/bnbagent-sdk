"""``quality.fluency``: grammar, readability and naturalness (AgentSight FluencyEvaluator).

The source's language-tool ``rule`` method (``prioritize_efficiency=True``) is not ported.
"""

from __future__ import annotations

from ...judges.prompts.quality import fluency as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Fluency(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    include_input = False  # source: build_prompt(..., include_input=False)
    spec = MetricSpec(
        id="quality.fluency",
        category="quality",
        description="The response is grammatical, readable and natural (5-tier judge).",
        methods=(native_method("output", optional=("expected", "conversation")),),
        params_schema=judge_params(),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:FluencyEvaluator",),
    )
