"""AgentCore ADOT (CloudWatch ``aws/spans`` + runtime-log JSON) -> Session.

Provenance: port of AgentSight ``mappers/agentcore`` (``OtelTraceLogsParser`` +
``OtelSpanMapper``) and its three scope mappers:

- ``strands.telemetry.tracer`` (``StrandsTelemetryTracerMapper``): ``gen_ai.operation.name``
  ``chat`` / ``execute_tool`` / ``invoke_agent``; Strands content blocks in the event body;
- ``opentelemetry.instrumentation.langchain`` (OpenLLMetry,
  ``OpenTelemetryInstrumentationLangchainMapper``): ``llm.request.type == chat``,
  ``traceloop.span.kind`` ``tool`` / ``workflow``; tools from ``llm.request.functions.*``;
- ``openinference.instrumentation.langchain`` (``OpenInferenceTelemetryMapper``):
  ``openinference.span.kind`` ``LLM`` / ``TOOL``; one agent span per trace (``CHAIN`` named
  ``LangGraph``, else ``AGENT`` / ``CHAIN model`` with ``langgraph_step >= 2``).

Input: a list of CloudWatch records: spans (``traceId``, ``spanId``, ``scope.name``,
``attributes``) and span-event log records whose ``attributes["event.name"]`` equals the
scope name and whose ``body`` is ``{"input": {"messages": [..]}, "output": {"messages":
[..]}}``. Spans from other scopes (botocore, starlette, ...) are skipped; mapped steps are
re-parented to the nearest mapped ancestor. Agent ids are ``<name>_<sha256(system_prompt)[:8]>``.

Deviations from AgentSight: per-trace tools and system prompts (LangChain scopes) are collected
in a first pass, so the result does not depend on span order; spans missing required fields
are dropped with a warning instead of aborting the whole session; traces without an agent
span are kept (with a warning) instead of dropped; OpenLLMetry input roles come from
``gen_ai.prompt.N.role`` (AgentSight treated every input as a user message); missing timestamps
are tolerated; usage, model and error status are kept.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
    tool_def,
    usage,
)
from ._otel import OtelSpan, group_by_trace, is_event_record, parse_spans, session_id_of

STRANDS = "strands.telemetry.tracer"
OPENLLMETRY_LANGCHAIN = "opentelemetry.instrumentation.langchain"
OPENINFERENCE_LANGCHAIN = "openinference.instrumentation.langchain"
SCOPES = (STRANDS, OPENLLMETRY_LANGCHAIN, OPENINFERENCE_LANGCHAIN)


class SkipSpan(Exception):
    """The span lacks what its kind requires; it is dropped with a warning."""


def _body(span: OtelSpan) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Input / output messages of the scope's runtime event (``SpanEventMessageBody``)."""
    for event in span.events:
        if event.name == span.scope or event.attributes.get("event.name") == span.scope:
            body = as_dict(event.body)
            inputs = as_list(as_dict(body.get("input")).get("messages"))
            outputs = as_list(as_dict(body.get("output")).get("messages"))
            inputs = [m for m in inputs if isinstance(m, Mapping)]
            outputs = [m for m in outputs if isinstance(m, Mapping)]
            if not inputs or not outputs:
                raise SkipSpan("event body has no input/output messages")
            return inputs, outputs
    raise SkipSpan("no runtime event found for span")


def _base(span: OtelSpan) -> dict[str, Any]:
    return {
        "id": span.span_id,
        "parent_id": span.parent_id,
        "start_ms": span.start_ms,
        "end_ms": span.end_ms,
        "error": span.error,
    }


# --------------------------------------------------------------------------- strands scope


