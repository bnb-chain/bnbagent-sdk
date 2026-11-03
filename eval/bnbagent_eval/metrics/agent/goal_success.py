"""``agent.goal_success``: were all user goals achieved (AgentSight GoalSuccessRateEvaluator).

Session-level: one judge call over the whole rendered trajectory. The judge lists the user's
goals with a 0-1 success score each; the score is their mean (0 when no goal is listed). An
optional ``expected`` outcome is appended to the prompt (``with_expected`` mode).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...judges.prompts import PromptTemplate
from ...judges.prompts.agent import goal_success as prompts
from ..base import MetricSpec
from ..judge_base import AGENT_ID_PARAM, JudgeMetric, judge_params, native_method
from ..registry import register


@register
class GoalSuccess(JudgeMetric):
    templates = prompts.VERSIONS
    default_version = "v0"
    spec = MetricSpec(
        id="agent.goal_success",
        category="agent",
        description="Mean success score of the user goals identified in the trajectory.",
        methods=(native_method("trajectory", optional=("expected",)),),
        params_schema=judge_params(AGENT_ID_PARAM),
        default_threshold=0.7,
        sources=(
            "agentsight:GoalSuccessRateEvaluator",
            "ragas:agent_goal_accuracy",
            "ragas:agent_goal_accuracy_with_reference",
            "deepeval:goal_accuracy",
            "agentcore:Builtin.GoalSuccessRate",
        ),
    )

    def score_parsed(self, parsed: Mapping[str, Any], template: PromptTemplate) -> float:
        goals = parsed.get("goals") or []
        if not goals:
            return 0.0
        return sum(float(g["goal_success_score"]) for g in goals) / len(goals)
