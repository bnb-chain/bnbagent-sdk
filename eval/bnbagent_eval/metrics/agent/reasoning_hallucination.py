"""``agent.reasoning_hallucination``: are the agent's thoughts grounded in the evidence
(AgentSight AgentHallucinationEvaluator).

Per invocation: 1 - hallucinated steps / tool calls (at least 1); samples merged by majority
vote, invocations by their mean. Higher is better (no hallucination = 1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.prompts.agent import reasoning_hallucination as prompts
from ...types.case import EvalCase
from ..base import MetricSpec
from ..judge_base import judge_params, native_method
from ..registry import register
from ._whitebox import Invocation, StepRatioJudge, params_str, result_text


def evidence_with_thoughts(inv: Invocation) -> str:
    seen: list[str] = []
    lines: list[str] = []
    for i, u in enumerate(inv.tool_uses):
        params = params_str(u.call)
        # As in the source, the first step's thought is not shown (it has no evidence yet).
        if i > 0 and u.thought and u.thought not in seen:
            lines.append(f' [{i}] **Agent\'s Thought:**: "{u.thought}"')
            seen.append(u.thought)
        lines.append(f' [{i}] Called "{u.call.name}({params})"')
        lines.append(f' [{i}] Result from {u.call.name}({params}): "{result_text(u.result)}"')
    return "\n".join(lines)


@register
class ReasoningHallucination(StepRatioJudge):
    templates = prompts.VERSIONS
    default_version = prompts.DEFAULT_VERSION
    spec = MetricSpec(
        id="agent.reasoning_hallucination",
        category="agent",
        description="Share of agent thoughts grounded in the evidence (1 - hallucinated / steps).",
        methods=(native_method("trajectory"),),
        params_schema=judge_params(),
        default_threshold=0.5,
        # v2: a call is counted once when only one side has an id; case input as user input
        version="2",
        sources=("agentsight:AgentHallucinationEvaluator",),
    )

    def invocation_fields(
        self, inv: Invocation, previous: Sequence[Invocation], case: EvalCase
    ) -> dict[str, str]:
        return {
            "query": inv.user_input,
            "files": "",
            "evidence_with_thoughts": evidence_with_thoughts(inv),
        }

    def score_sample(self, parsed: Mapping[str, Any], steps: int) -> float:
        return 1 - len(parsed.get("hallucination_steps") or []) / max(steps, 1)
