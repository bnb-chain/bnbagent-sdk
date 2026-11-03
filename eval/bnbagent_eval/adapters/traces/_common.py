"""Shared helpers for the trace adapters: parsing, message shapes, and step-tree finalization.

Normalized message shape (``LlmStep.messages``)::

    {"role": "system" | "user" | "assistant" | "tool", "content": str,
     "tool_calls": [{"id": ..., "name": ..., "arguments": {...}}],   # assistant only
     "tool_call_id": ...}                                             # tool only
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ...types.trajectory import AgentStep, Step, ToolCall, Trace, Usage

# --------------------------------------------------------------------------- parsing


def parse_json(value: Any, default: Any = None, *, literal: bool = False) -> Any:
    """``json.loads`` for strings (optionally falling back to Python literals); else as-is."""
    if not isinstance(value, str):
        return default if value is None else value
    try:
        return json.loads(value, strict=False)
    except ValueError:
        pass
    if literal:
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    return default


def as_dict(value: Any) -> dict[str, Any]:
    value = parse_json(value, value)
    return dict(value) if isinstance(value, Mapping) else {}


def as_list(value: Any) -> list[Any]:
    value = parse_json(value, value)
    return list(value) if isinstance(value, (list, tuple)) else []


def arguments(value: Any) -> dict[str, Any]:
    """Tool arguments as a mapping (JSON / Python-literal strings are parsed)."""
    parsed = parse_json(value, value, literal=True)
    if parsed is None or parsed == "":
        return {}
    if isinstance(parsed, Mapping):
        return dict(parsed)
    return {"input": parsed}


def status_error(status: Any, content: Any = None) -> str | None:
    """Tool error text (the result content, else the status) unless the status is success."""
    if status is None or str(status).lower() in ("", "success", "ok", "unset"):
        return None
    return text_of(content) or str(status)


def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def get(obj: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """First present (non-None) key, e.g. ``get(obs, "startTime", "start_time")``."""
    for key in keys:
        value = obj.get(key)
        if value is not None:
            return value
    return default


def as_int(value: Any) -> int | None:
    value = parse_json(value, value)
    if isinstance(value, Mapping):  # OTLP {"intValue": 0}
        value = value.get("intValue", value.get("doubleValue"))
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_FRACTION = re.compile(r"(\.\d+)")


def to_ms(value: Any) -> float | None:
    """Epoch milliseconds from Unix nanoseconds (int / digit string) or an ISO-8601 string."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_number(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _from_number(float(text))
        return _from_iso(text)
    return None


def _from_number(number: float) -> float | None:
    if number <= 0:
        return None
    if number > 1e17:  # nanoseconds
        return round(number / 1e6, 3)
    if number > 1e14:  # microseconds
        return round(number / 1e3, 3)
    if number > 1e11:  # milliseconds
        return round(number, 3)
    return round(number * 1e3, 3)  # seconds


def _from_iso(text: str) -> float | None:
    text = text.replace(" ", "T", 1) if "T" not in text else text
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python 3.10 ``fromisoformat`` only accepts 3- or 6-digit fractions.
    text = _FRACTION.sub(lambda m: (m.group(1) + "000000")[:7], text, count=1)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return round(moment.timestamp() * 1000, 3)


