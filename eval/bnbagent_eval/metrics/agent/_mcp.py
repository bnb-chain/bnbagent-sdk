"""MCP tool-call detection and the shared base of the agent.mcp_* judges.

Convention: a tool call is an MCP call when
- its ``ToolCall.arguments`` carry an ``mcp_server`` key (server = that value, tool = name), or
- its name is ``mcp__<server>__<tool>`` (Claude Code / Agent SDK naming), or
- its name is ``<server>.<tool>`` (first dot separates server and tool).
A declared tool (``AgentStep.available_tools``) is an MCP tool when its name follows the same
convention or the declaration has an ``mcp_server`` key.

Calls come from the trajectory (tool steps, plus LLM tool calls without a tool step) or, in
conversation mode, from ``Message.tool_calls`` with results from ``tool`` messages.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...adapters.traces.render import available_tools
from ...types.case import EvalCase, Message
from ...types.result import ErrorInfo
from ...types.trajectory import Session, ToolCall
from ..base import MetricContext, Outcome
from ._section_judge import SectionJudge
from ._whitebox import tool_uses

MCP_PREFIX = "mcp__"
NO_MCP = "no MCP tool calls or MCP tools found in the trajectory or conversation"


@dataclass(frozen=True)
class McpCall:
    server: str
    tool: str
    arguments: Mapping[str, Any]
    result: Any = None
    error: str | None = None


def mcp_target(name: str, arguments: Mapping[str, Any] | None = None) -> tuple[str, str] | None:
    """``(server, tool)`` if ``name`` / ``arguments`` follow the MCP convention, else None."""
    if arguments and arguments.get("mcp_server"):
        return str(arguments["mcp_server"]), name
    if name.startswith(MCP_PREFIX):
        server, sep, tool = name[len(MCP_PREFIX) :].partition("__")
        return (server, tool) if sep and server and tool else None
    server, sep, tool = name.partition(".")
    return (server, tool) if sep and server and tool else None


def _call(call: ToolCall, result: Any = None, error: str | None = None) -> McpCall | None:
    target = mcp_target(call.name, call.arguments)
    if target is None:
        return None
    return McpCall(target[0], target[1], dict(call.arguments), result, error)


def session_mcp_calls(session: Session | None, agent_id: str | None = None) -> list[McpCall]:
    if session is None:
        return []
    out: list[McpCall | None] = []
    for trace in session.traces:
        # one entry per call: an LLM call and the tool step executing it are merged
        for use in tool_uses(trace):
            if agent_id is not None and use.agent_id not in (agent_id, None):
                continue
            res = use.result
            out.append(_call(use.call, res and res.content, res and res.error))
    return [c for c in out if c is not None]


def conversation_mcp_calls(messages: Sequence[Message]) -> list[McpCall]:
    results = {m.tool_call_id: m.content for m in messages if m.role == "tool" and m.tool_call_id}
    calls = [_call(c, results.get(c.id) if c.id else None) for m in messages for c in m.tool_calls]
    return [c for c in calls if c is not None]


def mcp_tools(session: Session | None) -> list[Mapping[str, Any]]:
    if session is None:
        return []
    return [
        t
        for t in available_tools(session)
        if t.get("mcp_server") or mcp_target(str(t.get("name", ""))) is not None
    ]


def _json(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " ..."


def calls_text(calls: Sequence[McpCall]) -> str:
    if not calls:
        return "(no MCP tool calls detected)"
    lines = []
    for i, c in enumerate(calls, 1):
        line = f"{i}. server={c.server} tool={c.tool} arguments={_json(dict(c.arguments))}"
        if c.error:
            line += f"\n   error: {_json(c.error)}"
        elif c.result is not None:
            line += f"\n   result: {_json(c.result)}"
        lines.append(line)
    return "\n".join(lines)


def tools_list_text(tools: Sequence[Mapping[str, Any]]) -> str:
    if not tools:
        return "(no MCP tools declared)"
    lines = []
    for t in tools:
        server = t.get("mcp_server")
        name = f"{server}/{t.get('name')}" if server else str(t.get("name"))
        lines.append(f"- {name}: {t.get('description') or 'No description'}")
    return "\n".join(lines)


def conversation_record(messages: Sequence[Message]) -> str:
    """``Role: content`` turns, with ``Action:`` lines for tool calls and tool results."""
    lines = []
    for m in messages:
        if m.role == "tool":
            lines.append(f"Tool result: {_json(m.content)}")
            continue
        if m.content:
            lines.append(f"{m.role.capitalize()}: {m.content}")
        lines.extend(f"Action: {c.name}({_json(dict(c.arguments))})" for c in m.tool_calls)
    return "\n".join(lines)


class McpJudge(SectionJudge):
    """Judges MCP use in a trajectory (``simple``) or a conversation (``conversation``)."""

    optional_sections = ("input",)

    def has_input(self, name: str, case: EvalCase, ctx: MetricContext) -> bool:
        if name == "trajectory" and any(m.tool_calls for m in case.conversation):
            return True  # a conversation with tool calls stands in for the trajectory
        return super().has_input(name, case, ctx)

    def base_mode(self, case: EvalCase, ctx: MetricContext) -> str:
        if case.trajectory is None:
            return "conversation"
        agent_id = ctx.params.get("agent_id")
        if not session_mcp_calls(case.trajectory, agent_id) and conversation_mcp_calls(
            case.conversation
        ):
            return "conversation"
        return "simple"

    def calls(self, case: EvalCase, ctx: MetricContext, mode: str) -> list[McpCall]:
        if mode.startswith("conversation"):
            return conversation_mcp_calls(case.conversation)
        return session_mcp_calls(case.trajectory, ctx.params.get("agent_id"))

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = super().fields(case, ctx, mode)
        out["mcp_tools"] = tools_list_text(mcp_tools(case.trajectory))
        out["mcp_calls"] = calls_text(self.calls(case, ctx, mode))
        out["mcp_conversation"] = conversation_record(case.conversation)
        return out

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        template = self.template_for(ctx.params)
        mode = self.select_mode(case, ctx, template)
        calls = self.calls(case, ctx, mode)
        tools = mcp_tools(case.trajectory)
        if not calls and not tools:
            return Outcome(None, status="skipped", error=ErrorInfo("missing_input", NO_MCP))
        out = await super()._evaluate_native(case, ctx)
        servers = sorted({c.server for c in calls})
        return dataclasses.replace(
            out, details={**out.details, "mcp_calls": len(calls), "mcp_servers": servers}
        )
