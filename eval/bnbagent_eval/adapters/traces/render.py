"""Render a normalized Session as judge-prompt text.

Same layout as AgentSight ``generate_conversation_text_history``: ``# Available tools`` and
``# Conversation record`` with ``User:`` / ``Assistant:`` / ``Action:`` lines.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...hashing import canonical_json
from ...types.case import Message
from ...types.trajectory import (
    AgentStep,
    DelegationStep,
    HandoffStep,
    LlmStep,
    Session,
    ToolCall,
    ToolStep,
    Trace,
    TxStep,
)


def _compact(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + " …"


def available_tools(session: Session) -> list[Mapping[str, Any]]:
    """Unique tools declared by agent steps (by name, first declaration wins)."""
    seen: dict[str, Mapping[str, Any]] = {}
    for step in session.steps():
        if isinstance(step, AgentStep):
            for tool in step.available_tools:
                name = str(tool.get("name", ""))
                if name and name not in seen:
                    seen[name] = tool
    return list(seen.values())


def tools_text(session: Session) -> str:
    return "\n\n".join(
        f"- {t.get('name')}: {t.get('description') or 'No description'}"
        for t in available_tools(session)
    )


def _agents(session: Session) -> set[str]:
    return {s.agent_id for s in session.steps() if s.agent_id}


def _trace_lines(trace: Trace, agent_id: str | None, multi: bool) -> list[str]:
    lines: list[str] = []
    pending_responses: list[str] = []
    executed = executed_llm_calls(trace)
    for n, step in enumerate(trace.steps):
        if agent_id is not None and step.agent_id not in (agent_id, None):
            continue
        who = f" [{step.agent_id}]" if multi and step.agent_id else ""
        if isinstance(step, AgentStep):
            if step.user_prompt:
                lines.append(f"User{who}: {step.user_prompt}")
            if step.response:
                pending_responses.append(f"Assistant{who}: {step.response}")
        elif isinstance(step, LlmStep):
            if step.response and step.tool_calls:
                lines.append(f"Assistant{who}: {step.response}")
            for k, call in enumerate(step.tool_calls):
                if (n, k) not in executed:  # executed calls are shown with their tool step
                    lines.append(f"Action{who}: {call.name}({_compact(dict(call.arguments))})")
        elif isinstance(step, ToolStep):
            lines.append(f"Action{who}: {step.call.name}({_compact(dict(step.call.arguments))})")
            if step.result is not None:
                if step.result.error:
                    lines.append(f"Tool error: {_compact(step.result.error)}")
                else:
                    lines.append(f"Tool result: {_compact(step.result.content)}")
        elif isinstance(step, (DelegationStep, HandoffStep)):
            verb = "Delegated" if isinstance(step, DelegationStep) else "Handed off"
            lines.append(f"[{verb}: {step.from_agent} -> {step.to_agent}]")
        elif isinstance(step, TxStep):
            parts = [f"chain={step.chain_id}", f"to={step.to}", f"value={step.value}"]
            if step.decoded and step.decoded.function:
                parts.append(f"call={step.decoded.function}({_compact(dict(step.decoded.args))})")
            if step.status is not None:
                parts.append(f"status={'success' if step.status == 1 else 'failed'}")
            intent = f" intent: {step.intent}" if step.intent else ""
            lines.append(f"Transaction{who}: {' '.join(parts)}{intent}")
        if step.error and not isinstance(step, ToolStep):
            lines.append(f"Error{who}: {_compact(step.error)}")
    return lines + pending_responses


def _same_call(a: ToolCall, b: ToolCall) -> bool:
    return a.name == b.name and canonical_json(dict(a.arguments)) == canonical_json(
        dict(b.arguments)
    )


def executed_llm_calls(trace: Trace) -> set[tuple[int, int]]:
    """``(step index, call index)`` of LLM tool calls that a tool step of ``trace`` executes:
    matched by id, or by name and arguments when either side has no id (first in, first out),
    the same rule as ``metrics.agent._whitebox.tool_uses``."""
    tool_ids = {s.call.id for s in trace.steps if isinstance(s, ToolStep) and s.call.id}
    executed: set[tuple[int, int]] = set()
    pending: list[tuple[int, int, ToolCall]] = []
    for n, step in enumerate(trace.steps):
        if isinstance(step, LlmStep):
            for k, call in enumerate(step.tool_calls):
                if call.id and call.id in tool_ids:
                    executed.add((n, k))
                else:
                    pending.append((n, k, call))
        elif isinstance(step, ToolStep):
            match = next(
                (
                    p
                    for p in pending
                    if (p[2].id is None or step.call.id is None) and _same_call(p[2], step.call)
                ),
                None,
            )
            if match is not None:
                pending.remove(match)
                executed.add(match[:2])
    return executed


def render_trajectory(session: Session, *, agent_id: str | None = None) -> str:
    """Judge-prompt text for the whole session (or one agent of a multi-agent session)."""
    multi = len(_agents(session)) > 1 and agent_id is None
    parts = []
    tools = tools_text(session)
    if tools:
        parts.append(f"# Available tools\n{tools}")
    record = [line for trace in session.traces for line in _trace_lines(trace, agent_id, multi)]
    if record:
        parts.append("# Conversation record\n" + "\n".join(record))
    return "\n\n".join(parts)


def session_messages(session: Session) -> tuple[Message, ...]:
    """User / assistant turns derived from agent steps (for conversation metrics)."""
    out: list[Message] = []
    for step in session.steps():
        if isinstance(step, AgentStep) and step.parent_id is None:
            if step.user_prompt:
                out.append(Message(role="user", content=step.user_prompt))
            if step.response:
                out.append(Message(role="assistant", content=step.response))
    return tuple(out)
