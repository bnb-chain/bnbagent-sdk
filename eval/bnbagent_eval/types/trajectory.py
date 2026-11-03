"""Normalized agent execution: Session -> Trace -> Step."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._serde import Serializable


@dataclass(frozen=True)
class ToolCall(Serializable):
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass(frozen=True)
class ToolResult(Serializable):
    content: Any = None
    error: str | None = None
    call_id: str | None = None


@dataclass(frozen=True)
class Usage(Serializable):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class DecodedCall(Serializable):
    contract: str | None = None
    function: str | None = None
    args: Mapping[str, Any] = field(default_factory=dict)


# All Step subclasses are kw_only: the base fields have defaults.
@dataclass(frozen=True, kw_only=True)
class _StepBase(Serializable):
    id: str
    parent_id: str | None = None
    agent_id: str | None = None
    start_ms: float | None = None
    end_ms: float | None = None
    usage: Usage | None = None
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms


@dataclass(frozen=True, kw_only=True)
class LlmStep(_StepBase):
    kind: Literal["llm"] = "llm"
    model: str | None = None
    messages: tuple[Mapping[str, Any], ...] = ()
    response: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ToolStep(_StepBase):
    kind: Literal["tool"] = "tool"
    call: ToolCall
    result: ToolResult | None = None


@dataclass(frozen=True, kw_only=True)
class AgentStep(_StepBase):
    kind: Literal["agent"] = "agent"
    system_prompt: str | None = None
    user_prompt: str | None = None
    response: str | None = None
    available_tools: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, kw_only=True)
class DelegationStep(_StepBase):
    kind: Literal["delegation"] = "delegation"
    from_agent: str | None = None
    to_agent: str | None = None


@dataclass(frozen=True, kw_only=True)
class HandoffStep(_StepBase):
    kind: Literal["handoff"] = "handoff"
    from_agent: str | None = None
    to_agent: str | None = None


@dataclass(frozen=True, kw_only=True)
class TxStep(_StepBase):
    """An on-chain transaction performed (or proposed) by the agent."""

    kind: Literal["tx"] = "tx"
    chain_id: int
    to: str
    value: int = 0
    calldata: str = "0x"
    intent: str | None = None
    decoded: DecodedCall | None = None
    tx_hash: str | None = None
    from_address: str | None = None
    status: int | None = None  # receipt status: 1 success, 0 reverted
    block_number: int | None = None
    gas_used: int | None = None
    logs: tuple[Mapping[str, Any], ...] = ()


Step = LlmStep | ToolStep | AgentStep | DelegationStep | HandoffStep | TxStep


@dataclass(frozen=True)
class Trace(Serializable):
    id: str
    steps: tuple[Step, ...] = ()


@dataclass(frozen=True)
class Session(Serializable):
    id: str
    traces: tuple[Trace, ...] = ()
    warnings: tuple[str, ...] = ()

    def steps(self) -> Iterator[Step]:
        for trace in self.traces:
            yield from trace.steps

    def tool_steps(self) -> list[ToolStep]:
        return [s for s in self.steps() if isinstance(s, ToolStep)]

    def tool_calls(self) -> list[ToolCall]:
        """Tool calls in execution order (from tool steps, else from LLM steps)."""
        calls = [s.call for s in self.tool_steps()]
        if calls:
            return calls
        return [c for s in self.steps() if isinstance(s, LlmStep) for c in s.tool_calls]

    def tx_steps(self) -> list[TxStep]:
        return [s for s in self.steps() if isinstance(s, TxStep)]

    def final_response(self) -> str | None:
        for step in reversed(list(self.steps())):
            if isinstance(step, (AgentStep, LlmStep)) and step.response:
                return step.response
        return None
