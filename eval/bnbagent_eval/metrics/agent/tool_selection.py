"""``agent.tool_selection``: the right tools were chosen from the available tools."""

from __future__ import annotations

from ...judges.prompts.agent import tool_selection as prompts
from ..base import MetricSpec
from ..judge_base import native_method
from ..registry import register
from ._section_judge import AGENT_JUDGE_PARAMS, SectionJudge


@register
class ToolSelection(SectionJudge):
    """Optional sections: user query, available tools, reference tool calls (``expected``)."""

    templates = prompts.VERSIONS
    optional_sections = ("input", "tools", "expected")
    separate_tools = True
    spec = MetricSpec(
        id="agent.tool_selection",
        category="agent",
        description="The agent chose the right tools for the task (5-tier judge).",
        methods=(native_method("trajectory", optional=("input", "expected")),),
        params_schema=AGENT_JUDGE_PARAMS,
        default_threshold=0.5,
        sources=(
            "etk:llm_as_judge.tool_use",
            "deepeval:tool_use",
            "agentcore:Builtin.ToolSelectionAccuracy",
        ),
    )
