"""Judge-model protocol and request/response value types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..types.judge import JudgeConfig
from ..types.trajectory import Usage


@dataclass(frozen=True)
class JudgeRequest:
    system: str
    user: str
    output_schema: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )  # metric_id, prompt_version, sample_idx


@dataclass(frozen=True)
class JudgeResponse:
    text: str
    parsed: Mapping[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: float = 0.0
    source: str = "live"  # "live" or the cache name ("cache:<id>")


@runtime_checkable
class JudgeModel(Protocol):
    config: JudgeConfig

    async def complete(self, request: JudgeRequest) -> JudgeResponse: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
