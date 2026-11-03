"""Tau-bench result files -> Session.

Input: one task result ``{"task_id", "reward", "info", "traj": [messages], "trial"}`` or a list of
them. ``traj`` holds OpenAI-style chat messages (``system`` / ``user`` / ``assistant`` with
``tool_calls: [{"id", "function": {"name", "arguments": "<json>"}}]`` / ``tool``).

Provenance: AgentSight ``TauBenchSessionMapper`` (taubench_session_mapper.py). Each user turn
(a user message and everything up to the next one) becomes a Trace with one root agent step
(system prompt, user prompt, final response, the airline tool catalog) and, under it, one LLM
step per assistant message (inputs = the conversation so far) and one tool step per tool
result (matched to its call by ``tool_call_id``; ``Error:`` results are errors). Turns without
an assistant message are skipped. Tool definitions: ``info.tools`` / ``tools`` when present,
else the tau-bench airline catalog AgentSight hard-codes.

Deviations from AgentSight: ids are deterministic (``t<task>-r<trial>-turn<n>[-sN]``, not
UUIDs) and there are no fabricated timestamps (AgentSight stamped ``datetime.now()``); steps keep
conversation order (AgentSight put all LLM steps before all tool steps); the airline catalog
keeps names, descriptions and required parameters only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import (
    Warnings,
    arguments,
    as_dict,
    as_list,
    finalize_trace,
    message,
    text_of,
    tool_def,
)

# (name, description, required parameters) of the tau-bench airline environment tools.
AIRLINE_TOOLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("book_reservation", "Book a reservation.", (
        "user_id", "origin", "destination", "flight_type", "cabin", "flights", "passengers",
        "payment_methods", "total_baggages", "nonfree_baggages", "insurance")),
    ("calculate", "Calculate the result of a mathematical expression.", ("expression",)),
    ("cancel_reservation", "Cancel the whole reservation.", ("reservation_id",)),
    ("get_reservation_details", "Get the details of a reservation.", ("reservation_id",)),
    ("get_user_details", "Get the details of an user, including their reservations.",
     ("user_id",)),
    ("list_all_airports", "List all airports and their cities.", ()),
    ("search_direct_flight", "Search direct flights between two cities on a specific date.",
     ("origin", "destination", "date")),
    ("search_onestop_flight", "Search one-stop flights between two cities on a specific date.",
     ("origin", "destination", "date")),
    ("send_certificate", "Send a certificate to a user. Be careful!", ("user_id", "amount")),
    ("think", "Use the tool to think about something. It will not obtain new information or "
     "change the database, but just append the thought to the log. Use it when complex "
     "reasoning is needed.", ("thought",)),
    ("transfer_to_human_agents", "Transfer the user to a human agent, with a summary of the "
     "user's issue. Only transfer if the user explicitly asks for a human agent, or if the "
     "user's issue cannot be resolved by the agent with the available tools.", ("summary",)),
    ("update_reservation_baggages", "Update the baggage information of a reservation.",
     ("reservation_id", "total_baggages", "nonfree_baggages", "payment_id")),
    ("update_reservation_flights", "Update the flight information of a reservation.",
     ("reservation_id", "cabin", "flights", "payment_id")),
    ("update_reservation_passengers", "Update the passenger information of a reservation.",
     ("reservation_id", "passengers")),
)  # fmt: skip


def airline_tools() -> tuple[dict[str, Any], ...]:
    return tuple(
        tool_def(
            name,
            description,
            {
                "type": "object",
                "properties": {p: {} for p in required},
                "required": list(required),
            },
        )
        for name, description, required in AIRLINE_TOOLS
    )


def _tasks(data: Any) -> list[Mapping[str, Any]]:
    items = data if isinstance(data, (list, tuple)) else [data]
    return [t for t in items if isinstance(t, Mapping) and isinstance(t.get("traj"), list)]


def _tools(task: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    declared = as_list(task.get("tools")) or as_list(as_dict(task.get("info")).get("tools"))
    tools = []
    for item in declared:
        item = as_dict(item)
        function = as_dict(item.get("function")) or item
        if function.get("name"):
            tools.append(
                tool_def(function["name"], function.get("description"), function.get("parameters"))
            )
    return tuple(tools) or airline_tools()


def _calls(msg: Mapping[str, Any]) -> list[ToolCall]:
    calls = []
    for index, raw in enumerate(as_list(msg.get("tool_calls"))):
        raw = as_dict(raw)
        function = as_dict(raw.get("function"))
        args = function.get("arguments", "{}")
        try:
            parsed = arguments(args)
        except Exception:  # noqa: BLE001
            parsed = {"raw_arguments": args}
        calls.append(
            ToolCall(
                name=str(function.get("name", "")),
                arguments=parsed,
                id=raw.get("id") or f"call_{index}",
            )
        )
    return calls


def _as_message(msg: Mapping[str, Any]) -> dict[str, Any] | None:
    role = msg.get("role")
    if role == "user":
        return message("user", msg.get("content") or "")
    if role == "assistant":
        calls = _calls(msg)
        if not msg.get("content") and not calls:
            return None
        return message(
            "assistant",
            msg.get("content") or "",
            tool_calls=[
                {"id": c.id, "name": c.name, "arguments": dict(c.arguments)} for c in calls
            ],
        )
    if role == "tool":
        return message("tool", msg.get("content") or "", tool_call_id=msg.get("tool_call_id"))
    return None


def _turns(traj: list[Any]) -> list[list[Mapping[str, Any]]]:
    turns: list[list[Mapping[str, Any]]] = []
    for msg in traj:
        if not isinstance(msg, Mapping) or msg.get("role") == "system":
            continue
        if msg.get("role") == "user":
            turns.append([msg])
        elif turns and msg.get("role") in ("assistant", "tool"):
            turns[-1].append(msg)
    return turns


class TauBenchAdapter:
    name = "taubench"

    def detect(self, data: Any) -> float:
        tasks = _tasks(data)
        if not tasks:
            return 0.0
        return 0.95 if all("task_id" in t for t in tasks) else 0.5

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        warnings = Warnings()
        tasks = _tasks(data)
        if not tasks:
            warnings.add("no tau-bench tasks (with a traj) found")
        traces = []
        for task in tasks:
            traces.extend(self._task_traces(task, warnings))
        if session_id is None:
            first = tasks[0] if tasks else {}
            if isinstance(data, (list, tuple)):
                session_id = f"tau-bench-session-{first.get('task_id', 'unknown')}"
            else:
                session_id = (
                    f"tau-bench-task-{first.get('task_id', 0)}-trial-{first.get('trial', 0)}"
                )
        return Session(id=session_id, traces=tuple(traces), warnings=warnings.as_tuple())

    def _task_traces(self, task: Mapping[str, Any], warnings: Warnings) -> list[Any]:
        task_id, trial = task.get("task_id", 0), task.get("trial", 0)
        traj = list(task["traj"])
        system = next(
            (text_of(m.get("content")) for m in traj if as_dict(m).get("role") == "system"), None
        )
        tools = _tools(task)
        history: list[dict[str, Any]] = [message("system", system)] if system else []
        traces = []
        for n, turn in enumerate(_turns(traj)):
            assistants = [m for m in turn if m.get("role") == "assistant"]
            if not assistants:
                history.extend(m for m in map(_as_message, turn) if m)
                continue
            trace_id = f"t{task_id}-r{trial}-turn{n}"
            response = ""
            for msg in reversed(assistants):
                content = msg.get("content") or ""
                if content and not msg.get("tool_calls"):
                    response = content
                    break
                if content and not response:
                    response = content
            agent = AgentStep(
                id=trace_id,
                agent_id=f"tau-bench-agent-task-{task_id}",
                system_prompt=system,
                user_prompt=text_of(turn[0].get("content")) or None,
                response=response or None,
                available_tools=tools,
            )
            steps: list[Step] = [agent]
            pending: dict[str, ToolCall] = {}
            for index, msg in enumerate(turn):
                normalized = _as_message(msg)
                step_id = f"{trace_id}-s{index}"
                if msg.get("role") == "assistant" and normalized:
                    calls = _calls(msg)
                    pending.update({c.id: c for c in calls if c.id})
                    steps.append(
                        LlmStep(
                            id=step_id,
                            parent_id=agent.id,
                            messages=tuple(history),
                            response=msg.get("content") or None,
                            tool_calls=tuple(calls),
                        )
                    )
                elif msg.get("role") == "tool":
                    call_id = msg.get("tool_call_id") or ""
                    content = text_of(msg.get("content"))
                    call = pending.pop(call_id, None)
                    if call is None:
                        warnings.add(
                            f"{trace_id}: tool result with unknown tool_call_id {call_id!r}"
                        )
                        call = ToolCall(
                            name=str(msg.get("name", "")), arguments={}, id=call_id or None
                        )
                    error = content if content.startswith("Error:") else None
                    steps.append(
                        ToolStep(
                            id=step_id,
                            parent_id=agent.id,
                            error=error,
                            call=call,
                            result=ToolResult(
                                content=content, error=error, call_id=call_id or None
                            ),
                        )
                    )
                if normalized:
                    history.append(normalized)
            traces.append(finalize_trace(trace_id, steps, warnings, expect_timestamps=False))
        return traces
