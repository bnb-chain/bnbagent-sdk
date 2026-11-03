"""``quality.tone``: is the tone appropriate for the context (AgentSight ToneEvaluator).

There is no ``expected_tone`` parameter: the tone is judged against the context, and
``domain_rubric`` can state the desired tone.
"""

from __future__ import annotations

from ...judges.prompts.quality import tone as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Tone(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.tone",
        category="quality",
        description="The response tone is appropriate, polite and fits the context.",
        methods=(native_method("output", optional=("input", "expected", "conversation")),),
        params_schema=judge_params(),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:ToneEvaluator",),
    )
