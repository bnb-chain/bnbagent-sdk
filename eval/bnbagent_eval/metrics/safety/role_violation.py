"""``safety.role_violation``: the response breaks its assigned ``role`` (DeepEval semantics)."""

from __future__ import annotations

from ...judges.prompts.safety import role_violation as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import ParamJudge


@register
class RoleViolation(ParamJudge):
    templates = prompts.VERSIONS
    prompt_params = ("role",)
    spec = MetricSpec(
        id="safety.role_violation",
        category="safety",
        description="The response stays within the agent's assigned role (5-tier judge).",
        methods=(native_method("output", optional=("input", "conversation")),),
        params_schema={
            **judge_params({"role": {"type": "string", "minLength": 1, "maxLength": 2000}}),
            "required": ["role"],
        },
        default_threshold=0.5,
        sources=("deepeval:role_violation",),
    )
