"""``agent.task_completion``: did the agent's trajectory accomplish the task in the input."""

from __future__ import annotations

from ...judges.prompts.agent import task_completion as prompts
from ..base import MetricSpec
from ..judge_base import native_method
from ..registry import register
from ._section_judge import AGENT_JUDGE_PARAMS, SectionJudge


@register
class TaskCompletion(SectionJudge):
    templates = prompts.VERSIONS
    optional_sections = ("output",)
    spec = MetricSpec(
        id="agent.task_completion",
        category="agent",
        description="The agent's execution accomplished the task in the input (5-tier judge).",
        methods=(native_method("input", "trajectory", optional=("output",)),),
        params_schema=AGENT_JUDGE_PARAMS,
        default_threshold=0.5,
        sources=("deepeval:task_completion",),
    )
