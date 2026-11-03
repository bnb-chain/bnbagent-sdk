"""Serializable description of the agent under test (live targets are built in the engine)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._serde import Serializable

TargetKind = Literal["callable", "http", "a2a", "mcp", "erc8004"]


@dataclass(frozen=True)
class TargetRef(Serializable):
    kind: TargetKind
    uri: str | None = (
        None  # URL, "module:attr" for callable, "<chain_id>:<registry>:<agent_id>" for erc8004
    )
    config: Mapping[str, Any] = field(
        default_factory=dict
    )  # headers, timeout_s, stream, request/response mapping
    simulated_user: Mapping[str, Any] | None = None
