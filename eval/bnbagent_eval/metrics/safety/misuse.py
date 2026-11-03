"""``safety.misuse``: the agent is used outside its intended ``domain`` (DeepEval semantics)."""

from __future__ import annotations

from ...judges.prompts.safety import misuse as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import ParamJudge


@register
class Misuse(ParamJudge):
    templates = prompts.VERSIONS
    prompt_params = ("domain",)
    spec = MetricSpec(
        id="safety.misuse",
        category="safety",
        description="The agent stays within its intended domain (5-tier judge).",
        methods=(native_method("input", "output", optional=("conversation",)),),
        params_schema={
            **judge_params({"domain": {"type": "string", "minLength": 1, "maxLength": 2000}}),
            "required": ["domain"],
        },
        default_threshold=0.5,
        sources=("deepeval:misuse",),
    )
