"""LangSmith run exports (LangGraph) -> Session.

Input: JSON-serialized LangSmith ``Run`` dicts (``run.dict()`` / ``GET /runs``): ``{"id",
"name", "run_type", "start_time", "end_time", "parent_run_id", "trace_id", "session_id",
"inputs", "outputs", "extra", "error", ...}``. Accepted as a flat list of runs (grouped by
``trace_id``), a list of per-trace run lists, ``{"runs": [...]}``, or nested ``child_runs``.

Provenance: AgentSight ``LanggraphLangsmithSessionMapper`` (langgraph_langsmith_mapper.py).
``run_type`` ``llm`` -> LLM step (LangChain-serialized messages, ``outputs.generations``);
``tool`` -> tool step (``inputs.input`` as a Python-literal dict, ``outputs.output``);
``chain`` named ``LangGraph`` (top level, or with ``checkpoint_ns``) -> agent step whose system
prompt comes from the first descendant LLM run; agent id ``<name>_<sha256(prompt)[:8]>``
(``<name>_<run id[:8]>`` without one). Runs are sorted by ``start_time`` and re-parented to
the nearest mapped ancestor.

Deviations from AgentSight: when the agent run has no ``invocation_params.tools`` the tools are
taken from its first descendant LLM run (LangGraph chain runs never carry them); system messages
stay in LLM step inputs; run ``error`` / tool status ``error`` become step errors; token usage,
cost and model are kept; flat run lists are grouped by ``trace_id``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import (
    Warnings,
    agent_key,
    arguments,
    as_dict,
    as_list,
    calls_from_message,
    dedupe_tools,
    finalize_trace,
    get,
    last_assistant,
    message,
    reparent,
    text_of,
    to_ms,
    tool_def,
    usage,
)

AGENT_NAMES = ("LangGraph",)


def _is_run(value: Any) -> bool:
    return isinstance(value, Mapping) and "run_type" in value and "id" in value


def _flatten(runs: Sequence[Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for run in runs:
        if _is_run(run):
            out.append(run)
            out.extend(_flatten(as_list(run.get("child_runs"))))
    return out


def run_groups(data: Any) -> list[list[Mapping[str, Any]]]:
    """Runs grouped per trace (input order of first appearance)."""
    if isinstance(data, Mapping):
        data = data.get("runs", [data])
    if not isinstance(data, (list, tuple)) or not data:
        return []
    if all(isinstance(item, (list, tuple)) for item in data):
        return [g for g in (_flatten(list(item)) for item in data) if g]
    runs = _flatten(list(data))
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        key = str(run.get("trace_id") or "")
        groups.setdefault(key, []).append(run)
    return list(groups.values())


def _metadata(run: Mapping[str, Any]) -> dict[str, Any]:
    return as_dict(as_dict(run.get("extra")).get("metadata"))


def _is_agent(run: Mapping[str, Any]) -> bool:
    if run.get("run_type") != "chain" or run.get("name") not in AGENT_NAMES:
        return False
    metadata = _metadata(run)
    return not metadata.get("langgraph_step") or "checkpoint_ns" in metadata


def _lc_messages(value: Any) -> list[Any]:
    messages = as_list(value)
    if messages and isinstance(messages[0], list):
        messages = messages[0]
    return messages


def _lc_message(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    kwargs = as_dict(raw.get("kwargs")) or dict(raw)
    kind = kwargs.get("type")
    content = kwargs.get("content", "")
    if kind == "system":
        return message("system", content)
    if kind == "human":
        return message("user", content)
    if kind == "ai":
        calls = [
            {
                "id": c.get("id"),
                "name": str(c.get("name", "")),
                "arguments": arguments(c.get("args")),
            }
            for c in as_list(kwargs.get("tool_calls"))
            if isinstance(c, Mapping)
        ]
        return message("assistant", content, tool_calls=calls) if content or calls else None
    if kind == "tool":
        return message("tool", content, tool_call_id=kwargs.get("tool_call_id"))
    return None


def _tools_of(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    params = as_dict(as_dict(run.get("extra")).get("invocation_params"))
    return [
        tool_def(t.get("name", ""), t.get("description"), t.get("input_schema"))
        for t in as_list(params.get("tools"))
        if isinstance(t, Mapping)
    ]


def _first_llm(
    run_id: str, children: Mapping[str, list[Mapping[str, Any]]], seen: set[str]
) -> list[Mapping[str, Any]]:
    """LLM descendants of ``run_id`` in depth-first order (cycle-safe)."""
    out: list[Mapping[str, Any]] = []
    for child in children.get(run_id, []):
        child_id = str(child.get("id"))
        if child_id in seen:
            continue
        seen.add(child_id)
        if child.get("run_type") == "llm":
            out.append(child)
        out.extend(_first_llm(child_id, children, seen))
    return out


def _system_prompt(llm_runs: Sequence[Mapping[str, Any]]) -> str | None:
    for run in llm_runs:
        for raw in _lc_messages(as_dict(run.get("inputs")).get("messages")):
            msg = _lc_message(raw)
            if msg and msg["role"] == "system":
                return str(msg["content"])
    return None


def _user_prompt(inputs: Any) -> str:
    for msg in as_list(as_dict(inputs).get("messages")):
        if isinstance(msg, Mapping):
            if msg.get("role") == "user" or msg.get("type") == "human":
                return text_of(msg.get("content", ""))
        elif isinstance(msg, list) and len(msg) == 2 and msg[0] == "user":
            return text_of(msg[1])
    return ""


def _agent_response(outputs: Any) -> str:
    for msg in reversed(as_list(as_dict(outputs).get("messages"))):
        if isinstance(msg, Mapping) and msg.get("type") == "ai":
            return text_of(msg.get("content", ""))
    return ""


def _base(run: Mapping[str, Any], parent: str | None) -> dict[str, Any]:
    start = to_ms(run.get("start_time"))
    error = run.get("error")
    return {
        "id": str(run.get("id")),
        "parent_id": parent,
        "start_ms": start,
        "end_ms": to_ms(run.get("end_time")) or start,
        "error": text_of(error) if error else None,
    }


def _model(run: Mapping[str, Any]) -> str | None:
    params = as_dict(as_dict(run.get("extra")).get("invocation_params"))
    return get(params, "model_id", "model", "model_name") or _metadata(run).get("ls_model_name")


def _map_run(
    run: Mapping[str, Any],
    children: Mapping[str, list[Mapping[str, Any]]],
) -> Step | None:
    parent = str(run["parent_run_id"]) if run.get("parent_run_id") else None
    kind = run.get("run_type")
    if kind == "llm":
        inputs = [
            m
            for m in map(_lc_message, _lc_messages(as_dict(run.get("inputs")).get("messages")))
            if m
        ]
        outputs = []
        for generations in as_list(as_dict(run.get("outputs")).get("generations")):
            for generation in as_list(generations):
                msg = _lc_message(as_dict(generation).get("message"))
                if msg is None and isinstance(generation, Mapping) and generation.get("text"):
                    msg = message("assistant", generation["text"])
                if msg:
                    outputs.append(msg)
        final = last_assistant(outputs)
        return LlmStep(
            **_base(run, parent),
            usage=usage(
                run.get("prompt_tokens"), run.get("completion_tokens"), cost=run.get("total_cost")
            ),
            model=_model(run),
            messages=tuple(inputs),
            response=(final or {}).get("content") or None,
            tool_calls=calls_from_message(final),
        )
    if kind == "tool":
        if not run.get("outputs"):
            return None
        output = as_dict(run["outputs"]).get("output", {})
        if isinstance(output, Mapping):
            result, call_id, status = (
                text_of(output.get("content", "")),
                output.get("tool_call_id"),
                output.get("status"),
            )
        else:
            result, call_id, status = text_of(output), None, None
        tool_error = result if status == "error" else None
        base = _base(run, parent)
        return ToolStep(
            **{**base, "error": base["error"] or tool_error},
            call=ToolCall(
                name=str(run.get("name", "")),
                arguments=arguments(as_dict(run.get("inputs")).get("input")),
                id=call_id or None,
            ),
            result=ToolResult(content=result, error=tool_error, call_id=call_id or None),
        )
    if _is_agent(run):
        run_id = str(run.get("id"))
        llm_runs = _first_llm(run_id, children, set())
        system = _system_prompt(llm_runs)
        name = str(run.get("name") or "agent")
        tools = _tools_of(run) or (_tools_of(llm_runs[0]) if llm_runs else [])
        return AgentStep(
            **_base(run, parent),
            agent_id=agent_key(name, system) if system else f"{name}_{run_id[:8]}",
            system_prompt=system,
            user_prompt=_user_prompt(run.get("inputs")) or None,
            response=_agent_response(run.get("outputs")) or None,
            available_tools=dedupe_tools(tools),
        )
    return None


class LangsmithAdapter:
    name = "langsmith_langgraph"

    def detect(self, data: Any) -> float:
        groups = run_groups(data)
        if not groups:
            return 0.0
        runs = [r for g in groups for r in g]
        return 0.95 if any("parent_run_id" in r for r in runs) else 0.7

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        warnings = Warnings()
        groups = run_groups(data)
        if not groups:
            warnings.add("no LangSmith runs found")
        traces = []
        for runs in groups:
            trace_id = str(
                next((r["trace_id"] for r in runs if r.get("trace_id")), runs[0].get("id"))
            )
            if all(to_ms(r.get("start_time")) is not None for r in runs):
                runs = sorted(runs, key=lambda r: to_ms(r.get("start_time")) or 0.0)
            else:
                warnings.add(f"trace {trace_id!r}: runs without start_time kept in input order")
            children: dict[str, list[Mapping[str, Any]]] = {}
            for run in runs:
                if run.get("parent_run_id"):
                    children.setdefault(str(run["parent_run_id"]), []).append(run)
            steps: list[Step] = []
            for run in runs:
                try:
                    step = _map_run(run, children)
                except Exception as exc:  # noqa: BLE001 - one bad run must not sink the trace
                    warnings.add(
                        f"run {run.get('id')!r}: cannot map ({type(exc).__name__}: {exc})"
                    )
                    continue
                if step is not None:
                    steps.append(step)
            raw_parents = {
                str(r.get("id")): (str(r["parent_run_id"]) if r.get("parent_run_id") else None)
                for r in runs
            }
            steps = reparent(steps, raw_parents, warnings)
            if steps:
                traces.append(finalize_trace(trace_id, steps, warnings))
            else:
                warnings.add(f"trace {trace_id!r} produced no steps")
        sid = session_id
        if sid is None:
            first = next((r for g in groups for r in g if r.get("session_id")), None)
            sid = str(first["session_id"]) if first else (traces[0].id if traces else "session")
        return Session(id=sid, traces=tuple(traces), warnings=warnings.as_tuple())


__all__ = ["LangsmithAdapter", "run_groups"]
