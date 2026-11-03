"""Bedrock AgentCore Evaluations bridge: Session -> session spans, ``evaluate()`` and mapping.

Port of ETK ``evaluator_builtin.scorers.agentcore`` (``AGENTCORE_SCORES``, ``SpanFilter``,
``MAX_TOOL_SPANS``, ``_build_eval_params``). ETK forwards the raw CloudWatch documents captured
by its AgentCore ``app_invoke``; we rebuild that payload from our normalized :class:`Session`
(the reverse of ``adapters.traces.agentcore_adot``, Strands scope), so any trajectory source
can be scored by AgentCore.

Payload (``evaluationInput.sessionSpans``): spans and their log events as separate top-level
items, as the API requires (ETK ``CloudWatchSpanFetcher._merge``):

- span: ``traceId`` (32 hex), ``spanId`` (16 hex), ``parentSpanId``, ``name``
  (``invoke_agent <agent>`` / ``chat`` / ``execute_tool <tool>``), ``kind`` ``INTERNAL``,
  ``startTimeUnixNano`` / ``endTimeUnixNano`` / ``durationNano`` (integers),
  ``scope`` ``strands.telemetry.tracer``, ``attributes`` (``session.id``,
  ``gen_ai.operation.name``, ``gen_ai.*``), ``status.code`` ``OK`` / ``ERROR``;
- event: ``traceId``, ``spanId`` (of its span), ``timeUnixNano``, ``observedTimeUnixNano``,
  ``attributes`` (``event.name`` = scope, ``session.id``), ``body`` ``{"input": {"messages"},
  "output": {"messages"}}`` with Strands content blocks.

IDs that are already hex of the right length are kept (round trip with the ADOT adapter);
others are derived deterministically (sha256 of session / trace / step id). Missing timestamps
are synthesized deterministically. Delegation, handoff and tx steps have no Strands span: their
children are re-parented to the nearest emitted ancestor. A trace without an agent step gets a
synthesized ``invoke_agent`` root span (user prompt / final response from its LLM steps).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..._optional import require
from ...adapters.traces._common import short_hash, text_of
from ...types.case import EvalCase
from ...types.result import ErrorInfo
from ...types.trajectory import AgentStep, LlmStep, Session, Step, ToolCall, ToolStep, Trace
from ..base import Kind, MethodSpec, MetricContext, Outcome

logger = logging.getLogger(__name__)

SCOPE = "strands.telemetry.tracer"
MAX_TOOL_SPANS = 10  # ETK: tool-level evaluators accept at most 10 span ids
DEFAULT_REGION = "us-east-1"  # ETK AgentCoreParams default
LEVELS = ("session", "trace", "tool")

# ETK AGENTCORE_SCORES: built-in evaluator id -> evaluation level.
BUILTINS: dict[str, str] = {
    "Builtin.GoalSuccessRate": "session",
    "Builtin.Coherence": "trace",
    "Builtin.Conciseness": "trace",
    # Builtin.ContextRelevance is excluded, as in ETK: the AWS API errors on it.
    "Builtin.Correctness": "trace",
    "Builtin.Faithfulness": "trace",
    "Builtin.Harmfulness": "trace",
    "Builtin.Helpfulness": "trace",
    "Builtin.InstructionFollowing": "trace",
    "Builtin.Refusal": "trace",
    "Builtin.ResponseRelevance": "trace",
    "Builtin.Stereotyping": "trace",
    "Builtin.ToolParameterAccuracy": "tool",
    "Builtin.ToolSelectionAccuracy": "tool",
}
EXCLUDED = frozenset({"Builtin.ContextRelevance"})

# Params read by every agentcore method (for the method's params schema).
AGENTCORE_PARAMS: dict[str, Any] = {
    "region": {"type": "string", "pattern": "^[a-z0-9-]+$"},
    "agentcore_evaluator_id": {"type": "string", "minLength": 1, "maxLength": 2048},
    "level": {"enum": ["session", "trace", "tool", "tool_call"]},
}

_RETRYABLE = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServerException",
    }
)


def agentcore_method() -> MethodSpec:
    """The ``agentcore`` vendor method: input is the trajectory, converted to session spans."""
    return MethodSpec(
        "agentcore",
        Kind.VENDOR,
        required_inputs=frozenset({"trajectory"}),
        extra="agentcore",
        extra_module="boto3",
    )


# --------------------------------------------------------------------------- ids and times


def _hex_id(value: str, length: int, *salt: str) -> str:
    text = value.lower().replace("-", "") if length == 32 else value.lower()
    if re.fullmatch(f"[0-9a-f]{{{length}}}", text):
        return text
    return hashlib.sha256("/".join((*salt, value)).encode()).hexdigest()[:length]


def trace_hex(session: Session, trace: Trace) -> str:
    return _hex_id(trace.id, 32, session.id)


def _ns(ms: float) -> int:
    return int(round(ms * 1000)) * 1000  # microsecond precision, as the adapters keep


_FALLBACK_START_MS = 1_700_000_000_000.0  # deterministic clock for steps without timestamps


class _Clock:
    """Deterministic timestamps: real ones when present, else 1 ms ticks after the last one."""

    def __init__(self, steps: Sequence[Step]):
        known = [s.start_ms for s in steps if s.start_ms is not None]
        self.cursor = min(known) if known else _FALLBACK_START_MS

    def span(self, step: Step) -> tuple[int, int]:
        start = step.start_ms if step.start_ms is not None else self.cursor
        end = step.end_ms if step.end_ms is not None else start + 1.0
        end = max(end, start)
        self.cursor = max(self.cursor, end + 1.0)
        return _ns(start), _ns(end)


# --------------------------------------------------------------------------- strands bodies


def _blocks(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tool_use(call: ToolCall | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(call, ToolCall):
        name, args, call_id = call.name, call.arguments, call.id
    else:
        name, args, call_id = call.get("name", ""), call.get("arguments"), call.get("id")
    use: dict[str, Any] = {"name": str(name), "input": dict(args or {})}
    if call_id:
        use["toolUseId"] = str(call_id)
    return {"toolUse": use}


def _input_message(msg: Mapping[str, Any]) -> dict[str, Any] | None:
    role = str(msg.get("role", "user"))
    text = text_of(msg.get("content"))
    if role == "system":
        return {"role": "system", "content": text}
    if role == "tool":
        result: dict[str, Any] = {"status": "success", "content": [{"text": text}]}
        if msg.get("tool_call_id"):
            result["toolUseId"] = str(msg["tool_call_id"])
        return {"role": "tool", "content": {"content": _blocks([{"toolResult": result}])}}
    blocks: list[dict[str, Any]] = [{"text": text}] if text else []
    if role == "assistant":
        blocks += [_tool_use(c) for c in msg.get("tool_calls") or () if isinstance(c, Mapping)]
        return {"role": "assistant", "content": {"content": _blocks(blocks)}} if blocks else None
    return {"role": "user", "content": {"content": _blocks(blocks)}} if blocks else None


def _usage_attrs(step: Step) -> dict[str, Any]:
    u = step.usage
    if u is None:
        return {}
    pairs = {
        "gen_ai.usage.input_tokens": u.input_tokens,
        "gen_ai.usage.output_tokens": u.output_tokens,
        "gen_ai.usage.cache_read_input_tokens": u.cache_read_tokens,
        "gen_ai.usage.cache_write_input_tokens": u.cache_write_tokens,
    }
    return {k: v for k, v in pairs.items() if v is not None}


def _agent_name(step: AgentStep) -> str:
    agent = step.agent_id or "agent"
    if step.system_prompt:
        suffix = f"_{short_hash(step.system_prompt)}"
        if agent.endswith(suffix):
            return agent[: -len(suffix)] or "agent"
    return agent


def _llm_body(step: LlmStep) -> tuple[str, dict[str, Any], dict[str, Any]]:
    inputs = [m for m in (_input_message(x) for x in step.messages) if m is not None]
    blocks: list[dict[str, Any]] = [{"text": step.response}] if step.response else []
    blocks += [_tool_use(c) for c in step.tool_calls]
    finish = "tool_use" if step.tool_calls else "end_turn"
    output = {
        "role": "assistant",
        "content": {"message": _blocks(blocks), "finish_reason": finish},
    }
    attrs: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.system": "strands-agents"}
    if step.model:
        attrs["gen_ai.request.model"] = step.model
    attrs["gen_ai.response.finish_reasons"] = [finish]
    attrs.update(_usage_attrs(step))
    return "chat", attrs, {"input": {"messages": inputs}, "output": {"messages": [output]}}


def _tool_body(step: ToolStep) -> tuple[str, dict[str, Any], dict[str, Any]]:
    call = step.call
    call_id = call.id or (step.result.call_id if step.result else None)
    error = step.error or (step.result.error if step.result else None)
    content = step.result.content if step.result else None
    result_text = text_of(content) if content is not None else (error or "")
    tool_in: dict[str, Any] = {"content": _blocks(dict(call.arguments)), "role": "tool"}
    tool_out: dict[str, Any] = {"message": _blocks([{"text": result_text}])}
    if call_id:
        tool_in["id"] = tool_out["id"] = str(call_id)
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.system": "strands-agents",
        "gen_ai.tool.name": call.name,
        "gen_ai.tool.status": "error" if error else "success",
    }
    if call_id:
        attrs["gen_ai.tool.call.id"] = str(call_id)
    body = {
        "input": {"messages": [{"role": "tool", "content": tool_in}]},
        "output": {"messages": [{"role": "assistant", "content": tool_out}]},
    }
    return f"execute_tool {call.name}", attrs, body


def _agent_body(
    name: str,
    system: str | None,
    user: str | None,
    response: str | None,
    tools: Sequence[Mapping[str, Any]] = (),
    step: Step | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    if system:
        inputs.append({"role": "system", "content": system})
    inputs.append({"role": "user", "content": {"content": _blocks([{"text": user or ""}])}})
    output = {
        "role": "assistant",
        "content": {"message": response or "", "finish_reason": "end_turn"},
    }
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.system": "strands-agents",
        "gen_ai.agent.name": name,
    }
    names = [str(t.get("name")) for t in tools if t.get("name")]
    if names:
        attrs["gen_ai.agent.tools"] = json.dumps(names)
    if any("description" in t or "parameters" in t for t in tools):
        attrs["gen_ai.tool.definitions"] = json.dumps(
            [
                {
                    "name": str(t.get("name", "")),
                    "description": t.get("description", ""),
                    "inputSchema": {"json": t.get("parameters") or {}},
                }
                for t in tools
            ]
        )
    if step is not None:
        attrs.update(_usage_attrs(step))
    return (
        f"invoke_agent {name}",
        attrs,
        {"input": {"messages": inputs}, "output": {"messages": [output]}},
    )


# --------------------------------------------------------------------------- conversion


def _first_user(steps: Sequence[Step]) -> str | None:
    for step in steps:
        if isinstance(step, LlmStep):
            for msg in step.messages:
                if msg.get("role") == "user" and text_of(msg.get("content")):
                    return text_of(msg.get("content"))
    return None


def _final_response(steps: Sequence[Step]) -> str | None:
    for step in reversed(steps):
        if isinstance(step, LlmStep) and step.response:
            return step.response
    return None


def _trace_spans(session: Session, trace: Trace) -> list[dict[str, Any]]:
    trace_id = trace_hex(session, trace)
    steps = list(trace.steps)
    emitted = [s for s in steps if isinstance(s, (AgentStep, LlmStep, ToolStep))]
    if not emitted:
        return []
    ids = {s.id: _hex_id(s.id, 16, session.id, trace.id) for s in steps}
    parents = {s.id: s.parent_id for s in steps}
    emitted_ids = {s.id for s in emitted}
    clock = _Clock(steps)
    times = {s.id: clock.span(s) for s in emitted}

    root: str | None = None
    items: list[dict[str, Any]] = []
    if not any(isinstance(s, AgentStep) for s in emitted):
        user, response = _first_user(emitted), _final_response(emitted)
        if user or response:
            root = _hex_id(f"{trace.id}:agent", 16, session.id, trace.id)
            start = min(t[0] for t in times.values())
            end = max(t[1] for t in times.values())
            name, attrs, body = _agent_body("agent", None, user, response)
            items += _span_items(session, trace_id, root, None, name, attrs, body, (start, end))

    def parent_of(step: Step) -> str | None:
        current, seen = parents.get(step.id), set()
        while current is not None and current not in seen:
            if current in emitted_ids:
                return ids[current]
            seen.add(current)
            current = parents.get(current)
        return root

    for step in emitted:
        if isinstance(step, AgentStep):
            name, attrs, body = _agent_body(
                _agent_name(step),
                step.system_prompt,
                step.user_prompt,
                step.response,
                step.available_tools,
                step,
            )
        elif isinstance(step, LlmStep):
            name, attrs, body = _llm_body(step)
        else:
            name, attrs, body = _tool_body(step)
        items += _span_items(
            session,
            trace_id,
            ids[step.id],
            parent_of(step),
            name,
            attrs,
            body,
            times[step.id],
            error=step.error,
        )
    return items


def _span_items(
    session: Session,
    trace_id: str,
    span_id: str,
    parent: str | None,
    name: str,
    attrs: dict[str, Any],
    body: dict[str, Any],
    times: tuple[int, int],
    error: str | None = None,
) -> list[dict[str, Any]]:
    start, end = times
    status: dict[str, Any] = {"code": "ERROR", "message": error} if error else {"code": "OK"}
    span: dict[str, Any] = {"traceId": trace_id, "spanId": span_id}
    if parent is not None:
        span["parentSpanId"] = parent
    span.update(
        {
            "name": name,
            "kind": "INTERNAL",
            "startTimeUnixNano": start,
            "endTimeUnixNano": end,
            "durationNano": end - start,
            "scope": {"name": SCOPE, "version": ""},
            "attributes": {"session.id": session.id, **attrs},
            "status": status,
        }
    )
    event = {
        "traceId": trace_id,
        "spanId": span_id,
        "timeUnixNano": end,
        "observedTimeUnixNano": end,
        "severityNumber": 9,
        "severityText": "",
        "scope": {"name": SCOPE},
        "attributes": {"event.name": SCOPE, "session.id": session.id},
        "body": body,
    }
    return [span, event]


def session_spans(session: Session) -> list[dict[str, Any]]:
    """Our Session as the AgentCore Evaluations ``sessionSpans`` payload (spans + events)."""
    items: list[dict[str, Any]] = []
    for trace in session.traces:
        items += _trace_spans(session, trace)
    return items


def trace_ids(spans: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for item in spans:
        if is_span(item):
            seen.setdefault(str(item["traceId"]), None)
    return list(seen)


# --------------------------------------------------------------------------- ETK SpanFilter


def is_span(item: Mapping[str, Any]) -> bool:
    return "name" in item and "kind" in item and "startTimeUnixNano" in item


def is_tool_span(name: str, attributes: Mapping[str, Any]) -> bool:
    low = name.lower()
    return (
        "execute_tool" in low
        or "tool_call" in low
        or "tool_use" in low
        or "gen_ai.tool.name" in attributes
    )


def tool_span_ids(spans: Sequence[Mapping[str, Any]]) -> list[str]:
    """Span ids of tool execution spans, in payload order (ETK ``extract_tool_span_ids``)."""
    return [
        str(item["spanId"])
        for item in spans
        if is_span(item)
        and "spanId" in item
        and is_tool_span(str(item.get("name", "")), item.get("attributes") or {})
    ]


def build_eval_params(
    evaluator_id: str, level: str, traces: list[str], spans: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``evaluate()`` kwargs by level (ETK ``_build_eval_params``) and mapping details."""
    params: dict[str, Any] = {
        "evaluatorId": evaluator_id,
        "evaluationInput": {"sessionSpans": spans},
    }
    details: dict[str, Any] = {}
    if level == "trace":
        params["evaluationTarget"] = {"traceIds": traces}
    elif level == "tool":
        tools = tool_span_ids(spans)
        if tools:
            if len(tools) > MAX_TOOL_SPANS:
                logger.warning(
                    "limiting tool spans from %d to %d for evaluator %r",
                    len(tools),
                    MAX_TOOL_SPANS,
                    evaluator_id,
                )
                details["tool_spans_total"] = len(tools)
                details["tool_spans_capped"] = True
            params["evaluationTarget"] = {"spanIds": tools[:MAX_TOOL_SPANS]}
        else:  # ETK: no tool spans -> fall back to the trace ids
            details["tool_spans_fallback"] = "traceIds"
            params["evaluationTarget"] = {"traceIds": traces}
    return params, details


