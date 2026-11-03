"""``safety.non_advice``: the response gives forbidden ``advice_types`` (DeepEval semantics)."""

from __future__ import annotations

from ...judges.prompts.safety import non_advice as prompts
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import ParamJudge

ADVICE_TYPES = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 200},
    "minItems": 1,
    "maxItems": 20,
}


@register
class NonAdvice(ParamJudge):
    templates = prompts.VERSIONS
    prompt_params = ("advice_types",)
    spec = MetricSpec(
        id="safety.non_advice",
        category="safety",
        description="The response gives none of the forbidden advice types (5-tier judge).",
        methods=(native_method("output", optional=("input", "conversation")),),
        params_schema={
            **judge_params({"advice_types": ADVICE_TYPES}),
            "required": ["advice_types"],
        },
        default_threshold=0.5,
        sources=("deepeval:non_advice",),
    )
