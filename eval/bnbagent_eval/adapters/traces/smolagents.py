"""SmolAgents (OpenInference ``openinference.instrumentation.smolagents``) traces -> Session.

Input: ``{"trace_id", "spans": [span, ...]}`` where each span has ``span_id``,
``parent_span_id``, ``span_name``, ``timestamp`` (ISO), ``duration`` (ISO-8601, ``PT1M2.5S``),
``span_attributes`` and nested ``child_spans`` (the GAIA / Patronus export used by AgentSight).

Provenance: AgentSight ``SmolAgentsSessionMapper`` (smolagents_mapper.py).
``openinference.span.kind`` ``AGENT`` -> agent step (``input.value.task``, ``output.value``,
tools from ``smolagents.tools_names`` + ``smolagents.managed_agents.N``, system prompt from the
first child LLM span), ``TOOL`` -> tool step, ``LLM`` -> LLM step (``llm.input_messages.N`` /
``llm.output_messages.N``). For ``CodeAgent`` (no structured tool calls), the code executions
are recovered from the ``tool-call`` / ``tool-response`` messages after the last assistant
message as ``python_interpreter`` tool steps. LLM and tool steps hang under their nearest agent.

Deviations from AgentSight: recovered code-tool steps get unique ids (AgentSight gave every
call of one LLM span the same id) and malformed tool-call text is skipped with a warning
instead of raising; system messages are kept in LLM inputs; token usage and model are kept.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import (
    Warnings,
    as_dict,
    as_list,
    dedupe_tools,
    finalize_trace,
    iso_duration_ms,
    message,
    parse_json,
    records,
    text_of,
    to_ms,
    tool_def,
    usage,
)


def _flatten(spans: list[Any], seen: set[int] | None = None) -> list[dict[str, Any]]:
    seen = set() if seen is None else seen
    out: list[dict[str, Any]] = []
    for span in spans:
        if not isinstance(span, Mapping) or id(span) in seen:
            continue
        seen.add(id(span))
        out.append({k: v for k, v in span.items() if k != "child_spans"})
        out.extend(_flatten(records(span.get("child_spans")), seen))
    return out


def _attrs(span: Mapping[str, Any]) -> dict[str, Any]:
    return as_dict(span.get("span_attributes"))


def _kind(span: Mapping[str, Any]) -> str | None:
    return _attrs(span).get("openinference.span.kind")


def _times(span: Mapping[str, Any]) -> tuple[float | None, float | None]:
    start = to_ms(span.get("timestamp"))
    duration = iso_duration_ms(span.get("duration"))
    end = start + duration if start is not None and duration is not None else start
    return start, (round(end, 3) if end is not None else None)


def _indexed(attrs: Mapping[str, Any], prefix: str) -> list[tuple[str, str]]:
    out = []
    index = 0
    while f"{prefix}.{index}.message.role" in attrs:
        out.append(
            (
                str(attrs[f"{prefix}.{index}.message.role"]),
                text_of(attrs.get(f"{prefix}.{index}.message.content", "")),
            )
        )
        index += 1
    return out


class SmolagentsAdapter:
    name = "smolagents"

    def detect(self, data: Any) -> float:
        if not isinstance(data, Mapping) or not isinstance(data.get("spans"), list):
            return 0.0
        spans = _flatten(data["spans"])
        if any("smolagents" in str(s.get("scope_name", "")) for s in spans) or any(
            k.startswith("smolagents.") for s in spans for k in _attrs(s)
        ):
            return 0.95
        if any(_kind(s) for s in spans) and any("span_attributes" in s for s in spans):
            return 0.4  # OpenInference spans in the same export shape
        return 0.0

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        warnings = Warnings()
        data = as_dict(data)
        spans = _flatten(as_list(data.get("spans")))
        trace_id = str(data.get("trace_id") or session_id or "trace")
        lookup = {str(s.get("span_id")): s for s in spans}
        parents = {
            str(s.get("span_id")): (str(s["parent_span_id"]) if s.get("parent_span_id") else None)
            for s in spans
        }

        def ancestors(span_id: str) -> list[str]:
            chain: list[str] = []
            current = parents.get(span_id)
            while current is not None and current not in chain:
                if current not in lookup:
                    warnings.add(f"orphan span {span_id!r}: parent {current!r} not found")
                    break
                chain.append(current)
                current = parents.get(current)
            else:
                if current is not None:
                    warnings.add(f"parent cycle detected at span {current!r}")
            return chain

        agent_ids = {str(s.get("span_id")) for s in spans if _kind(s) == "AGENT"}
        converted = {str(s.get("span_id")) for s in spans if _kind(s) in ("AGENT", "TOOL", "LLM")}

        def owner(span_id: str, *, agents_only: bool) -> str | None:
            chain = ancestors(span_id)
            for candidate in chain:
                if candidate in agent_ids:
                    return candidate
            if agents_only:
                return None
            return next((c for c in chain if c in converted), None)

        tool_info: dict[str, dict[str, Any]] = {}
        for span in spans:
            attrs = _attrs(span)
            if _kind(span) == "TOOL" and attrs.get("tool.name"):
                info = tool_info.setdefault(str(attrs["tool.name"]), {})
                if "tool.description" in attrs:
                    info["description"] = attrs["tool.description"]
                if attrs.get("tool.parameters"):
                    info["parameters"] = parse_json(attrs["tool.parameters"], {})

        steps: list[Step] = []
        llm_inputs: dict[str, list[dict[str, Any]]] = {}
        for span in spans:
            kind = _kind(span)
            span_id = str(span.get("span_id"))
            attrs = _attrs(span)
            start, end = _times(span)
            base = {"id": span_id, "start_ms": start, "end_ms": end}
            if kind == "AGENT":
                task = parse_json(attrs.get("input.value"), attrs.get("input.value"))
                user = task.get("task", "") if isinstance(task, Mapping) else text_of(task)
                tools = [
                    tool_def(
                        n,
                        tool_info.get(n, {}).get("description"),
                        tool_info.get(n, {}).get("parameters"),
                    )
                    for n in as_list(attrs.get("smolagents.tools_names"))
                    if isinstance(n, str)
                ]
                index = 0
                while f"smolagents.managed_agents.{index}.name" in attrs:
                    tools.append(
                        tool_def(
                            attrs[f"smolagents.managed_agents.{index}.name"],
                            attrs.get(f"smolagents.managed_agents.{index}.description"),
                        )
                    )
                    index += 1
                name = str(span.get("span_name") or "")
                steps.append(
                    AgentStep(
                        **base,
                        parent_id=owner(span_id, agents_only=True),
                        agent_id=name.replace(".run", "") if name else "unknown_agent",
                        error=_error(span),
                        usage=usage(
                            attrs.get("llm.token_count.prompt"),
                            attrs.get("llm.token_count.completion"),
                        ),
                        user_prompt=text_of(user) or None,
                        response=text_of(attrs.get("output.value")) or None,
                        available_tools=dedupe_tools(tools),
                    )
                )
            elif kind == "TOOL":
                raw = parse_json(attrs.get("input.value"), attrs.get("input.value"))
                params: dict[str, Any] = {}
                if isinstance(raw, Mapping):
                    if raw.get("args"):
                        params["args"] = raw["args"]
                    params.update(as_dict(raw.get("kwargs")))
                elif raw is not None:
                    params = {"raw_input": text_of(raw)}
                output = attrs.get("output.value") or span.get("status_message") or ""
                call = ToolCall(
                    name=str(attrs.get("tool.name", "unknown_tool")),
                    arguments=params,
                    id=f"tool_{span_id}",
                )
                steps.append(
                    ToolStep(
                        **base,
                        parent_id=owner(span_id, agents_only=False),
                        error=_error(span),
                        call=call,
                        result=ToolResult(
                            content=text_of(output), error=_error(span), call_id=call.id
                        ),
                    )
                )
            elif kind == "LLM":
                inputs = [
                    message(role if role in ("system", "user", "assistant") else "user", content)
                    for role, content in _indexed(attrs, "llm.input_messages")
                ]
                outputs = _indexed(attrs, "llm.output_messages")
                if not outputs:
                    continue
                llm_inputs[span_id] = inputs
                steps.append(
                    LlmStep(
                        **base,
                        parent_id=owner(span_id, agents_only=False),
                        error=_error(span),
                        usage=usage(
                            attrs.get("llm.token_count.prompt"),
                            attrs.get("llm.token_count.completion"),
                        ),
                        model=attrs.get("llm.model_name"),
                        messages=tuple(inputs),
                        response="\n".join(content for _, content in outputs) or None,
                    )
                )
                steps.extend(
                    self._code_tools(
                        span, span_id, base, owner(span_id, agents_only=False), lookup, warnings
                    )
                )

        steps = _attach_system_prompts(steps, llm_inputs)
        trace = finalize_trace(trace_id, steps, warnings)
        if not trace.steps:
            warnings.add("no AGENT / LLM / TOOL spans found")
        traces = (trace,) if trace.steps else ()
        return Session(id=session_id or trace_id, traces=traces, warnings=warnings.as_tuple())

    @staticmethod
    def _code_tools(
        span: Mapping[str, Any],
        span_id: str,
        base: Mapping[str, Any],
        parent: str | None,
        lookup: Mapping[str, Mapping[str, Any]],
        warnings: Warnings,
    ) -> list[ToolStep]:
        """CodeAgent tool executions, recovered from the LLM span's input messages."""
        attrs = _attrs(span)
        if "llm.output_messages.0.message.tool_calls.0.tool_call.id" in attrs:
            return []
        if parent and "ToolCallingAgent" in str(lookup.get(parent, {}).get("span_name", "")):
            return []
        messages = [
            m
            for m in as_list(as_dict(parse_json(attrs.get("input.value"))).get("messages"))
            if isinstance(m, Mapping)
        ]
        start = 0
        for index, msg in enumerate(messages):
            if msg.get("role") == "assistant":
                start = index
        calls: dict[str, dict[str, Any]] = {}
        for msg in messages[start:]:
            parts = as_list(msg.get("content"))
            text = text_of(as_dict(parts[0]).get("text", "")) if parts else ""
            try:
                if msg.get("role") == "tool-call":
                    payload = ast.literal_eval(text.replace("Calling tools:", "").strip())
                    first = payload[0]
                    calls[str(first["id"])] = {
                        "call": ToolCall(
                            name="python_interpreter",
                            arguments=as_dict(first.get("function")),
                            id=str(first["id"]),
                        ),
                        "response": "",
                    }
                elif msg.get("role") == "tool-response":
                    call_id = text.split("Call id: ")[1].split("\n")[0].strip()
                    calls[call_id]["response"] += "\n" + text
            except (ValueError, SyntaxError, KeyError, IndexError, TypeError) as exc:
                warnings.add(
                    f"LLM span {span_id!r}: unparseable {msg.get('role')} message "
                    f"({type(exc).__name__})"
                )
        return [
            ToolStep(
                **{**base, "id": f"tool_call_{span_id}_{n}"},
                parent_id=parent,
                call=data["call"],
                result=ToolResult(content=data["response"], call_id=call_id),
            )
            for n, (call_id, data) in enumerate(calls.items())
        ]


def _error(span: Mapping[str, Any]) -> str | None:
    if str(span.get("status_code", "")).lower() == "error":
        return text_of(span.get("status_message")) or "error"
    return None


def _attach_system_prompts(
    steps: list[Step], llm_inputs: Mapping[str, list[dict[str, Any]]]
) -> list[Step]:
    """Agent system prompt = first child LLM span whose first input message is a system one."""
    import dataclasses

    prompts: dict[str, str] = {}
    for step in steps:
        if isinstance(step, LlmStep) and step.parent_id and step.parent_id not in prompts:
            inputs = llm_inputs.get(step.id, [])
            if inputs and inputs[0]["role"] == "system":
                prompts[step.parent_id] = inputs[0]["content"]
    return [
        dataclasses.replace(s, system_prompt=prompts[s.id])
        if isinstance(s, AgentStep) and s.id in prompts
        else s
        for s in steps
    ]