# --------------------------------------------------------------------------- evaluate


_client_cache: dict[str, tuple[Any, Any]] = {}


def default_region() -> str:
    """``EVAL_JUDGE_REGION``, else ``AWS_REGION`` / ``AWS_DEFAULT_REGION``, else the AWS SDK's
    configured region (``~/.aws/config``), else ``DEFAULT_REGION``."""
    from ...config import EvalConfig, get_env

    configured = (
        EvalConfig.from_env().judge_region
        or get_env("AWS_REGION", prefix="")
        or get_env("AWS_DEFAULT_REGION", prefix="")
    )
    if configured:
        return configured
    session = getattr(getattr(require("boto3", "agentcore"), "session", None), "Session", None)
    return (session().region_name if session else None) or DEFAULT_REGION


def _client(region: str) -> Any:
    boto3 = require("boto3", "agentcore")
    cached = _client_cache.get(region)
    if cached is not None and cached[0] is boto3:
        return cached[1]
    client = boto3.client("bedrock-agentcore", region_name=region)
    _client_cache[region] = (boto3, client)
    return client


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        code = (response.get("Error") or {}).get("Code")
        return str(code) if code else None
    return None


def _fail(code: str, message: str, retryable: bool = False, **details: Any) -> Outcome:
    return Outcome(
        None, status="error", error=ErrorInfo(code, message, retryable=retryable), details=details
    )


