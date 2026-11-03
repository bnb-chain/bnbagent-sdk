"""``quality.coherence``: logical flow and internal consistency of one response (new prompt)."""

from __future__ import annotations

from ...judges.prompts.quality import coherence as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Coherence(QualityJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.coherence",
        category="quality",
        description="The response is logically connected, consistent and organized.",
        methods=(native_method("output", optional=("input",)),),
        params_schema=judge_params(),
        default_threshold=0.5,
        sources=("agentcore:Builtin.Coherence",),
    )