_DURATION = re.compile(r"^P(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def iso_duration_ms(text: Any) -> float | None:
    """``PT1M48.75533S`` -> milliseconds."""
    if not isinstance(text, str):
        return None
    match = _DURATION.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return round((hours * 3600 + minutes * 60 + seconds) * 1000, 3)


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def agent_key(name: str | None, system_prompt: str | None) -> str | None:
    """AgentSight agent identity: ``<name>_<sha256(system_prompt)[:8]>`` (or just the name)."""
    if system_prompt:
        return f"{name or ''}_{short_hash(system_prompt)}"
    return name or None


# --------------------------------------------------------------------------- messages


def message(role: str, content: Any = "", **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": role, "content": text_of(content)}
    out.update({k: v for k, v in extra.items() if v not in (None, [], ())})
    return out


def tool_call_dict(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}


def strands_blocks(value: Any) -> list[dict[str, Any]]:
    """Strands / Bedrock Converse content blocks (``[{"text"}, {"toolUse"}, {"toolResult"}]``)."""
    parsed = parse_json(value, value)
    if isinstance(parsed, Mapping):
        return [dict(parsed)]
    if isinstance(parsed, list):
        return [dict(b) for b in parsed if isinstance(b, Mapping)]
    if isinstance(parsed, str) and parsed:
        return [{"text": parsed}]
    return []


def blocks_text(blocks: Iterable[Mapping[str, Any]]) -> str:
    parts = []
    for block in blocks:
        if "text" in block:
            parts.append(text_of(block["text"]))
        elif "json" in block:
            parts.append(text_of(block["json"]))
    return "\n".join(p for p in parts if p)


def blocks_tool_calls(blocks: Iterable[Mapping[str, Any]]) -> list[ToolCall]:
    calls = []
    for block in blocks:
        use = block.get("toolUse")
        if isinstance(use, Mapping):
            calls.append(
                ToolCall(
                    name=str(use.get("name", "")),
                    arguments=arguments(use.get("input")),
                    id=use.get("toolUseId"),
                )
            )
    return calls


def tool_result_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping):
            return text_of(first.get("text", first.get("json", "")))
        return text_of(first)
    return text_of(content)


def strands_messages(role: str, value: Any) -> list[dict[str, Any]]:
    """One Strands message (role + content blocks) -> normalized messages.

    ``toolResult`` blocks become separate ``tool`` messages; ``toolUse`` blocks become
    ``tool_calls`` on the assistant message.
    """
    blocks = strands_blocks(value)
    out: list[dict[str, Any]] = []
    text = blocks_text(blocks)
    calls = blocks_tool_calls(blocks)
    if text or calls:
        speaker = "assistant" if calls else ("user" if role == "tool" else role)
        out.append(message(speaker, text, tool_calls=[tool_call_dict(c) for c in calls]))
    for block in blocks:
        result = block.get("toolResult")
        if isinstance(result, Mapping):
            out.append(
                message("tool", tool_result_text(result), tool_call_id=result.get("toolUseId"))
            )
    return out


def last_assistant(messages: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg
    return None


def calls_from_message(msg: Mapping[str, Any] | None) -> tuple[ToolCall, ...]:
    if not msg:
        return ()
    return tuple(
        ToolCall(
            name=str(c.get("name", "")), arguments=arguments(c.get("arguments")), id=c.get("id")
        )
        for c in msg.get("tool_calls", ())
        if isinstance(c, Mapping)
    )


def tool_def(name: Any, description: Any = None, parameters: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"name": str(name)}
    if description:
        out["description"] = str(description)
    if parameters:
        out["parameters"] = parameters
    return out


def dedupe_tools(tools: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    seen: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name", ""))
        if name and name not in seen:
            seen[name] = dict(tool)
    return tuple(seen.values())


# --------------------------------------------------------------------------- usage


def usage(
    input_tokens: Any = None,
    output_tokens: Any = None,
    cache_read: Any = None,
    cache_write: Any = None,
    cost: Any = None,
) -> Usage | None:
    values = Usage(
        input_tokens=as_int(input_tokens),
        output_tokens=as_int(output_tokens),
        cache_read_tokens=as_int(cache_read),
        cache_write_tokens=as_int(cache_write),
        cost_usd=as_float(cost) or None,
    )
    if all(v is None for v in dataclasses.astuple(values)):
        return None
    if not any(dataclasses.astuple(values)):  # all zeros: no information
        return None
    return values


def genai_usage(attrs: Mapping[str, Any]) -> Usage | None:
    """Usage from GenAI semconv attributes (current and legacy names)."""
    return usage(
        get(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
        get(attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
        get(attrs, "gen_ai.usage.cache_read_input_tokens", "gen_ai.usage.cache_read_tokens"),
        get(attrs, "gen_ai.usage.cache_write_input_tokens", "gen_ai.usage.cache_creation_tokens"),
        get(attrs, "gen_ai.usage.cost"),
    )


# --------------------------------------------------------------------------- tree


class Warnings:
    """Ordered, de-duplicated warning collector."""

    def __init__(self) -> None:
        self._items: dict[str, None] = {}

    def add(self, text: str) -> None:
        self._items.setdefault(text, None)

    def extend(self, texts: Iterable[str]) -> None:
        for text in texts:
            self.add(text)

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(self._items)


def nearest_converted(
    parent_id: str | None,
    raw_parents: Mapping[str, str | None],
    converted: Mapping[str, str] | set[str],
    warnings: Warnings,
    *,
    origin: str,
) -> str | None:
    """Walk raw parents up to the nearest converted ancestor (cycle- and orphan-safe).

    ``converted`` maps a raw id to the step id it became (a set means identity).
    """
    seen: set[str] = set()
    current = parent_id
    while current is not None:
        if current in converted:
            return converted[current] if isinstance(converted, Mapping) else current
        if current in seen:
            warnings.add(f"parent cycle detected at span {current!r} (from {origin!r})")
            return None
        seen.add(current)
        if current not in raw_parents:
            warnings.add(f"orphan span {origin!r}: parent {current!r} not found")
            return None
        current = raw_parents[current]
    return None


def reparent(
    steps: Sequence[Step],
    raw_parents: Mapping[str, str | None],
    warnings: Warnings,
    converted: Mapping[str, str] | set[str] | None = None,
) -> list[Step]:
    """Point each step's parent at its nearest *mapped* ancestor (AgentSight behaviour)."""
    targets = converted if converted is not None else {s.id for s in steps}
    out: list[Step] = []
    for step in steps:
        parent = nearest_converted(step.parent_id, raw_parents, targets, warnings, origin=step.id)
        out.append(
            dataclasses.replace(step, parent_id=parent) if parent != step.parent_id else step
        )
    return out


def finalize_trace(
    trace_id: str,
    steps: Sequence[Step],
    warnings: Warnings,
    *,
    expect_timestamps: bool = True,
    sort: bool = True,
) -> Trace:
    """Order steps, drop duplicate ids, break parent cycles and inherit ``agent_id``."""
    unique: list[Step] = []
    seen: set[str] = set()
    for step in steps:
        if step.id in seen:
            warnings.add(f"trace {trace_id!r}: duplicate step id {step.id!r} dropped")
            continue
        seen.add(step.id)
        unique.append(step)

    by_id = {s.id: s for s in unique}
    fixed: dict[str, str | None] = {}
    for step in unique:
        parent = step.parent_id
        if parent is not None and parent not in by_id:
            warnings.add(f"orphan span {step.id!r}: parent {parent!r} not found")
            parent = None
        fixed[step.id] = parent
    for step in unique:  # break cycles: the first step found on a cycle becomes a root
        path: list[str] = []
        current: str | None = step.id
        while current is not None and current not in path:
            path.append(current)
            current = fixed.get(current)
        if current is not None:
            warnings.add(f"parent cycle detected at span {current!r}")
            fixed[current] = None

    def owner(step_id: str) -> str | None:
        current = fixed.get(step_id)
        while current is not None:
            step = by_id[current]
            if isinstance(step, AgentStep) and step.agent_id:
                return step.agent_id
            current = fixed.get(current)
        return None

    out: list[Step] = []
    for step in unique:
        changes: dict[str, Any] = {}
        if fixed[step.id] != step.parent_id:
            changes["parent_id"] = fixed[step.id]
        if step.agent_id is None:
            agent = owner(step.id)
            if agent:
                changes["agent_id"] = agent
        out.append(dataclasses.replace(step, **changes) if changes else step)

    if expect_timestamps:
        missing = sum(1 for s in out if s.start_ms is None or s.end_ms is None)
        if missing:
            warnings.add(f"trace {trace_id!r}: {missing} step(s) without timestamps")
    if sort and out and all(s.start_ms is not None for s in out):
        out = [s for _, s in sorted(enumerate(out), key=lambda p: (p[1].start_ms, p[0]))]
    return Trace(id=trace_id, steps=tuple(out))


def records(data: Any) -> list[Any]:
    """A list from a list, a tuple, or a single object."""
    if isinstance(data, (list, tuple)):
        return list(data)
    return [] if data is None else [data]