class StrandsScope:
    scope = STRANDS

    def prepare(self, spans: list[OtelSpan]) -> None:
        return None

    def kind(self, span: OtelSpan) -> str | None:
        operation = span.attributes.get("gen_ai.operation.name")
        return {"chat": "llm", "execute_tool": "tool", "invoke_agent": "agent"}.get(operation)

    @staticmethod
    def _content(msg: Mapping[str, Any], *keys: str) -> Any:
        content = msg.get("content")
        content = parse_json(content, content) if isinstance(content, str) else content
        if isinstance(content, Mapping):
            for key in keys:
                if key in content:
                    return content[key]
        return None

    def llm(self, span: OtelSpan) -> LlmStep:
        inputs, outputs = _body(span)
        messages: list[dict[str, Any]] = []
        for msg in inputs:
            role = str(msg.get("role", ""))
            if role == "system" and isinstance(msg.get("content"), str):
                messages.append(message("system", msg["content"]))
                continue
            keys = ("message", "content") if role == "assistant" else ("content",)
            blocks = self._content(msg, *keys)
            if blocks is not None:
                speaker = "assistant" if role == "assistant" else "user"
                messages.extend(strands_messages(speaker, blocks))
        output = self._content(outputs[-1], "message", "content")
        if output is None:
            raise SkipSpan("no model output")
        out_blocks = strands_blocks(output)
        final = last_assistant(strands_messages("assistant", out_blocks))
        if not [m for m in messages if m["role"] != "system"]:
            raise SkipSpan("no model input")
        attrs = span.attributes
        return LlmStep(
            **_base(span),
            usage=genai_usage(attrs),
            model=get(attrs, "gen_ai.request.model", "gen_ai.response.model"),
            messages=tuple(messages),
            response=blocks_text(out_blocks) or None,
            tool_calls=calls_from_message(final),
        )

    def tool(self, span: OtelSpan) -> ToolStep:
        inputs, outputs = _body(span)
        attrs = span.attributes
        name = attrs.get("gen_ai.tool.name")
        call_id = attrs.get("gen_ai.tool.call.id") or None
        status = get(attrs, "tool.status", "gen_ai.tool.status", default="success")
        params = None
        for msg in inputs:
            raw = self._content(msg, "content")
            parsed = parse_json(raw, raw, literal=True)
            if isinstance(parsed, Mapping):
                params = dict(parsed)
                break
        result = None
        for msg in outputs:
            raw = self._content(msg, "message")
            if raw is not None:
                blocks = strands_blocks(raw)
                if blocks:
                    first = blocks[0]
                    result = text_of(first.get("text", first.get("json", "")))
                break
        if name is None or params is None or result is None:
            raise SkipSpan("tool span without name, parameters or output")
        error = status_error(status, result)
        return ToolStep(
            **{**_base(span), "error": error or span.error},
            call=ToolCall(name=str(name), arguments=params, id=call_id),
            result=ToolResult(content=result, error=error, call_id=call_id),
        )

    def agent(self, span: OtelSpan) -> AgentStep:
        inputs, outputs = _body(span)
        texts = [b for b in strands_blocks(self._content(inputs[-1], "content")) if "text" in b]
        user = text_of(texts[0]["text"]) if texts else None
        response = self._content(outputs[-1], "message")
        if user is None or response is None:
            raise SkipSpan("agent span without user prompt or response")
        if not isinstance(response, str):
            response = blocks_text(strands_blocks(response))
        system = None
        for msg in inputs:
            if msg.get("role") == "system":
                system = msg["content"] if isinstance(msg.get("content"), str) else None
        attrs = span.attributes
        return AgentStep(
            **_base(span),
            agent_id=agent_key(attrs.get("gen_ai.agent.name", ""), system),
            usage=genai_usage(attrs),
            system_prompt=system,
            user_prompt=user,
            response=response,
            available_tools=self._tools(attrs),
        )

    @staticmethod
    def _tools(attrs: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        if "gen_ai.tool.definitions" in attrs:
            return dedupe_tools(
                tool_def(
                    t.get("name", ""),
                    t.get("description"),
                    as_dict(t.get("inputSchema")).get("json"),
                )
                for t in as_list(attrs["gen_ai.tool.definitions"])
                if isinstance(t, Mapping)
            )
        return dedupe_tools(
            tool_def(n)
            for n in as_list(attrs.get("gen_ai.agent.tools"))
            if isinstance(n, str) and n
        )


# --------------------------------------------------------------------------- OpenLLMetry scope


class OpenLLMetryLangchainScope:
    scope = OPENLLMETRY_LANGCHAIN

    def __init__(self) -> None:
        self.tools: dict[str, dict[str, dict[str, Any]]] = {}
        self.system: dict[str, str] = {}

    def kind(self, span: OtelSpan) -> str | None:
        attrs = span.attributes
        if attrs.get("llm.request.type") == "chat":
            return "llm"
        return {"tool": "tool", "workflow": "agent"}.get(attrs.get("traceloop.span.kind"))

    def prepare(self, spans: list[OtelSpan]) -> None:
        for span in spans:
            if self.kind(span) != "llm":
                continue
            tools = self.tools.setdefault(span.trace_id, {})
            tools.update(self._functions(span.attributes))
            try:
                inputs, _ = _body(span)
            except SkipSpan:
                continue
            for index, msg in enumerate(inputs):
                if span.attributes.get(f"gen_ai.prompt.{index}.role") == "system":
                    self.system.setdefault(span.trace_id, text_of(msg.get("content")))
                    break

    @staticmethod
    def _functions(attrs: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        indices = sorted(
            {
                key.split(".")[3]
                for key in attrs
                if key.startswith("llm.request.functions.") and key.endswith(".name")
            }
        )
        for index in indices:
            name = attrs.get(f"llm.request.functions.{index}.name")
            if name:
                params = parse_json(attrs.get(f"llm.request.functions.{index}.parameters"))
                out[str(name)] = tool_def(
                    name, attrs.get(f"llm.request.functions.{index}.description"), params
                )
        return out

    @staticmethod
    def _completion_calls(index: int, attrs: Mapping[str, Any]) -> list[ToolCall]:
        calls = []
        j = 0
        while attrs.get(f"gen_ai.completion.{index}.tool_calls.{j}.name"):
            prefix = f"gen_ai.completion.{index}.tool_calls.{j}"
            calls.append(
                ToolCall(
                    name=str(attrs[f"{prefix}.name"]),
                    arguments=arguments(attrs.get(f"{prefix}.arguments")),
                    id=attrs.get(f"{prefix}.id") or None,
                )
            )
            j += 1
        return calls

    def llm(self, span: OtelSpan) -> LlmStep:
        inputs, outputs = _body(span)
        attrs = span.attributes
        messages = []
        for index, msg in enumerate(inputs):
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            role = str(attrs.get(f"gen_ai.prompt.{index}.role") or "user")
            call_id = attrs.get(f"gen_ai.prompt.{index}.tool_call_id") if role == "tool" else None
            messages.append(message(role, content, tool_call_id=call_id))
        response_parts: list[str] = []
        calls: list[ToolCall] = []
        for index, msg in enumerate(outputs):
            if isinstance(msg.get("content"), str):
                response_parts.append(msg["content"])
                calls.extend(self._completion_calls(index, attrs))
        if not messages or not response_parts:
            raise SkipSpan("chat span without model input or output")
        return LlmStep(
            **_base(span),
            usage=genai_usage(attrs)
            or usage(
                attrs.get("llm.usage.prompt_tokens"), attrs.get("llm.usage.completion_tokens")
            ),
            model=get(attrs, "gen_ai.request.model", "gen_ai.response.model"),
            messages=tuple(messages),
            response="\n".join(p for p in response_parts if p) or None,
            tool_calls=tuple(calls),
        )

    def tool(self, span: OtelSpan) -> ToolStep:
        inputs, outputs = _body(span)
        name = span.attributes.get("traceloop.entity.name")
        params = None
        parsed_in = as_dict(parse_json(inputs[-1].get("content"), literal=True))
        if "input_str" in parsed_in:
            candidate = parse_json(parsed_in["input_str"], literal=True)
            if isinstance(candidate, Mapping):
                params = dict(candidate)
        if params is None and isinstance(parsed_in.get("inputs"), Mapping):
            params = dict(parsed_in["inputs"])
        kwargs = as_dict(
            as_dict(as_dict(parse_json(outputs[-1].get("content"))).get("output")).get("kwargs")
        )
        result = kwargs.get("content")
        call_id = kwargs.get("tool_call_id") or None
        status = kwargs.get("status")
        if name is None or params is None or result is None:
            raise SkipSpan("tool span without name, parameters or output")
        error = status_error(status, result)
        return ToolStep(
            **{**_base(span), "error": error or span.error},
            call=ToolCall(name=str(name), arguments=params, id=call_id),
            result=ToolResult(content=text_of(result), error=error, call_id=call_id),
        )

    @staticmethod
    def _state_text(msg: Mapping[str, Any], key: str) -> str | None:
        state = as_dict(as_dict(parse_json(msg.get("content"))).get(key))
        messages = state.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        last = messages[-1]
        if isinstance(last, Mapping):
            kwargs = last.get("kwargs")
            if isinstance(kwargs, Mapping) and "content" in kwargs:
                return text_of(kwargs["content"])
            if "content" in last:
                return text_of(last["content"])
        elif isinstance(last, list) and last:
            return text_of(last[-1])
        elif isinstance(last, str):
            return last
        return None

    def agent(self, span: OtelSpan) -> AgentStep:
        inputs, outputs = _body(span)
        user = self._state_text(inputs[-1], "inputs")
        response = self._state_text(outputs[-1], "outputs")
        if user is None or response is None:
            raise SkipSpan("workflow span without user prompt or response")
        system = self.system.get(span.trace_id)
        tools = self.tools.get(span.trace_id, {})
        return AgentStep(
            **_base(span),
            agent_id=agent_key(span.attributes.get("traceloop.workflow.name", ""), system),
            system_prompt=system,
            user_prompt=user,
            response=response,
            available_tools=tuple(tools[name] for name in sorted(tools)),
        )


# --------------------------------------------------------------------------- OpenInference scope


def _lc_messages(raw: Any) -> list[Any]:
    messages = as_dict(parse_json(raw)).get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], list):
        messages = messages[0]
    return messages if isinstance(messages, list) else []


