"""``agent.mcp_task_completion``: the MCP-based interaction completed the user's task."""

from __future__ import annotations

from ...judges.prompts.agent import mcp_task_completion as prompts
from ..base import MetricSpec
from ..judge_base import native_method
from ..registry import register
from ._mcp import McpJudge
from ._section_judge import AGENT_JUDGE_PARAMS


@register
class McpTaskCompletion(McpJudge):
    """Trajectory, or a conversation with tool calls; skipped when nothing MCP is found."""

    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="agent.mcp_task_completion",
        category="agent",
        description="The MCP-based interaction accomplished the user's task (5-tier judge).",
        methods=(native_method("trajectory", optional=("input", "conversation")),),
        params_schema=AGENT_JUDGE_PARAMS,
        default_threshold=0.5,
        version="2",  # v2: an LLM call and the tool step executing it count once
        sources=("deepeval:mcp_task_completion",),
    )
