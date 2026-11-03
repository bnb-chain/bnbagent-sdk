"""``agent.plan_adherence``: the execution followed the plan the agent stated."""

from __future__ import annotations

from ...judges.prompts.agent import plan_adherence as prompts
from ..base import MetricSpec
from ..judge_base import native_method
from ..registry import register
from ._section_judge import AGENT_JUDGE_PARAMS, SectionJudge


@register
class PlanAdherence(SectionJudge):
    """LLM-step texts are shown as the ``plan`` section (where explicit plans live)."""

    templates = prompts.VERSIONS
    optional_sections = ("input", "plan")
    spec = MetricSpec(
        id="agent.plan_adherence",
        category="agent",
        description="The agent's execution followed its stated plan (5-tier judge).",
        methods=(native_method("trajectory", optional=("input",)),),
        params_schema=AGENT_JUDGE_PARAMS,
        default_threshold=0.5,
        version="2",  # v2: with agent_id, plan text keeps steps without an agent id
        sources=("deepeval:plan_adherence",),
    )
