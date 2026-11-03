"""CX0 multi-agent trajectories -> Session.

Input: the CX0 trajectory ``{"steps": [{"agent": "<id>_<name>", "steps": [message, ...]}]}``
(the ``==== Trajectory ====`` section of a CX0 dump), the bare list of agent blocks, or a dict
with ``"trajectory"`` holding either. Messages are ``{"role": "system" | "user" | "assistant",
"content", "name"?, "tool_calls": [{"id", "name", "arguments"}]?}``.

Provenance: AgentSight ``CX0SessionMapper`` (cx0_mapper.py). Agent blocks are read in reverse
order; inside a block, ``assistant`` messages carrying a ``name`` split it into per-agent
sub-blocks (system + user + that message), otherwise the block is one agent. Each (sub-)block
becomes an agent step (system prompt, first user prompt, last assistant content, tools seen in
``tool_calls``) followed by one tool step per tool call (CX0 records no tool results).

Detection: AgentSight's ``_detect_cx0`` looked for span dicts with ``span_id`` /
``trace_id`` / ``operation_name`` / ``start_time`` / ``end_time``, a shape the mapper never
reads, so CX0 input was never detected (and span-shaped input was routed to a mapper that could
not parse it). This adapter detects the agent-block shape the mapper actually parses.

Deviations from AgentSight: ids are deterministic (``cx0-<n>``) and no timestamps are invented
(AgentSight used UUIDs and ``datetime.now()``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.trajectory import AgentStep, Session, Step, ToolCall, ToolStep
from ._common import Warnings, arguments, as_dict, as_list, finalize_trace, text_of, tool_def


def _blocks(data: Any) -> list[Mapping[str, Any]] | None:
    """The agent blocks, or ``None`` when ``data`` is not CX0."""
    if isinstance(data, Mapping):
        data = data.get("trajectory", data)
        if isinstance(data, Mapping):
            data = data.get("steps")
    if not isinstance(data, (list, tuple)) or not data:
        return None
    if not all(
        isinstance(b, Mapping) and "agent" in b and isinstance(b.get("steps"), list) for b in data
    ):
        return None
    return list(data)


def _is_message_list(steps: list[Any]) -> bool:
    return all(isinstance(s, Mapping) and isinstance(s.get("role"), str) for s in steps)


def _group(steps: list[Mapping[str, Any]]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    blocks = []
    system = user = None
    for step in steps:
        role = step.get("role")
        if role == "system":
            if system is not None and user is not None:
                user = None
            system = step
        elif role == "user":
            user = step
        elif role == "assistant" and "name" in step:
            block = [m for m in (system, user) if m is not None] + [step]
            system = user = None
            blocks.append((str(step["name"]), block))
    return blocks


def _first(steps: list[Mapping[str, Any]], role: str) -> str | None:
    for step in steps:
        if step.get("role") == role:
            return text_of(step.get("content", ""))
    return None


def _response(steps: list[Mapping[str, Any]]) -> str:
    for step in reversed(steps):
        if step.get("role") == "assistant" and "content" in step:
            return text_of(step.get("content", ""))
    return ""


def _calls(steps: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [as_dict(c) for s in steps for c in as_list(s.get("tool_calls"))]


class Cx0Adapter:
    name = "cx0"

    def detect(self, data: Any) -> float:
        blocks = _blocks(data)
        if blocks is None:
            return 0.0
        inner = [s for b in blocks for s in b["steps"]]
        return 0.9 if inner and _is_message_list(inner) else 0.5

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        warnings = Warnings()
        blocks = _blocks(data) or []
        if not blocks:
            warnings.add("no CX0 agent blocks found")
        steps: list[Step] = []
        counter = 0

        def next_id() -> str:
            nonlocal counter
            counter += 1
            return f"cx0-{counter}"

        for block in reversed(blocks):
            parent_agent = text_of(block.get("agent")) or "unknown"
            messages = [s for s in block["steps"] if isinstance(s, Mapping)]
            if not messages:
                continue
            groups = _group(messages)
            units = groups or [(parent_agent, messages)]
            for agent_name, unit in units:
                user = _first(unit, "user")
                response = _response(unit)
                if not (response if groups else (user or response)):
                    continue
                tools = []
                for call in _calls(unit):
                    if call.get("name") and call["name"] not in [t["name"] for t in tools]:
                        tools.append(tool_def(call["name"]))
                agent = AgentStep(
                    id=next_id(),
                    agent_id=agent_name or "unknown",
                    system_prompt=_first(unit, "system"),
                    user_prompt=user or None,
                    response=response or None,
                    available_tools=tuple(tools),
                )
                steps.append(agent)
                for call in _calls(unit):
                    steps.append(
                        ToolStep(
                            id=next_id(),
                            parent_id=agent.id,
                            call=ToolCall(
                                name=str(call.get("name", "")),
                                arguments=arguments(call.get("arguments")),
                                id=call.get("id"),
                            ),
                        )
                    )
        sid = session_id or "cx0-session"
        traces = (
            (finalize_trace("cx0-trace", steps, warnings, expect_timestamps=False, sort=False),)
            if steps
            else ()
        )
        if not steps:
            warnings.add("CX0 trajectory produced no steps")
        return Session(id=sid, traces=traces, warnings=warnings.as_tuple())
