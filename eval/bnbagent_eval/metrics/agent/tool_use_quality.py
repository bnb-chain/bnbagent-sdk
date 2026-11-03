"""``agent.tool_use_quality``: rubric-based tool-use quality (AgentSight ToolUseQualityEvaluator).

Per invocation the judge gives yes / no / nr per rubric (``rubrics`` param); samples are merged
by majority vote per rubric and the score is the mean of every applicable verdict (see
:class:`._whitebox.RubricWhitebox`). ``prompt_version`` selects the AgentSight
``trajectory_accuracy`` agent prompt: v2 (default) and v1 are rubric prompts; v0 is the original
correct / incorrect prompt (no rubrics; majority vote over samples, mean over invocations).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent import tool_use_quality as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec
from ..judge_base import Sample, native_method
from ..registry import register
from ._whitebox import (
    Invocation,
    RubricWhitebox,
    WhiteboxJudge,
    majority_score,
    previous_turns,
    rubric_params,
    tool_declarations,
    tool_usage,
)


@register
class ToolUseQuality(RubricWhitebox):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.tool_use_quality",
        category="agent",
        description="Share of tool-use rubrics the trajectory satisfies (yes / no / nr judge).",
        methods=(native_method("trajectory"),),
        params_schema={
            **rubric_params(),
            # v1 / v2 (the default) judge rubrics; only v0 (correct / incorrect) runs without
            "anyOf": [
                {"required": ["rubrics"]},
                {
                    "required": ["prompt_version"],
                    "properties": {"prompt_version": {"enum": ["v0"]}},
                },
            ],
            "errorMessage": "rubrics are required (unless prompt_version is v0)",
        },
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input;
        # rubrics required for v1 / v2
        version="2",
        sources=("agentsight:ToolUseQualityEvaluator", "agentsight:trajectory_accuracy"),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "tool_declarations": tool_declarations(inv, case, "Agent has no tools."),
            "user_input": inv.user_input,
            "conversation_history": previous_turns(previous),
            "tool_usage": tool_usage(inv),
        }

    def score_invocation(
        self, inv: Invocation, samples: list[Sample], template: PromptTemplate, ctx: MetricContext
    ) -> dict[str, Any]:
        if self.uses_rubrics(template):
            return super().score_invocation(inv, samples, template, ctx)
        scores = [template.labels[s.parsed["verdict"]] for s in samples]
        score = majority_score(scores)
        row: dict[str, Any] = {
            "score": score,
            "reason": None,
            "verdict": "correct" if score == 1.0 else "incorrect",
        }
        if len(samples) > 1:
            row["sample_scores"] = scores
        return row

    def combine(self, rows: list[dict[str, Any]], template: PromptTemplate) -> float | None:
        if self.uses_rubrics(template):
            return super().combine(rows, template)
        return WhiteboxJudge.combine(self, rows, template)
