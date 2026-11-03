"""OpenTelemetry GenAI semantic conventions (legacy <=1.36 and >=1.37) -> Session.

Provenance: port of AgentSight ``StrandsInMemorySessionMapper`` (mappers/
strands_in_memory_session_mapper.py), which read in-memory ``ReadableSpan`` objects; this
adapter reads their JSON forms instead (OTLP/JSON exports or ``ReadableSpan.to_json()`` lists,
see ``_otel``).

Span kinds come from ``gen_ai.operation.name``: ``chat`` (also ``text_completion`` /
``generate_content``) -> ``LlmStep``, ``execute_tool`` -> ``ToolStep``, ``invoke_agent`` ->
``AgentStep``. The convention version is decided per span: ``gen_ai.provider.name`` or a
``gen_ai.client.inference.operation.details`` event means >=1.37 (messages as ``parts``),
otherwise legacy (``gen_ai.user.message`` / ``gen_ai.assistant.message`` /
``gen_ai.tool.message`` / ``gen_ai.choice`` events, Strands content blocks).

Deviations from AgentSight: the version is detected per span rather than from the first span;
legacy ``gen_ai.choice`` is the LLM *output* (AgentSight appended it to the inputs); chat spans
without input messages are kept; >=1.37 messages are also read from span attributes (where the
semconv puts them) and ``gen_ai.tool.call.arguments`` / ``.result``; parents are remapped to the
nearest mapped ancestor; usage, model, and error status are kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolResult, ToolStep
from ._common import (
    Warnings,
    arguments,
    as_list,
    blocks_text,
    calls_from_message,
    dedupe_tools,
    finalize_trace,
    genai_usage,
    get,
    last_assistant,
    message,
    parse_json,
    reparent,
    status_error,
    strands_blocks,
    strands_messages,
    text_of,
    tool_call_dict,
    tool_def,
)
from ._otel import OtelSpan, group_by_trace, is_event_record, parse_spans, session_id_of

LLM_OPERATIONS = ("chat", "text_completion", "generate_content")
OPERATIONS = (*LLM_OPERATIONS, "execute_tool", "invoke_agent")
DETAILS_EVENT = "gen_ai.client.inference.operation.details"
LEGACY_EVENTS = (
    "gen_ai.system.message",
    "gen_ai.user.message",
    "gen_ai.assistant.message",
    "gen_ai.tool.message",
    "gen_ai.choice",
)


def is_latest(span: OtelSpan) -> bool:
    """>=1.37 ("gen_ai_latest_experimental") vs legacy conventions, per span."""
    return "gen_ai.provider.name" in span.attributes or any(
        e.name == DETAILS_EVENT for e in span.events
    )


# --------------------------------------------------------------------------- >=1.37 parts


def _parts_messages(raw: Any) -> list[dict[str, Any]]:
    """``[{"role", "parts": [{"type": "text"|"tool_call"|"tool_call_response", ..}]}]``."""
    out: list[dict[str, Any]] = []
    for msg in as_list(raw):
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "user")
        texts: list[str] = []
        calls: list[ToolCall] = []
        results: list[dict[str, Any]] = []
        for part in as_list(msg.get("parts")):
            if not isinstance(part, Mapping):
                continue
            kind = part.get("type")
            if kind == "text":
                texts.append(text_of(part.get("content")))
            elif kind == "tool_call":
                calls.append(
                    ToolCall(
                        name=str(part.get("name", "")),
                        arguments=arguments(part.get("arguments")),
                        id=part.get("id"),
                    )
                )
            elif kind == "tool_call_response":
                results.append(
                    message(
                        "tool", _response_text(part.get("response")), tool_call_id=part.get("id")
                    )
                )
        if texts or calls:
            speaker = "assistant" if role == "assistant" or calls else role
            out.append(
                message(speaker, "\n".join(texts), tool_calls=[tool_call_dict(c) for c in calls])
            )
        out.extend(results)
    return out


def _response_text(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        return text_of(first.get("text", first) if isinstance(first, Mapping) else first)
    return text_of(response)


def _details(span: OtelSpan) -> tuple[Any, Any]:
    """Raw >=1.37 input / output messages (span attributes first, then details events)."""
    inputs = span.attributes.get("gen_ai.input.messages")
    outputs = span.attributes.get("gen_ai.output.messages")
    for event in span.events:
        if event.name == DETAILS_EVENT:
            inputs = event.attributes.get("gen_ai.input.messages", inputs)
            outputs = event.attributes.get("gen_ai.output.messages", outputs)
    return inputs, outputs


def _system_instructions(span: OtelSpan) -> str | None:
    raw = span.attributes.get("gen_ai.system_instructions")
    for event in span.events:
        if event.name == DETAILS_EVENT:
            raw = event.attributes.get("gen_ai.system_instructions", raw)
    if raw is None:
        return None
    parsed = parse_json(raw, raw)
    if isinstance(parsed, list):
        return (
            "\n".join(text_of(p.get("content")) for p in parsed if isinstance(p, Mapping)) or None
        )
    return text_of(parsed) or None


# --------------------------------------------------------------------------- legacy events


def _legacy_message(event_name: str, attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    role = event_name.split(".")[1]  # system / user / assistant / tool
    raw = parse_json(attrs.get("content"), attrs.get("content"))
    if isinstance(raw, Mapping):  # OpenAI-style message
        return [_openai_message(role, raw)]
    if isinstance(raw, list):  # Strands content blocks
        return strands_messages(role, raw)
    return [message(role, raw or "", tool_call_id=attrs.get("id"))] if raw else []


def _openai_message(role: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    calls = []
    for call in as_list(raw.get("tool_calls")):
        if isinstance(call, Mapping):
            function = call.get("function") if isinstance(call.get("function"), Mapping) else call
            calls.append(
                {
                    "id": call.get("id"),
                    "name": str(function.get("name", "")),
                    "arguments": arguments(function.get("arguments")),
                }
            )
    content = raw.get("content")
    if isinstance(content, list):
        content = blocks_text(strands_blocks(content))
    return message(
        str(raw.get("role") or role), content or "", tool_calls=calls, tool_call_id=raw.get("id")
    )


def _choice_messages(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = parse_json(attrs.get("message"), attrs.get("message"))
    if isinstance(raw, Mapping):
        return [_openai_message("assistant", raw)]
    return strands_messages("assistant", raw)


def _legacy_io(span: OtelSpan) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for event in span.events:
        if event.name == "gen_ai.choice":
            outputs.extend(_choice_messages(event.attributes))
        elif event.name in LEGACY_EVENTS:
            inputs.extend(_legacy_message(event.name, event.attributes))
    return inputs, outputs


# --------------------------------------------------------------------------- span mapping


def _llm_step(span: OtelSpan) -> LlmStep:
    if is_latest(span):
        raw_in, raw_out = _details(span)
        inputs, outputs = _parts_messages(raw_in), _parts_messages(raw_out)
        system = _system_instructions(span)
        if system:
            inputs.insert(0, message("system", system))
    else:
        inputs, outputs = _legacy_io(span)
    final = last_assistant(outputs)
    attrs = span.attributes
    return LlmStep(
        id=span.span_id,
        parent_id=span.parent_id,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        usage=genai_usage(attrs),
        error=span.error,
        model=get(attrs, "gen_ai.request.model", "gen_ai.response.model"),
        messages=tuple(inputs),
        response=(final.get("content") or None) if final else None,
        tool_calls=calls_from_message(final),
    )


def _tool_step(span: OtelSpan) -> ToolStep:
    attrs = span.attributes
    call_id = attrs.get("gen_ai.tool.call.id")
    status = get(attrs, "gen_ai.tool.status", "tool.status")
    args: dict[str, Any] = arguments(attrs.get("gen_ai.tool.call.arguments"))
    result: Any = attrs.get("gen_ai.tool.call.result")
    if is_latest(span):
        raw_in, raw_out = _details(span)
        for msg in as_list(raw_in)[:1]:
            parts = as_list(msg.get("parts")) if isinstance(msg, Mapping) else []
            if parts and isinstance(parts[0], Mapping) and parts[0].get("type") == "tool_call":
                args = arguments(parts[0].get("arguments"))
        for msg in as_list(raw_out)[:1]:
            parts = as_list(msg.get("parts")) if isinstance(msg, Mapping) else []
            first = parts[0] if parts and isinstance(parts[0], Mapping) else {}
            if first.get("type") == "tool_call_response":
                result = _response_text(first.get("response"))
    else:
        for event in span.events:
            if event.name == "gen_ai.tool.message":
                args = arguments(event.attributes.get("content"))
            elif event.name == "gen_ai.choice":
                blocks = strands_blocks(event.attributes.get("message"))
                result = blocks_text(blocks[:1]) if blocks else result
    error = status_error(status, result)
    if span.error and not error:
        error = span.error
    return ToolStep(
        id=span.span_id,
        parent_id=span.parent_id,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        error=error,
        call=ToolCall(name=str(attrs.get("gen_ai.tool.name", "")), arguments=args, id=call_id),
        result=ToolResult(content=text_of(result), error=error, call_id=call_id),
    )


def _agent_tools(attrs: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    definitions = as_list(attrs.get("gen_ai.tool.definitions"))
    if definitions:
        tools = []
        for item in definitions:
            if isinstance(item, Mapping):
                function = (
                    item.get("function") if isinstance(item.get("function"), Mapping) else item
                )
                schema = function.get("inputSchema", function.get("parameters"))
                if isinstance(schema, Mapping) and "json" in schema:
                    schema = schema["json"]
                tools.append(
                    tool_def(function.get("name", ""), function.get("description"), schema)
                )
        return dedupe_tools(tools)
    return dedupe_tools(
        tool_def(n) for n in as_list(attrs.get("gen_ai.agent.tools")) if isinstance(n, str) and n
    )


def _agent_step(span: OtelSpan) -> AgentStep:
    attrs = span.attributes
    user_prompt = response = None
    if is_latest(span):
        raw_in, raw_out = _details(span)
        user = [m for m in _parts_messages(raw_in) if m["role"] == "user"]
        final = last_assistant(_parts_messages(raw_out))
        user_prompt = user[0]["content"] if user else None
        response = final["content"] if final else None
    else:
        for event in span.events:
            if event.name == "gen_ai.user.message" and user_prompt is None:
                msgs = _legacy_message(event.name, event.attributes)
                user_prompt = msgs[0]["content"] if msgs else None
            elif event.name == "gen_ai.choice":
                raw = parse_json(event.attributes.get("message"), event.attributes.get("message"))
                response = (
                    blocks_text(strands_blocks(raw)) if isinstance(raw, list) else text_of(raw)
                )
    system = _system_instructions(span) or attrs.get("system_prompt")
    return AgentStep(
        id=span.span_id,
        parent_id=span.parent_id,
        agent_id=str(attrs.get("gen_ai.agent.name") or "") or None,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        usage=genai_usage(attrs),
        error=span.error,
        system_prompt=system or None,
        user_prompt=user_prompt or None,
        response=response or None,
        available_tools=_agent_tools(attrs),
    )


def map_span(span: OtelSpan) -> Step | None:
    operation = span.attributes.get("gen_ai.operation.name")
    if operation in LLM_OPERATIONS:
        return _llm_step(span)
    if operation == "execute_tool":
        return _tool_step(span)
    if operation == "invoke_agent":
        return _agent_step(span)
    return None


def _flatten(data: Any) -> list[Any]:
    return list(data) if isinstance(data, (list, tuple)) else [data]


class OtelGenAIAdapter:
    name = "otel_genai"

    def detect(self, data: Any) -> float:
        items = _flatten(data)
        if not items or not all(isinstance(i, Mapping) for i in items):
            return 0.0
        if any(is_event_record(i) and isinstance(i.get("body"), Mapping) for i in items):
            # CloudWatch ADOT runtime events: the agentcore_adot format.
            return 0.3
        try:
            spans, _ = parse_spans(data)
        except Exception:  # noqa: BLE001
            return 0.0
        operations = [s.attributes.get("gen_ai.operation.name") for s in spans]
        if not any(op in OPERATIONS for op in operations):
            return 0.0
        has_content = any(
            e.name == DETAILS_EVENT or e.name in LEGACY_EVENTS for s in spans for e in s.events
        ) or any("gen_ai.input.messages" in s.attributes for s in spans)
        return 0.9 if has_content else 0.6

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        spans, skipped = parse_spans(data)
        warnings = Warnings()
        if skipped:
            warnings.add(f"{skipped} record(s) without trace/span ids skipped")
        traces = []
        for trace_id, group in group_by_trace(spans).items():
            raw_parents = {s.span_id: s.parent_id for s in group}
            steps: list[Step] = []
            for span in group:
                try:
                    step = map_span(span)
                except Exception as exc:  # noqa: BLE001 - one bad span must not sink the trace
                    warnings.add(
                        f"span {span.span_id!r}: cannot map ({type(exc).__name__}: {exc})"
                    )
                    continue
                if step is not None:
                    steps.append(step)
            steps = reparent(steps, raw_parents, warnings)
            if steps:
                traces.append(finalize_trace(trace_id, steps, warnings))
        if not traces:
            warnings.add("no GenAI spans (gen_ai.operation.name) found")
        sid = session_id or session_id_of(spans) or (traces[0].id if traces else "session")
        return Session(id=sid, traces=tuple(traces), warnings=warnings.as_tuple())
