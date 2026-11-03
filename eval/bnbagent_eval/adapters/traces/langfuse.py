"""Langfuse trace exports (Strands, LangChain and LangGraph variants) -> Session.

Input: the JSON form of Langfuse ``TraceWithFullDetails`` (``GET /api/public/traces/{id}``):
``{"id", "sessionId", "observations": [{"id", "type", "name", "startTime", "endTime",
"parentObservationId", "input", "output", "metadata", "model", "usageDetails", ...}]}``, or a
list of such traces (one Session). snake_case keys (SDK ``model_dump``) are accepted too.

Provenance: AgentSight ``StrandsLangfuseSessionMapper`` (langfuse_mapper.py),
``LangchainLangfuseSessionMapper`` (langchain_langfuse_mapper.py) and
``LanggraphLangfuseSessionMapper`` (langgraph_langfuse_mapper.py); variant detection from
``SessionMapperFactory._detect_langfuse``.

- **Strands**: ``metadata.attributes["gen_ai.operation.name"]`` is ``chat`` / ``execute_tool`` /
  ``invoke_agent``; message content is a JSON string of Strands content blocks.
- **LangChain**: ``GENERATION`` -> LLM step; ``SPAN`` named ``tools`` -> one tool step from
  ``input.tool_call``; ``SPAN`` named ``LangGraph`` (top level, or with ``checkpoint_ns``) ->
  agent step, with its system prompt and tools taken from descendant generations.
- **LangGraph**: as LangChain, but a ``tools`` span may yield several tool steps (results from
  ``output.messages``, calls matched by id in the input), content may be a list of
  ``text`` / ``tool_use`` parts, and sub-agent spans under a tool are re-parented to that tool.

Deviations from AgentSight: Strands tool steps use ``gen_ai.tool.name`` / ``gen_ai.tool.call.id``
(AgentSight used the observation name ``"execute_tool <tool>"`` and the observation id, which
breaks the link to the LLM tool calls); system messages stay in LLM step inputs; tool status
``error`` and observation level ``ERROR`` become step errors; usage, model and cost are kept;
observations without ``startTime`` are kept (warning) instead of failing the sort.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import (
    Warnings,
    agent_key,
    arguments,
    as_dict,
    as_list,
    blocks_text,
    calls_from_message,
    dedupe_tools,
    finalize_trace,
    get,
    last_assistant,
    message,
    parse_json,
    records,
    reparent,
    short_hash,
    status_error,
    strands_blocks,
    strands_messages,
    text_of,
    to_ms,
    tool_def,
    usage,
)

STRANDS = "langfuse_strands"
LANGCHAIN = "langfuse_langchain"
LANGGRAPH = "langfuse_langgraph"
AGENT_NAMES = ("LangGraph",)
TOOLS_SPAN = "tools"


# --------------------------------------------------------------------------- observation access


class Obs:
    """Read-only view of an observation dict (camelCase or snake_case keys)."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        self.raw = raw
        self.id = str(raw.get("id"))
        self.type = str(raw.get("type") or "")
        self.name = str(raw.get("name") or "")
        parent = get(raw, "parentObservationId", "parent_observation_id")
        self.parent_id = str(parent) if parent is not None else None
        self.start_ms = to_ms(get(raw, "startTime", "start_time"))
        self.end_ms = to_ms(get(raw, "endTime", "end_time")) or self.start_ms
        self.input = parse_json(raw.get("input"), raw.get("input"))
        self.output = parse_json(raw.get("output"), raw.get("output"))
        self.metadata = as_dict(raw.get("metadata"))
        self.attributes = as_dict(self.metadata.get("attributes"))

    @property
    def error(self) -> str | None:
        if str(self.raw.get("level") or "").upper() == "ERROR":
            return str(get(self.raw, "statusMessage", "status_message", default="error"))
        return None

    def usage(self) -> Any:
        details = as_dict(get(self.raw, "usageDetails", "usage_details"))
        legacy = as_dict(self.raw.get("usage"))
        attrs = self.attributes
        cost = get(self.raw, "calculatedTotalCost", "calculated_total_cost", "totalCost")
        cost = cost or as_dict(get(self.raw, "costDetails", "cost_details")).get("total")
        return usage(
            get(details, "input", "prompt_tokens", "input_tokens", default=legacy.get("input"))
            or get(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
            get(
                details,
                "output",
                "completion_tokens",
                "output_tokens",
                default=legacy.get("output"),
            )
            or get(attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
            get(details, "cache_read_input_tokens", "input_cache_read")
            or attrs.get("gen_ai.usage.cache_read_input_tokens"),
            get(details, "cache_write_input_tokens", "input_cache_creation")
            or attrs.get("gen_ai.usage.cache_write_input_tokens"),
            cost,
        )

    def base(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "error": self.error,
        }


def _traces(data: Any) -> list[Mapping[str, Any]]:
    items = records(data)
    if isinstance(data, Mapping) and isinstance(data.get("data"), list):  # paginated list API
        items = data["data"]
    return [t for t in items if isinstance(t, Mapping) and isinstance(t.get("observations"), list)]


def variant(data: Any) -> str | None:
    """AgentSight ``_detect_langfuse``: which Langfuse variant ``data`` is (or ``None``)."""
    traces = _traces(data)
    if not traces:
        return None
    langgraph = tools = generation = False
    for trace in traces:
        for raw in trace["observations"]:
            if not isinstance(raw, Mapping):
                continue
            obs = Obs(raw)
            if "gen_ai.operation.name" in obs.attributes:
                return STRANDS
            generation |= obs.type == "GENERATION"
            langgraph |= obs.type == "SPAN" and obs.name in AGENT_NAMES
            tools |= obs.type == "SPAN" and obs.name == TOOLS_SPAN
    if langgraph:
        return LANGGRAPH if tools else LANGCHAIN
    return LANGCHAIN if generation else None


def _sorted(observations: list[Obs], trace_id: str, warnings: Warnings) -> list[Obs]:
    if any(o.start_ms is None for o in observations):
        warnings.add(f"trace {trace_id!r}: observations without startTime kept in input order")
        return observations
    return sorted(observations, key=lambda o: o.start_ms or 0.0)


# --------------------------------------------------------------------------- Strands variant


def _strands_input(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in as_list(value):
        if isinstance(item, Mapping) and item.get("role") in (
            "user",
            "assistant",
            "tool",
            "system",
        ):
            role = str(item["role"])
            if role == "system":
                out.append(message("system", item.get("content")))
            else:
                out.extend(strands_messages(role, item.get("content")))
    return out


def _strands_step(obs: Obs) -> Step | None:
    operation = obs.attributes.get("gen_ai.operation.name")
    if operation == "chat":
        output = as_dict(obs.output)
        outs = strands_messages("assistant", output["message"]) if "message" in output else []
        final = last_assistant(outs)
        return LlmStep(
            **obs.base(),
            usage=obs.usage(),
            model=obs.raw.get("model") or obs.attributes.get("gen_ai.request.model"),
            messages=tuple(_strands_input(obs.input)),
            response=(final or {}).get("content") or None,
            tool_calls=calls_from_message(final),
        )
    if operation == "execute_tool":
        first = as_list(obs.input)[:1]
        args = arguments(as_dict(first[0]).get("content")) if first else {}
        output = obs.output
        if isinstance(output, Mapping) and "message" in output:
            result = blocks_text(strands_blocks(output["message"])[:1])
        else:
            result = text_of(output)
        status = obs.attributes.get("tool.status", obs.attributes.get("gen_ai.tool.status"))
        error = status_error(status, result)
        name = obs.attributes.get("gen_ai.tool.name") or obs.name.removeprefix("execute_tool ")
        call_id = obs.attributes.get("gen_ai.tool.call.id") or obs.id
        return ToolStep(
            **{**obs.base(), "error": error or obs.error},
            call=ToolCall(name=name or "unknown_tool", arguments=args, id=call_id),
            result=ToolResult(content=result, error=error, call_id=call_id),
        )
    if operation == "invoke_agent":
        user = ""
        for item in as_list(obs.input):
            if isinstance(item, Mapping) and item.get("role") == "user":
                user = blocks_text(b for b in strands_blocks(item.get("content")) if "text" in b)
                if user:
                    break
        output = obs.output
        response = output.get("message") if isinstance(output, Mapping) else output
        if isinstance(response, list):
            response = blocks_text(strands_blocks(response))
        system = obs.attributes.get("system_prompt") or None
        tools = [
            tool_def(t if isinstance(t, str) else as_dict(t).get("name", "unknown"))
            for t in as_list(obs.attributes.get("gen_ai.agent.tools"))
            if isinstance(t, (str, Mapping))
        ]
        return AgentStep(
            **obs.base(),
            agent_id=agent_key(obs.attributes.get("gen_ai.agent.name"), system) or "agent_unknown",
            usage=obs.usage(),
            system_prompt=system,
            user_prompt=user or None,
            response=text_of(response) or None,
            available_tools=dedupe_tools(tools),
        )
    return None


def _map_strands(trace: Mapping[str, Any], warnings: Warnings) -> list[Step]:
    trace_id = str(trace.get("id"))
    observations = _sorted(
        [Obs(o) for o in trace["observations"] if isinstance(o, Mapping)], trace_id, warnings
    )
    steps: list[Step] = []
    for obs in observations:
        try:
            step = _strands_step(obs)
        except Exception as exc:  # noqa: BLE001 - one bad observation must not sink the trace
            warnings.add(f"observation {obs.id!r}: cannot map ({type(exc).__name__}: {exc})")
            continue
        if step is not None:
            steps.append(step)
    return reparent(steps, {o.id: o.parent_id for o in observations}, warnings)


# --------------------------------------------------------------------------- LangChain / LangGraph


def _lc_call(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "name": str(raw.get("name", "")),
        "arguments": arguments(raw.get("args")),
    }


def _content_parts(content: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "text":
            texts.append(text_of(item.get("text")))
        elif item.get("type") == "tool_use":
            calls.append(
                {
                    "id": item.get("id"),
                    "name": str(item.get("name", "")),
                    "arguments": arguments(item.get("input")),
                }
            )
    return "\n".join(t for t in texts if t), calls


def _lc_input(value: Any, graph: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in as_list(value):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or item.get("type") or "")
        content = item.get("content", "")
        if graph and isinstance(content, list):
            text, calls = _content_parts(content)
            out.append(message("user" if role == "user" else "assistant", text, tool_calls=calls))
            continue
        if role == "system":
            out.append(message("system", content))
        elif role in ("user", "human"):
            out.append(message("user", content))
        elif role in ("assistant", "ai"):
            calls = [
                _lc_call(c) for c in as_list(item.get("tool_calls")) if isinstance(c, Mapping)
            ]
            if content or calls:
                out.append(message("assistant", content, tool_calls=calls))
        elif role == "tool" and isinstance(content, str):  # dict content = a tool schema
            out.append(message("tool", content, tool_call_id=item.get("tool_call_id")))
    return out


def _lc_output(value: Any, graph: bool) -> dict[str, Any] | None:
    output = as_dict(value)
    content = output.get("content", "")
    raw_calls = [c for c in as_list(output.get("tool_calls")) if isinstance(c, Mapping)]
    calls = [_lc_call(c) for c in raw_calls]
    if graph and isinstance(content, list):
        text, part_calls = _content_parts(content)
        if "tool_calls" not in output:
            calls = part_calls
        content = text
    if not content and not calls:
        return None
    return message("assistant", content, tool_calls=calls)


def _is_agent(obs: Obs) -> bool:
    if obs.type != "SPAN" or obs.name not in AGENT_NAMES:
        return False
    return not obs.metadata.get("langgraph_step") or "checkpoint_ns" in obs.metadata


def _children(observations: Sequence[Obs]) -> dict[str, list[Obs]]:
    out: dict[str, list[Obs]] = {}
    for obs in observations:
        if obs.parent_id:
            out.setdefault(obs.parent_id, []).append(obs)
    return out


def _system_prompt(obs_id: str, children: Mapping[str, list[Obs]], seen: set[str]) -> str | None:
    for child in children.get(obs_id, []):
        if child.id in seen:
            continue
        seen.add(child.id)
        if child.type == "GENERATION" and isinstance(child.input, list) and child.input:
            first = as_dict(child.input[0])
            if first.get("type") == "system" or first.get("role") == "system":
                return text_of(first.get("content", ""))
        found = _system_prompt(child.id, children, seen)
        if found:
            return found
    return None


def _tools(
    obs_id: str, children: Mapping[str, list[Obs]], graph: bool, seen: set[str]
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for child in children.get(obs_id, []):
        if child.id in seen:
            continue
        seen.add(child.id)
        if child.type == "GENERATION" and child.input:
            for item in as_list(child.input):
                item = as_dict(item)
                role = (item.get("role") or item.get("type")) if graph else item.get("role")
                content = item.get("content")
                if role != "tool" or not isinstance(content, Mapping):
                    continue
                if "name" in content:
                    tools.append(
                        tool_def(
                            content["name"],
                            content.get("description"),
                            content.get("input_schema"),
                        )
                    )
                elif graph and isinstance(content.get("function"), Mapping):
                    fn = content["function"]
                    tools.append(
                        tool_def(fn.get("name", ""), fn.get("description"), fn.get("parameters"))
                    )
            break  # only the first generation
        if not _is_agent(child):
            found = _tools(child.id, children, graph, seen)
            if found:
                tools.extend(found)
                break
    return tools


def _user_prompt(value: Any) -> str:
    if isinstance(value, str):
        parsed = parse_json(value, literal=True)
        if isinstance(parsed, Mapping) and "query" in parsed:
            return text_of(parsed["query"])
        return value
    if isinstance(value, Mapping):
        if "messages" in value:
            for msg in as_list(value["messages"]):
                if isinstance(msg, Mapping) and (
                    msg.get("type") == "human" or msg.get("role") == "user"
                ):
                    return text_of(msg.get("content", ""))
                if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "user":
                    return text_of(msg[1])
            return ""
        if "query" in value:
            return text_of(value["query"])
        return ""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "user":
                return text_of(item[1])
    return ""


def _agent_response(value: Any) -> str:
    if isinstance(value, Mapping):
        if "messages" in value:
            for msg in reversed(as_list(value["messages"])):
                if isinstance(msg, Mapping) and msg.get("type") == "ai":
                    return text_of(msg.get("content", ""))
            return ""
        return text_of(value.get("content", ""))
    return text_of(value) if isinstance(value, str) else ""


def _tool_steps(obs: Obs, graph: bool, warnings: Warnings) -> list[ToolStep]:
    payload = as_dict(obs.input)
    output = as_dict(obs.output)
    out_messages = [m for m in as_list(output.get("messages")) if isinstance(m, Mapping)]
    if not graph:
        call = as_dict(payload.get("tool_call"))
        if not call:
            return []
        result = text_of(out_messages[0].get("content", "")) if out_messages else ""
        status = out_messages[0].get("status") if out_messages else None
        error = status_error(status, result)
        return [
            ToolStep(
                **{**obs.base(), "error": error or obs.error},
                call=ToolCall(
                    name=str(call.get("name", "unknown")),
                    arguments=arguments(call.get("args")),
                    id=call.get("id"),
                ),
                result=ToolResult(content=result, error=error, call_id=call.get("id")),
            )
        ]
    results: dict[str, Mapping[str, Any]] = {}
    for msg in out_messages:
        if msg.get("type") == "tool" and msg.get("tool_call_id"):
            results[str(msg["tool_call_id"])] = msg
    calls: dict[str, ToolCall] = {}
    direct = as_dict(payload.get("tool_call"))
    if direct.get("id") in results:
        calls[str(direct["id"])] = ToolCall(
            name=str(direct.get("name", "unknown")),
            arguments=arguments(direct.get("args")),
            id=direct["id"],
        )
    for msg in reversed(as_list(payload.get("messages"))):
        msg = as_dict(msg)
        if "tool_calls" in msg:
            for call in as_list(msg["tool_calls"]):
                call = as_dict(call)
                if call.get("id") in results and call["id"] not in calls:
                    calls[str(call["id"])] = ToolCall(
                        name=str(call.get("name", "unknown")),
                        arguments=arguments(call.get("args")),
                        id=call["id"],
                    )
        elif isinstance(msg.get("content"), list):
            for item in msg["content"]:
                item = as_dict(item)
                if (
                    item.get("type") == "tool_use"
                    and item.get("id") in results
                    and item["id"] not in calls
                ):
                    calls[str(item["id"])] = ToolCall(
                        name=str(item.get("name", "")),
                        arguments=arguments(item.get("input")),
                        id=item["id"],
                    )
    steps = []
    for call_id, result in results.items():
        call = calls.get(call_id)
        if call is None:
            warnings.add(f"tools span {obs.id!r}: no tool call found for result {call_id!r}")
            continue
        content = text_of(result.get("content", ""))
        error = status_error(result.get("status"), content)
        steps.append(
            ToolStep(
                **{
                    **obs.base(),
                    "id": hashlib.sha256(f"{obs.id}:{call_id}".encode()).hexdigest()[:16],
                    "error": error or obs.error,
                },
                call=call,
                result=ToolResult(content=content, error=error, call_id=call_id),
            )
        )
    return steps


def _map_langchain(trace: Mapping[str, Any], warnings: Warnings, graph: bool) -> list[Step]:
    trace_id = str(trace.get("id"))
    observations = _sorted(
        [Obs(o) for o in trace["observations"] if isinstance(o, Mapping)], trace_id, warnings
    )
    children = _children(observations)
    steps: list[Step] = []
    tool_step_ids: dict[str, list[ToolStep]] = {}
    for obs in observations:
        try:
            if obs.type == "GENERATION":
                final = _lc_output(obs.output, graph)
                steps.append(
                    LlmStep(
                        **obs.base(),
                        usage=obs.usage(),
                        model=obs.raw.get("model"),
                        messages=tuple(_lc_input(obs.input, graph)),
                        response=(final or {}).get("content") or None,
                        tool_calls=calls_from_message(final),
                    )
                )
            elif obs.type == "SPAN" and obs.name == TOOLS_SPAN:
                tool_steps = _tool_steps(obs, graph, warnings)
                if not tool_steps:
                    warnings.add(f"tools span {obs.id!r}: no tool call data")
                tool_step_ids[obs.id] = tool_steps
                steps.extend(tool_steps)
            elif _is_agent(obs):
                system = _system_prompt(obs.id, children, set())
                name = obs.name or "agent"
                fallback = f"{'agent' if not graph else name}_{obs.id[:8]}"
                agent_id = (
                    f"{'agent' if not graph else name}_{short_hash(system)}"
                    if system
                    else fallback
                )
                steps.append(
                    AgentStep(
                        **obs.base(),
                        agent_id=agent_id,
                        system_prompt=system,
                        user_prompt=_user_prompt(obs.input) or None,
                        response=_agent_response(obs.output) or None,
                        available_tools=dedupe_tools(_tools(obs.id, children, graph, set())),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - one bad observation must not sink the trace
            warnings.add(f"observation {obs.id!r}: cannot map ({type(exc).__name__}: {exc})")
    # Converted observation id -> step id (a tools span maps to its first tool step).
    converted: dict[str, str] = {s.id: s.id for s in steps}
    for obs_id, tool_steps in tool_step_ids.items():
        if tool_steps:
            converted[obs_id] = tool_steps[0].id
    if graph:  # intermediate children of a tools span (tool / sub-agent nodes) -> that tool step
        by_id = {o.id: o for o in observations}
        for obs in observations:
            parent = by_id.get(obs.parent_id or "")
            if parent is not None and parent.name == TOOLS_SPAN:
                for step in tool_step_ids.get(parent.id, []):
                    if step.call.name == obs.name:
                        converted[obs.id] = step.id
                        break
    raw_parents = {o.id: o.parent_id for o in observations}
    for steps_of_span in tool_step_ids.values():
        for step in steps_of_span:
            raw_parents.setdefault(step.id, step.parent_id)
    return reparent(steps, raw_parents, warnings, converted)


# --------------------------------------------------------------------------- adapters


class _LangfuseAdapter:
    name = ""
    confidence: dict[str, float] = {}

    def detect(self, data: Any) -> float:
        return self.confidence.get(variant(data) or "", 0.0)

    def _map(self, trace: Mapping[str, Any], warnings: Warnings) -> list[Step]:
        raise NotImplementedError

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        traces_in = _traces(data)
        warnings = Warnings()
        if not traces_in:
            warnings.add("no Langfuse traces with observations found")
        traces = []
        for trace in traces_in:
            trace_id = str(trace.get("id"))
            steps = self._map(trace, warnings)
            if not steps:
                warnings.add(f"trace {trace_id!r} produced no steps")
                continue
            traces.append(finalize_trace(trace_id, steps, warnings))
        sid = session_id
        if sid is None:
            first = traces_in[0] if traces_in else {}
            sid = str(get(first, "sessionId", "session_id", "id", default="session"))
        return Session(id=sid, traces=tuple(traces), warnings=warnings.as_tuple())


class LangfuseStrandsAdapter(_LangfuseAdapter):
    name = STRANDS
    confidence = {STRANDS: 0.95}

    def _map(self, trace: Mapping[str, Any], warnings: Warnings) -> list[Step]:
        return _map_strands(trace, warnings)


class LangfuseLangchainAdapter(_LangfuseAdapter):
    name = LANGCHAIN
    confidence = {LANGCHAIN: 0.8, LANGGRAPH: 0.6}

    def _map(self, trace: Mapping[str, Any], warnings: Warnings) -> list[Step]:
        return _map_langchain(trace, warnings, graph=False)


class LangfuseLanggraphAdapter(_LangfuseAdapter):
    name = LANGGRAPH
    confidence = {LANGGRAPH: 0.9, LANGCHAIN: 0.5}

    def _map(self, trace: Mapping[str, Any], warnings: Warnings) -> list[Step]:
        return _map_langchain(trace, warnings, graph=True)
