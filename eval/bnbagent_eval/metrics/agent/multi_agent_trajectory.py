"""``agent.multi_agent_trajectory``: rubric-based end-to-end multi-agent trajectory quality
(AgentSight FullTrajectoryQualityEvaluator, ``trajectory_accuracy`` e2e prompts).

Same scoring as ``agent.tool_use_quality`` (majority vote per rubric, mean of applicable
verdicts). The rendered record attributes every step to its agent and shows delegations.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...judges.prompts.agent import multi_agent_trajectory as prompts
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
    tools_by_agent,
)


def agent_names(inv: Invocation, case: EvalCase) -> str:
    names = list(dict.fromkeys(s.agent_id for s in inv.trace.steps if s.agent_id))
    names = names or list(tools_by_agent(inv, case))
    return "-".join(names) if names else "agent_unknown"


@register
class MultiAgentTrajectory(RubricWhitebox):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.multi_agent_trajectory",
        category="agent",
        description="Share of rubrics a multi-agent trajectory satisfies (coordination, handoff).",
        methods=(native_method("trajectory"),),
        params_schema={**rubric_params(), "required": ["rubrics"]},
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:FullTrajectoryQualityEvaluator", "agentsight:trajectory_accuracy"),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "tool_declarations": tool_declarations(inv, case, "No tools available."),
            "agent_name": agent_names(inv, case),
            "user_input": inv.user_input,
            "tool_usage": tool_usage(inv),
            "conversation_history": previous_turns(previous),
            "agent_response": inv.final_response,
        }
