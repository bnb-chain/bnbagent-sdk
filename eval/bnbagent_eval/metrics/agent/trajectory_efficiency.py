"""``agent.trajectory_efficiency``: was every piece of evidence necessary (AgentSight
TrajectoryEfficiencyEvaluator).

Per invocation: 1 - inefficient steps / tool calls (at least 1); samples merged by majority
vote, invocations by their mean.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts.agent import trajectory_efficiency as prompts
from ...types.case import EvalCase
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._whitebox import Invocation, StepRatioJudge, params_str, result_text


def evidence_with_indices(inv: Invocation) -> str:
    seen: list[str] = []
    lines: list[str] = []
    for i, u in enumerate(inv.tool_uses):
        params = params_str(u.call)
        if u.thought and u.thought not in seen:
            lines.append(f' [{i}] agent action: {u.thought}: "{u.call.name}({params})"')
            seen.append(u.thought)
        else:
            lines.append(f' [{i}] agent action: "{u.call.name}({params})"')
        lines.append(
            f' [{i}] observation: Result from {u.call.name}({params}): "{result_text(u.result)}"'
        )
    return "\n".join(lines)


@register
class TrajectoryEfficiency(StepRatioJudge):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.trajectory_efficiency",
        category="agent",
        description="Share of tool steps that were necessary (1 - inefficient / steps).",
        methods=(native_method("trajectory"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:TrajectoryEfficiencyEvaluator", "deepeval:step_efficiency"),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "query": inv.user_input,
            "files": "",
            "evidence_with_indices": evidence_with_indices(inv),
            "final_answer": inv.final_response,
        }

    def score_sample(self, parsed: Mapping[str, Any], steps: int) -> float:
        return 1 - len(parsed.get("inefficient_steps") or []) / max(steps, 1)
