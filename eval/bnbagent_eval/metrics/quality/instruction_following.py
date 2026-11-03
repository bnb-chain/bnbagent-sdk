"""``quality.instruction_following``: does the response follow its instructions (new prompt;
DeepEval prompt_alignment / AgentCore InstructionFollowing semantics).

The instructions come from the ``instructions`` param (a string or a list of strings); without
it, the judge extracts them from the input.
"""

from __future__ import annotations

from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.quality import instruction_following as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


def instruction_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


@register
class InstructionFollowing(QualityJudge):
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.instruction_following",
        category="quality",
        description="The response follows every explicit instruction it was given (5-tier judge).",
        methods=(native_method("input", "output"),),
        params_schema=judge_params(
            {
                "instructions": {
                    "anyOf": [
                        {"type": "string", "minLength": 1},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    ]
                }
            }
        ),
        default_threshold=0.5,
        sources=("deepeval:prompt_alignment", "agentcore:Builtin.InstructionFollowing"),
    )

    def has_input(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if name == "input" and instruction_list(ctx.params.get("instructions")):
            return True  # explicit instructions stand in for the input
        return super().has_input(name, case, ctx)

    def select_mode(self, case: EvalCase, ctx: MetricContext, template: PromptTemplate) -> str:
        mode = super().select_mode(case, ctx, template)
        if mode == "simple" and instruction_list(ctx.params.get("instructions")):
            return "with_input_and_instructions" if case.has("input") else "with_instructions"
        return mode

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = super().fields(case, ctx, mode)
        items = instruction_list(ctx.params.get("instructions"))
        out["instructions"] = "\n".join(f"{i}. {s}" for i, s in enumerate(items, 1))
        return out
