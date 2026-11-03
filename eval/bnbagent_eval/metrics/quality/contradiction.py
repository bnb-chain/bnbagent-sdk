"""``quality.contradiction``: absence of self-contradictions (AgentSight ContradictionEvaluator).

The judge rates a contradiction ``level``; score 1.0 = no contradictions, 0.0 = major ones.
"""

from __future__ import annotations

from ...judges.prompts.quality import contradiction as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Contradiction(QualityJudge):
    templates = prompts.VERSIONS
    label_field = "level"
    include_input = False  # source: build_prompt(..., include_input=False)
    output_or_conversation = True
    spec = MetricSpec(
        id="quality.contradiction",
        category="quality",
        description="The response (or conversation) does not contradict itself (1 = none).",
        methods=(native_method("output", optional=("expected", "conversation")),),
        params_schema=judge_params(),
        default_threshold=0.7,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:ContradictionEvaluator",),
    )
