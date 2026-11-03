"""The evaluation case: one item to evaluate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._serde import Serializable
from .trajectory import Session, ToolCall


@dataclass(frozen=True)
class ContentPart(Serializable):
    """A piece of multimodal content."""

    type: Literal["text", "image", "file", "json"]
    text: str | None = None
    data: Any = None
    uri: str | None = None
    media_type: str | None = None
    hash: str | None = None


@dataclass(frozen=True)
class Artifact(Serializable):
    """A non-text deliverable referenced by URI and content hash."""

    uri: str
    hash: str
    media_type: str
    size: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class Context(Serializable):
    """A retrieval context."""

    text: str
    timestamp: str | None = None  # ISO-8601
    source: str | None = None


@dataclass(frozen=True)
class Message(Serializable):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class AppMetrics(Serializable):
    latency_ms: float | None = None
    ttft_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None


Content = str | Mapping[str, Any] | tuple[ContentPart, ...]


@dataclass(frozen=True)
class EvalCase(Serializable):
    """One item to evaluate; every field but ``id`` is optional (metrics declare their needs)."""

    id: str
    input: Content | None = None
    output: Content | None = None
    output_content_type: str = "text/plain"
    artifacts: tuple[Artifact, ...] = ()
    expected: Any = None
    contexts: tuple[Context, ...] = ()
    conversation: tuple[Message, ...] = ()
    trajectory: Session | None = None
    app_metrics: AppMetrics | None = None
    language: str | None = None  # BCP-47
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        """Whether input ``name`` (a ``MethodSpec`` input name) is present."""
        if name == "tx":
            return self.trajectory is not None and bool(self.trajectory.tx_steps())
        value = getattr(self, name, None)
        if value is None:
            return False
        # An empty output ("") or an empty expectation ([] = "no tool calls expected") is real
        # data; only collections of evidence (contexts, conversation, artifacts) must be non-empty.
        if name in ("contexts", "conversation", "artifacts"):
            return len(value) > 0
        return True


def content_text(content: Content | None) -> str | None:
    """Best-effort plain-text view of case content."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        import json

        return json.dumps(content, sort_keys=True, ensure_ascii=False)
    texts = [p.text for p in content if p.text]
    return "\n".join(texts) if texts else None
