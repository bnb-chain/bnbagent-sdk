"""The native trace format: the JSON form of :class:`Session` (``Session.to_dict()``).

Example::

    {"id": "s1", "traces": [{"id": "t1", "steps": [
        {"kind": "llm", "id": "1", "model": "m", "response": "…", "start_ms": 0, "end_ms": 900},
        {"kind": "tool", "id": "2", "call": {"name": "search", "arguments": {"q": "bnb"}},
         "result": {"content": "…"}}
    ]}]}
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...exceptions import AdapterError, SchemaError
from ...types.trajectory import Session


def load_session(source: str | Path | Mapping[str, Any]) -> Session:
    """Load a Session from a mapping, a JSON string, or a path to a JSON file."""
    if isinstance(source, Mapping):
        data: Any = source
    else:
        text = str(source)
        if not text.lstrip().startswith("{"):
            try:
                text = Path(text).read_text(encoding="utf-8")
            except OSError as exc:
                raise AdapterError(f"cannot read trace file {source}: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AdapterError(f"invalid trace JSON: {exc}") from exc
    try:
        return Session.from_dict(data)
    except SchemaError as exc:
        raise AdapterError(f"not a native trace: {exc}") from exc


def dump_session(session: Session) -> str:
    return json.dumps(session.to_dict(), ensure_ascii=False)


class NativeAdapter:
    """The native format as a registered trace adapter (``Session.to_dict()`` JSON)."""

    name = "native"

    def detect(self, data: Any) -> float:
        if not isinstance(data, Mapping) or "id" not in data:
            return 0.0
        traces = data.get("traces")
        if not isinstance(traces, list) or not set(data) <= {"id", "traces", "warnings"}:
            return 0.0
        if all(isinstance(t, Mapping) and isinstance(t.get("steps", []), list) for t in traces):
            return 1.0
        return 0.0

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        session = load_session(data)
        if session_id is not None and session_id != session.id:
            session = Session(id=session_id, traces=session.traces, warnings=session.warnings)
        return session