class OpenInferenceLangchainScope:
    scope = OPENINFERENCE_LANGCHAIN

    def __init__(self) -> None:
        self.tools: dict[str, dict[str, dict[str, Any]]] = {}
        self.system: dict[str, str] = {}
        self.agent_span: dict[str, str] = {}

    @staticmethod
    def _step(span: OtelSpan) -> int:
        metadata = as_dict(span.attributes.get("metadata"))
        try:
            return int(metadata.get("langgraph_step", 0))
        except (TypeError, ValueError):
            return 0

    def prepare(self, spans: list[OtelSpan]) -> None:
        for span in spans:
            kind = span.attributes.get("openinference.span.kind")
            if kind == "LLM":
                tools = self.tools.setdefault(span.trace_id, {})
                for key, value in span.attributes.items():
                    if key.startswith("llm.tools.") and key.endswith(".tool.json_schema"):
                        info = as_dict(value)
                        if info.get("name"):
                            tools[str(info["name"])] = tool_def(
                                info["name"], info.get("description"), info.get("input_schema")
                            )
                try:
                    inputs, _ = _body(span)
                except SkipSpan:
                    continue
                for msg in inputs:
                    if msg.get("role") != "user":
                        continue
                    for item in _lc_messages(msg.get("content")):
                        kwargs = as_dict(item).get("kwargs", {})
                        if as_dict(kwargs).get("type") == "system":
                            self.system[span.trace_id] = text_of(kwargs.get("content"))
        # One agent span per trace: CHAIN "LangGraph" first, else AGENT / CHAIN "model" step>=2.
        for span in spans:
            kind = span.attributes.get("openinference.span.kind")
            if kind == "CHAIN" and span.name == "LangGraph":
                self.agent_span.setdefault(span.trace_id, span.span_id)
        for span in spans:
            kind = span.attributes.get("openinference.span.kind")
            if (kind == "AGENT" or (kind == "CHAIN" and span.name == "model")) and self._step(
                span
            ) >= 2:
                self.agent_span.setdefault(span.trace_id, span.span_id)

    def kind(self, span: OtelSpan) -> str | None:
        kind = span.attributes.get("openinference.span.kind")
        if kind == "LLM":
            return "llm"
        if kind == "TOOL":
            return "tool"
        if self.agent_span.get(span.trace_id) == span.span_id:
            return "agent"
        return None

    def llm(self, span: OtelSpan) -> LlmStep:
        inputs, outputs = _body(span)
        messages: list[dict[str, Any]] = []
        structured = None
        for msg in inputs:
            if msg.get("role") == "user":
                content = parse_json(msg.get("content"))
                if isinstance(content, Mapping) and "messages" in content:
                    structured = _lc_messages(msg.get("content"))
                    break
        if structured:
            for item in structured:
                kwargs = as_dict(as_dict(item).get("kwargs"))
                kind = kwargs.get("type")
                if kind == "human":
                    messages.append(message("user", kwargs.get("content", "")))
                elif kind == "system":
                    messages.append(message("system", kwargs.get("content", "")))
                elif kind == "ai":
                    calls = [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "arguments": arguments(c.get("args")),
                        }
                        for c in as_list(kwargs.get("tool_calls"))
                        if isinstance(c, Mapping)
                    ]
                    messages.append(
                        message("assistant", kwargs.get("content", ""), tool_calls=calls)
                    )
                elif kind == "tool":
                    messages.append(
                        message(
                            "tool",
                            kwargs.get("content", ""),
                            tool_call_id=kwargs.get("tool_call_id"),
                        )
                    )
        else:
            for msg in inputs:
                if msg.get("role") == "user":
                    messages.append(message("user", msg.get("content")))
                    break
        if not [m for m in messages if m["role"] != "system"]:
            raise SkipSpan("LLM span without model input")
        response = None
        calls: list[ToolCall] = []
        for msg in outputs:
            if msg.get("role") != "assistant":
                continue
            generated = as_dict(parse_json(msg.get("content"), msg.get("content")))
            generations = generated.get("generations")
            if generations:
                first = generations[0][0]
                response = text_of(first.get("text"))
                for call in as_list(
                    as_dict(as_dict(first.get("message")).get("kwargs")).get("tool_calls")
                ):
                    calls.append(
                        ToolCall(
                            name=str(call["name"]),
                            arguments=arguments(call.get("args")),
                            id=call.get("id"),
                        )
                    )
                break
        if response is None:
            texts = [text_of(m.get("content")) for m in outputs if m.get("role") == "assistant"]
            if not texts:
                raise SkipSpan("LLM span without model output")
            response = "\n".join(texts)
        attrs = span.attributes
        return LlmStep(
            **_base(span),
            usage=usage(
                attrs.get("llm.token_count.prompt"), attrs.get("llm.token_count.completion")
            ),
            model=attrs.get("llm.model_name"),
            messages=tuple(messages),
            response=response or None,
            tool_calls=tuple(calls),
        )

    def tool(self, span: OtelSpan) -> ToolStep:
        inputs, outputs = _body(span)
        params = None
        if inputs[-1].get("role") == "user":
            content = inputs[-1].get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            parsed = parse_json(text) or parse_json(text.replace("'", '"'))
            if isinstance(parsed, Mapping):
                params = dict(parsed)
        data: dict[str, Any] = {}
        if outputs[-1].get("role") == "assistant":
            data = as_dict(parse_json(outputs[-1].get("content"), outputs[-1].get("content")))
        name = data.get("name") or span.attributes.get("tool.name")
        result = data.get("content")
        call_id = data.get("tool_call_id") or None
        status = data.get("status")
        if name is None or params is None or result is None:
            raise SkipSpan("tool span without name, parameters or output")
        error = status_error(status, result)
        return ToolStep(
            **{**_base(span), "error": error or span.error},
            call=ToolCall(name=str(name), arguments=params, id=call_id),
            result=ToolResult(content=text_of(result), error=error, call_id=call_id),
        )

    def agent(self, span: OtelSpan) -> AgentStep:
        inputs, outputs = _body(span)
        user = ""
        for msg in inputs:
            if msg.get("role") != "user":
                continue
            parsed = parse_json(msg.get("content"))
            if not isinstance(parsed, Mapping):
                user = text_of(msg.get("content"))
                break
            for item in as_list(parsed.get("messages")):
                item = as_dict(item)
                kwargs = as_dict(item.get("kwargs"))
                if kwargs.get("type") == "human" or item.get("type") == "human":
                    user = text_of((kwargs or item).get("content", ""))
                    break
                if item.get("role") == "user":
                    user = text_of(item.get("content", ""))
        response = ""
        for msg in outputs:
            if msg.get("role") != "assistant":
                continue
            parsed = as_dict(parse_json(msg.get("content"), msg.get("content")))
            if isinstance(parsed.get("messages"), list):
                for item in reversed(parsed["messages"]):
                    item = as_dict(item)
                    holder = item if "content" in item else as_dict(item.get("kwargs"))
                    if item.get("type") == "ai" or holder.get("type") == "ai":
                        response = text_of(holder.get("content", ""))
                        break
                if response:
                    break
            elif parsed.get("generations"):
                response = text_of(parsed["generations"][0][0].get("text"))
                break
        if not user or not response:
            raise SkipSpan("agent span without user prompt or response")
        system = self.system.get(span.trace_id)
        tools = self.tools.get(span.trace_id, {})
        return AgentStep(
            **_base(span),
            agent_id=agent_key(span.name, system),
            system_prompt=system,
            user_prompt=user,
            response=response,
            available_tools=tuple(tools[name] for name in sorted(tools)),
        )