def resolve_evaluator(params: Mapping[str, Any], default: str) -> tuple[str, str | None]:
    """``(evaluator_id, level)``: built-ins have a fixed level; custom ids need ``level``."""
    evaluator = str(params.get("agentcore_evaluator_id") or default)
    if evaluator in BUILTINS:
        return evaluator, BUILTINS[evaluator]
    level = params.get("level")
    if level == "tool_call":
        level = "tool"
    return evaluator, level if level in LEVELS else None


async def evaluate(case: EvalCase, ctx: MetricContext, default_evaluator: str) -> Outcome:
    """Run one AgentCore evaluator on ``case.trajectory``; score = the returned ``value``."""
    evaluator, level = resolve_evaluator(ctx.params, default_evaluator)
    if evaluator in EXCLUDED:
        return _fail("metric_error", f"{evaluator} is not supported (the AWS API errors on it)")
    if level is None:
        return _fail(
            "metric_error",
            f"custom AgentCore evaluator {evaluator!r} requires level (session, trace or tool)",
        )
    spans = session_spans(case.trajectory) if case.trajectory is not None else []
    if not spans:
        return Outcome(
            None,
            status="skipped",
            error=ErrorInfo("missing_input", "no evaluable spans in the trajectory"),
        )
    traces = trace_ids(spans)
    kwargs, details = build_eval_params(evaluator, level, traces, spans)
    region = str(ctx.params.get("region") or default_region())
    details = {"evaluator_id": evaluator, "level": level, "spans": len(spans), **details}
    client = _client(region)
    try:
        response = await asyncio.to_thread(client.evaluate, **kwargs)
    except Exception as exc:  # noqa: BLE001 - mapped to an error result
        code = _error_code(exc)
        retryable = code in _RETRYABLE
        return _fail(
            "judge_unavailable" if retryable else "metric_error",
            f"AgentCore evaluate failed: {code or type(exc).__name__}: {exc}",
            retryable=retryable,
            **details,
        )
    results = (response or {}).get("evaluationResults") or []
    if len(results) != 1:
        return _fail(
            "metric_error",
            f"evaluator {evaluator!r} returned {len(results)} results, expected 1",
            **details,
        )
    (result,) = results
    value = result.get("value")
    if value is None:
        message = result.get("errorMessage")
        if message:
            return _fail("metric_error", f"evaluator {evaluator!r} error: {message}", **details)
        return _fail("vendor_nan", f"evaluator {evaluator!r} returned no value", **details)
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = math.nan
    if math.isnan(score) or math.isinf(score):
        return _fail(
            "vendor_nan",
            f"evaluator {evaluator!r} returned non-numeric value {value!r}",
            **details,
        )
    if result.get("label") is not None:
        details["label"] = result["label"]
    if result.get("tokenUsage"):
        details["token_usage"] = result["tokenUsage"]
    return Outcome(
        score=score,
        reason=result.get("explanation"),
        details=details,
        provenance={
            "judge": {
                "provider": "agentcore",
                "model": evaluator,
                "params": {"region": region, "level": level},
            }
        },
    )


__all__ = [
    "AGENTCORE_PARAMS",
    "BUILTINS",
    "EXCLUDED",
    "MAX_TOOL_SPANS",
    "agentcore_method",
    "build_eval_params",
    "evaluate",
    "session_spans",
    "tool_span_ids",
    "trace_ids",
]
