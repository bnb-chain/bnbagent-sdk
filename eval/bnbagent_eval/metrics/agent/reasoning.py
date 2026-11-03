"""``agent.reasoning``: reasoning quality on seven rubrics (AgentSight AgentReasoningEvaluator).

Built-in rubrics: logical_progression, evidence_based, uncertainty_handling, tool_selection,
coherent_explanation, problem_decomposition, assumption_clarity (``rubrics`` overrides them).
Scoring as in :class:`._whitebox.RubricWhitebox`. The agent's thoughts are the texts of the LLM
steps that issued each tool call.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...judges.prompts.agent import reasoning as prompts
from ...types.case import EvalCase
from ..base import MetricSpec
from ..judge_base import native_method
from ..registry import register
from ._whitebox import (
    Invocation,
    RubricWhitebox,
    previous_turns,
    rubric_params,
    tool_declarations,
    tool_usage,
)

NO_THOUGHT = "No failure so far. I will carry on with the task"


def thought_process(inv: Invocation) -> str:
    return "\n".join(
        f' [{i}] **Agent\'s Thought:**: "{u.thought or NO_THOUGHT}"'
        for i, u in enumerate(inv.tool_uses)
    )


@register
class Reasoning(RubricWhitebox):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    default_rubrics = prompts.DEFAULT_RUBRICS
    spec = MetricSpec(
        id="agent.reasoning",
        category="agent",
        description="Share of seven reasoning-quality rubrics the agent's trajectory satisfies.",
        methods=(native_method("trajectory"),),
        params_schema=rubric_params(),
        default_threshold=0.7,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:AgentReasoningEvaluator",),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "tool_declarations": tool_declarations(inv, case, "Agent has no tools."),
            "user_input": inv.user_input,
            "conversation_history": previous_turns(previous),
            "thought": thought_process(inv),
            "tool_usage": tool_usage(inv),
        }
