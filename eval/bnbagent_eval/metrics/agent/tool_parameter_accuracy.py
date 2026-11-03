"""``agent.tool_parameter_accuracy``: are tool-call parameters faithful to the preceding context
(AgentSight ToolParameterAccuracyEvaluator).

Per invocation the judge answers All (1) / Partial (0.5) / None (0); samples are merged by
majority vote (most common score) and invocations by their mean.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent import tool_parameter_accuracy as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec
from ..judge_base import Sample, judge_params, native_method
from ..registry import register
from ._whitebox import Invocation, WhiteboxJudge, majority_score, tool_declarations, tool_usage


@register
class ToolParameterAccuracy(WhiteboxJudge):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.tool_parameter_accuracy",
        category="agent",
        description="Tool-call parameters come from the user or prior tool results (judge).",
        methods=(native_method("trajectory"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=(
            "agentsight:ToolParameterAccuracyEvaluator",
            "deepeval:argument_correctness",
            "agentcore:Builtin.ToolParameterAccuracy",
        ),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "tool_declarations": tool_declarations(inv, case, "Agent has no tools."),
            "user_input": inv.user_input,
            "tool_usage": tool_usage(inv),
        }

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        scores = [prompts.JUDGMENTS[str(s.parsed["judgment"]).strip().lower()] for s in samples]
        first = samples[0].parsed
        row: dict[str, Any] = {
            "score": majority_score(scores),
            "reason": first.get("analysis"),
            "judgment": first["judgment"],
            "judgment_explanation": first.get("judgment_explanation"),
        }
        if len(samples) > 1:
            row["sample_scores"] = scores
        return row
