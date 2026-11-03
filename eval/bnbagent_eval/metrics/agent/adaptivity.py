"""``agent.adaptivity``: does the agent adapt after tool failures (AgentSight
AgentAdaptivityEvaluator).

Per invocation: (adaptive + not-relevant steps) / tool calls (1 when there is no tool call);
samples merged by majority vote, invocations by their mean.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts.agent import adaptivity as prompts
from ...types.case import EvalCase
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._whitebox import Invocation, StepRatioJudge, params_str, result_text


def thought_process(inv: Invocation) -> str:
    seen: list[str] = []
    lines: list[str] = []
    for i, u in enumerate(inv.tool_uses):
        params = params_str(u.call)
        if u.thought and u.thought not in seen:
            lines.append(f' [{i}] **Agent\'s Thought:**: "{u.thought}"')
            seen.append(u.thought)
        lines.append(f' [{i}] Called "{u.call.name}({params})"')
        lines.append(f' [{i}] Result from {u.call.name}({params}): "{result_text(u.result)}"')
    return "\n".join(lines)


@register
class Adaptivity(StepRatioJudge):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.adaptivity",
        category="agent",
        description="Share of steps where the agent adapts after failures (or needs not adapt).",
        methods=(native_method("trajectory"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:AgentAdaptivityEvaluator",),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {"thought": thought_process(inv)}

    def score_sample(self, parsed: Mapping[str, Any], steps: int) -> float:
        if steps < 1:
            return 1.0
        adaptive = len(parsed.get("adaptive_steps") or [])
        not_relevant = len(parsed.get("not_relevant_steps") or [])
        return (adaptive + not_relevant) / steps
