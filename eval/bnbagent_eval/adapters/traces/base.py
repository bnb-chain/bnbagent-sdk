"""Trace-adapter protocol, registry and format auto-detection.

An adapter maps one external trace format (plain JSON / JSONL data, never SDK objects) into a
normalized :class:`Session`. ``detect`` returns a confidence in ``[0, 1]``; ``detect_format``
picks the most confident adapter and refuses to guess between equally confident ones.
(Counterpart of AgentSight's ``SessionMapperFactory``, with scored instead of first-match
detection.)
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ...exceptions import AdapterError
from ...types.trajectory import Session

__all__ = [
    "AdapterRegistry",
    "REGISTRY",
    "TraceAdapter",
    "adapter_names",
    "detect_format",
    "get_adapter",
    "load_trace",
    "read_trace_file",
    "register_adapter",
]

AUTO = "auto"


@runtime_checkable
class TraceAdapter(Protocol):
    name: str

    def detect(self, data: Any) -> float:
        """Confidence in ``[0, 1]`` that ``data`` is in this format (0 = not this format)."""
        ...

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        """Map ``data`` to a Session; problems that do not prevent mapping go in ``warnings``."""
        ...


class AdapterRegistry:
    def __init__(self, adapters: Iterable[TraceAdapter] = ()) -> None:
        self._adapters: dict[str, TraceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: TraceAdapter, *, replace: bool = False) -> TraceAdapter:
        if not isinstance(adapter, TraceAdapter):
            raise TypeError(f"{adapter!r} does not implement TraceAdapter")
        if adapter.name in self._adapters and not replace:
            raise ValueError(f"trace adapter {adapter.name!r} is already registered")
        self._adapters[adapter.name] = adapter
        return adapter

    def names(self) -> list[str]:
        return list(self._adapters)

    def get(self, name: str) -> TraceAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise AdapterError(
                f"unknown trace format {name!r}; known formats: {', '.join(self._adapters)}"
            ) from None

    def candidates(self, data: Any) -> list[tuple[str, float]]:
        """Adapters with a non-zero confidence, most confident first (ties keep order)."""
        scored = []
        for adapter in self._adapters.values():
            try:
                confidence = float(adapter.detect(data))
            except Exception:  # noqa: BLE001 - a detector must never break detection
                confidence = 0.0
            if confidence > 0:
                scored.append((adapter.name, min(confidence, 1.0)))
        return sorted(scored, key=lambda item: -item[1])

    def detect(self, data: Any) -> tuple[str, list[tuple[str, float]]]:
        candidates = self.candidates(data)
        if not candidates:
            raise AdapterError(
                f"could not detect the trace format ({type(data).__name__} input); "
                f"pass format= explicitly, one of: {', '.join(self._adapters)}"
            )
        best = candidates[0][1]
        tied = [name for name, confidence in candidates if confidence == best]
        if len(tied) > 1:
            raise AdapterError(
                f"ambiguous trace format: {', '.join(tied)} are equally likely "
                f"(confidence {best:.2f}); pass format= explicitly"
            )
        return candidates[0][0], candidates

    def load(self, data: Any, format: str = AUTO, *, session_id: str | None = None) -> Session:
        if isinstance(data, Path):
            data = read_trace_file(data)
        name = self.detect(data)[0] if format == AUTO else format
        adapter = self.get(name)
        try:
            return adapter.to_session(data, session_id=session_id)
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface mapping bugs as adapter errors
            raise AdapterError(f"{name}: cannot map trace: {type(exc).__name__}: {exc}") from exc


REGISTRY = AdapterRegistry()


def register_adapter(adapter: TraceAdapter, *, replace: bool = False) -> TraceAdapter:
    """Register an adapter for ``load_trace`` / ``detect_format``."""
    return REGISTRY.register(adapter, replace=replace)


def get_adapter(name: str) -> TraceAdapter:
    return REGISTRY.get(name)


def adapter_names() -> list[str]:
    return REGISTRY.names()


def detect_format(data: Any) -> tuple[str, list[tuple[str, float]]]:
    """``(best, [(format, confidence), ...])``; ``AdapterError`` if unknown or tied."""
    return REGISTRY.detect(data)


def load_trace(data: Any, format: str = AUTO, *, session_id: str | None = None) -> Session:
    """Map parsed trace data (or a ``Path`` to a JSON / JSONL file) to a Session."""
    return REGISTRY.load(data, format, session_id=session_id)


def read_trace_file(path: str | Path) -> Any:
    """Parse a JSON file, or a JSONL file into a list of records."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AdapterError(f"cannot read trace file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError as exc:
        raise AdapterError(f"{path}: neither JSON nor JSONL: {exc}") from exc
