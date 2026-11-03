"""OpenTelemetry span normalization for the OTel-based adapters (plain JSON only).

Accepted span shapes:

- OTLP/JSON export: ``{"resourceSpans": [{"scopeSpans": [{"scope": {...}, "spans": [...]}]}]}``
  with ``attributes: [{"key": k, "value": {"stringValue": ...}}]`` (a list of exports = JSONL);
- ``ReadableSpan.to_json()`` dicts: ``{"context": {"trace_id": "0x..", "span_id": ..},
  "parent_id": .., "start_time": "<ISO>", "attributes": {..}, "events": [..]}``;
- CloudWatch / ADOT records: ``{"traceId", "spanId", "parentSpanId", "startTimeUnixNano",
  "scope": {"name"}, "attributes": {..}}``, with span events as separate log records carrying
  ``attributes["event.name"]`` and a ``body`` (AgentSight ``OtelTraceLogsParser``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._common import as_dict, get, records, to_ms


@dataclass
class OtelEvent:
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    time_ms: float | None = None


@dataclass
class OtelSpan:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    scope: str
    start_ms: float | None
    end_ms: float | None
    attributes: dict[str, Any]
    events: list[OtelEvent]
    status_code: str
    status_message: str | None

    @property
    def error(self) -> str | None:
        if self.status_code in ("ERROR", "STATUS_CODE_ERROR", "2"):
            return self.status_message or "error"
        return None


def any_value(value: Any) -> Any:
    """Decode an OTLP ``AnyValue`` (``{"stringValue": ..}`` etc.); plain values pass through."""
    if not isinstance(value, Mapping) or len(value) != 1:
        return value
    ((kind, inner),) = value.items()
    if kind in ("stringValue", "boolValue", "doubleValue", "bytesValue"):
        return inner
    if kind == "intValue":
        try:
            return int(inner)
        except (TypeError, ValueError):
            return inner
    if kind == "arrayValue":
        return [any_value(v) for v in (inner or {}).get("values", [])]
    if kind == "kvlistValue":
        return kv_attributes((inner or {}).get("values", []))
    return value


def kv_attributes(value: Any) -> dict[str, Any]:
    """OTLP ``[{"key", "value"}]`` lists or plain dicts -> ``{key: value}``."""
    if isinstance(value, Mapping):
        return dict(value)
    out: dict[str, Any] = {}
    for item in records(value):
        if isinstance(item, Mapping) and "key" in item:
            out[str(item["key"])] = any_value(item.get("value"))
    return out


def _hex_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if text.startswith("0x"):
        text = text[2:]
    return text or None


def _status(raw: Any) -> tuple[str, str | None]:
    if isinstance(raw, Mapping):
        code = get(raw, "code", "status_code", default="UNSET")
        return str(code).upper(), get(raw, "message", "description")
    if raw is None:
        return "UNSET", None
    return str(raw).upper(), None


def _event(raw: Mapping[str, Any]) -> OtelEvent:
    attrs = kv_attributes(raw.get("attributes"))
    return OtelEvent(
        name=str(get(raw, "name", "eventName", "EventName", default=attrs.get("event.name", ""))),
        attributes=attrs,
        body=raw.get("body"),
        time_ms=to_ms(get(raw, "timeUnixNano", "time_unix_nano", "timestamp")),
    )


def _span(raw: Mapping[str, Any], scope: str | None = None) -> OtelSpan | None:
    context = as_dict(raw.get("context"))
    trace_id = _hex_id(get(raw, "traceId", "trace_id", default=context.get("trace_id")))
    span_id = _hex_id(get(raw, "spanId", "span_id", default=context.get("span_id")))
    if trace_id is None or span_id is None:
        return None
    parent = get(raw, "parentSpanId", "parent_span_id", "parent_id")
    if isinstance(parent, Mapping):
        parent = parent.get("span_id")
    scope_raw = get(raw, "scope", "instrumentationScope", "instrumentation_scope")
    if isinstance(scope_raw, Mapping):
        scope = str(scope_raw.get("name") or scope or "unknown")
    elif isinstance(scope_raw, str):
        scope = scope_raw
    code, message = _status(raw.get("status"))
    return OtelSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_id=_hex_id(parent),
        name=str(raw.get("name") or ""),
        scope=scope or "unknown",
        start_ms=to_ms(get(raw, "startTimeUnixNano", "start_time", "startTime")),
        end_ms=to_ms(get(raw, "endTimeUnixNano", "end_time", "endTime")),
        attributes=kv_attributes(raw.get("attributes")),
        events=[_event(e) for e in records(raw.get("events")) if isinstance(e, Mapping)],
        status_code=code,
        status_message=message,
    )


def is_event_record(raw: Any) -> bool:
    """A CloudWatch log record carrying a span event (not a span)."""
    if not isinstance(raw, Mapping):
        return False
    attrs = raw.get("attributes")
    named = isinstance(attrs, Mapping) and "event.name" in attrs
    return bool(raw.get("EventName") or raw.get("eventName") or named) and not (
        "startTimeUnixNano" in raw or "start_time" in raw or "name" in raw and "kind" in raw
    )


def is_otlp_export(raw: Any) -> bool:
    return isinstance(raw, Mapping) and isinstance(raw.get("resourceSpans"), list)


def parse_spans(data: Any) -> tuple[list[OtelSpan], int]:
    """All spans (input order) with event records attached; also the count of skipped records."""
    spans: list[OtelSpan] = []
    loose: dict[str, list[OtelEvent]] = {}
    skipped = 0
    items = records(data)
    if isinstance(data, Mapping) and not is_otlp_export(data):
        items = records(get(data, "spans", "records", default=[data]))
    for item in items:
        if is_otlp_export(item):
            for resource in item["resourceSpans"]:
                for block in records(as_dict(resource).get("scopeSpans")) + records(
                    as_dict(resource).get("instrumentationLibrarySpans")
                ):
                    block = as_dict(block)
                    scope = as_dict(block.get("scope") or block.get("instrumentationLibrary"))
                    for raw in records(block.get("spans")):
                        span = _span(raw, str(scope.get("name") or "unknown"))
                        if span is None:
                            skipped += 1
                        else:
                            spans.append(span)
        elif is_event_record(item):
            event = _event(item)
            span_id = _hex_id(
                get(item, "spanId", "span_id", default=as_dict(item.get("context")).get("span_id"))
            )
            if span_id is None:
                skipped += 1
            else:
                loose.setdefault(span_id, []).append(event)
        elif isinstance(item, Mapping):
            span = _span(item)
            if span is None:
                skipped += 1
            else:
                spans.append(span)
        else:
            skipped += 1
    for span in spans:
        span.events.extend(loose.pop(span.span_id, []))
    skipped += sum(len(v) for v in loose.values())
    return spans, skipped


def group_by_trace(spans: list[OtelSpan]) -> dict[str, list[OtelSpan]]:
    grouped: dict[str, list[OtelSpan]] = {}
    for span in spans:
        grouped.setdefault(span.trace_id, []).append(span)
    return grouped


def session_id_of(spans: list[OtelSpan]) -> str | None:
    for span in spans:
        value = span.attributes.get("session.id")
        if value:
            return str(value)
    return None