# --------------------------------------------------------------------------- adapter


def _scope_mappers() -> dict[str, Any]:
    return {
        STRANDS: StrandsScope(),
        OPENLLMETRY_LANGCHAIN: OpenLLMetryLangchainScope(),
        OPENINFERENCE_LANGCHAIN: OpenInferenceLangchainScope(),
    }


class AgentCoreAdotAdapter:
    name = "agentcore_adot"

    def detect(self, data: Any) -> float:
        items = data if isinstance(data, (list, tuple)) else None
        if not items or not all(isinstance(i, Mapping) for i in items):
            return 0.0
        has_events = any(is_event_record(i) and isinstance(i.get("body"), Mapping) for i in items)
        scopes = {
            str(as_dict(i.get("scope")).get("name"))
            for i in items
            if not is_event_record(i) and ("traceId" in i or "spanId" in i)
        }
        if not scopes & set(SCOPES):
            return 0.0
        return 0.95 if has_events else 0.5

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        spans, skipped = parse_spans(data)
        warnings = Warnings()
        if skipped:
            warnings.add(f"{skipped} record(s) without a matching span skipped")
        mappers = _scope_mappers()
        supported = 0
        traces = []
        for trace_id, group in group_by_trace(spans).items():
            for mapper in mappers.values():
                mapper.prepare([s for s in group if s.scope == mapper.scope])
            steps: list[Step] = []
            for span in group:
                mapper = mappers.get(span.scope)
                if mapper is None:
                    continue
                supported += 1
                kind = mapper.kind(span)
                if kind is None:
                    continue
                try:
                    if kind == "llm":
                        step: Step = mapper.llm(span)
                    elif kind == "tool":
                        step = mapper.tool(span)
                    else:
                        step = mapper.agent(span)
                except SkipSpan as exc:
                    warnings.add(f"{kind} span {span.span_id!r} ({span.scope}) dropped: {exc}")
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad span must not sink the trace
                    warnings.add(
                        f"{kind} span {span.span_id!r} ({span.scope}) dropped: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                steps.append(step)
            if not steps:
                continue
            if not any(isinstance(s, AgentStep) for s in steps):
                warnings.add(f"trace {trace_id!r} has no agent invocation span")
            steps = reparent(steps, {s.span_id: s.parent_id for s in group}, warnings)
            traces.append(finalize_trace(trace_id, steps, warnings))
        if not supported:
            warnings.add(f"no spans from supported scopes ({', '.join(SCOPES)})")
        elif not traces:
            warnings.add("no model, tool or agent spans could be mapped")
        sid = session_id or session_id_of(spans) or (traces[0].id if traces else "session")
        return Session(id=sid, traces=tuple(traces), warnings=warnings.as_tuple())


__all__ = ["AgentCoreAdotAdapter", "SCOPES"]
