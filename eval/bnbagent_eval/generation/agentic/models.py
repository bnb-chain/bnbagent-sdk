"""Value types of the agentic generator (ETK ``generator_builtin.agentic.models``, as dataclasses).

LLM-facing shapes keep ETK's camelCase wire names (``toolCalls``, ``toolsInvolved``) because the
prompts are a contract on them; Python attributes are snake_case.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Union

from ...exceptions import JudgeParseError
from ...types.trajectory import ToolCall

DEFAULT_TURN_COUNTS = (1, 2, 3)
DEFAULT_DIFFICULTY = ("easy", "medium", "hard")
DEFAULT_OUTCOMES = ("happy_path", "refusal", "clarification")
STRATEGIES = (
    "rephrase",
    "parameter_variation",
    "multi_turn_extension",
    "edge_case",
    "negative_case",
)
SCENARIO_SOURCES = ("gap_fill", "focus_area", "failure_pattern")
AUGMENTATION = "augmentation"

# A flat list (single turn) or one list per turn (multi turn); both shapes are preserved.
ToolCalls = Union[tuple[ToolCall, ...], tuple[tuple[ToolCall, ...], ...]]  # noqa: UP007


@dataclass(frozen=True)
class ToolSpec:
    """A tool of the agent under test; ``parameters`` is a JSON Schema object (or None)."""

    name: str
    description: str = ""
    parameters: Mapping[str, Any] | None = None

    @classmethod
    def from_any(cls, obj: ToolSpec | Mapping[str, Any]) -> ToolSpec:
        """From a ToolSpec, ``{name, description, parameters}``, an MCP / Anthropic tool
        (``inputSchema`` / ``input_schema``) or an OpenAI ``{"type": "function", ...}`` tool."""
        if isinstance(obj, ToolSpec):
            return obj
        if not isinstance(obj, Mapping):
            raise TypeError(f"not a tool definition: {obj!r}")
        if isinstance(obj.get("function"), Mapping):
            obj = obj["function"]
        name = obj.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"tool definition without a name: {obj!r}")
        params = None
        for key in ("parameters", "input_schema", "inputSchema", "args_schema"):
            if obj.get(key) is not None:
                params = obj[key]
                break
        return cls(name=name, description=str(obj.get("description") or ""), parameters=params)

    def to_prompt(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.parameters is not None:
            out["parameters"] = dict(self.parameters)
        return out


@dataclass(frozen=True)
class TaxonomyConfig:
    """The three caller-controlled axes (ETK ``TaxonomyAxesOverride`` resolved to defaults)."""

    turn_counts: tuple[int, ...] = DEFAULT_TURN_COUNTS
    difficulty: tuple[str, ...] = DEFAULT_DIFFICULTY
    outcomes: tuple[str, ...] = DEFAULT_OUTCOMES

    def __post_init__(self) -> None:
        if not self.turn_counts or any(int(t) < 1 for t in self.turn_counts):
            raise ValueError(f"turn_counts must be non-empty and >= 1, got {self.turn_counts}")
        if not self.difficulty or not self.outcomes:
            raise ValueError("difficulty and outcomes cannot be empty")

    @classmethod
    def from_any(cls, obj: TaxonomyConfig | Mapping[str, Any] | None) -> TaxonomyConfig:
        if obj is None:
            return cls()
        if isinstance(obj, TaxonomyConfig):
            return obj
        get = obj.get

        def pick(snake: str, camel: str, default: tuple[Any, ...]) -> tuple[Any, ...]:
            value = get(snake, get(camel))
            return tuple(value) if value is not None else default

        return cls(
            turn_counts=tuple(
                int(t) for t in pick("turn_counts", "turnCounts", DEFAULT_TURN_COUNTS)
            ),
            difficulty=pick("difficulty", "difficulty", DEFAULT_DIFFICULTY),
            outcomes=pick("outcomes", "outcomes", DEFAULT_OUTCOMES),
        )


@dataclass(frozen=True)
class Taxonomy:
    """The coverage axes scenarios are sampled across, plus the gaps the planner noted."""

    tools: tuple[str, ...] = ()
    tool_combinations: tuple[tuple[str, ...], ...] = ((),)
    turn_counts: tuple[int, ...] = DEFAULT_TURN_COUNTS
    difficulty: tuple[str, ...] = DEFAULT_DIFFICULTY
    topics: tuple[str, ...] = ("general",)
    outcomes: tuple[str, ...] = DEFAULT_OUTCOMES
    gaps: tuple[str, ...] = ()

    def to_prompt(self) -> dict[str, Any]:
        return {
            "axes": {
                "tools": list(self.tools),
                "toolCombinations": [list(c) for c in self.tool_combinations],
                "turnCounts": list(self.turn_counts),
                "difficulty": list(self.difficulty),
                "topics": list(self.topics),
                "outcomes": list(self.outcomes),
            },
            "gaps": list(self.gaps),
        }

    @classmethod
    def from_prompt(cls, obj: Mapping[str, Any], axes: TaxonomyConfig) -> Taxonomy:
        """From the model's reply; the caller's axes always win over the echoed ones (ETK)."""
        raw = obj.get("axes") or {}
        combos = raw.get("toolCombinations", raw.get("tool_combinations")) or [[]]
        return cls(
            tools=tuple(str(t) for t in raw.get("tools") or ()),
            tool_combinations=tuple(tuple(str(t) for t in c) for c in combos),
            turn_counts=axes.turn_counts,
            difficulty=axes.difficulty,
            topics=tuple(str(t) for t in raw.get("topics") or ()) or ("general",),
            outcomes=axes.outcomes,
            gaps=tuple(str(g) for g in obj.get("gaps") or ()),
        )

    @property
    def config(self) -> TaxonomyConfig:
        return TaxonomyConfig(self.turn_counts, self.difficulty, self.outcomes)


@dataclass(frozen=True)
class Scenario:
    """A planned test case, before it is expanded into concrete turns."""

    id: str
    description: str
    turns: int
    difficulty: str
    outcome: str
    tools_involved: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    type: str = "single_turn"
    source: str = "gap_fill"
    priority: int = 5

    def to_prompt(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "type": self.type,
            "turns": self.turns,
            "toolsInvolved": list(self.tools_involved),
            "difficulty": self.difficulty,
            "topics": list(self.topics),
            "outcome": self.outcome,
            "source": self.source,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class Provenance:
    source: str
    scenario_id: str
    plan_axis: str | None = None
    augmentation_strategy: str | None = None
    parent_scenario_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "source": self.source,
            "scenario_id": self.scenario_id,
            "plan_axis": self.plan_axis,
            "augmentation_strategy": self.augmentation_strategy,
            "parent_scenario_id": self.parent_scenario_id,
        }
        return {k: v for k, v in out.items() if v is not None}


def flatten_tool_calls(tool_calls: ToolCalls | None) -> list[ToolCall]:
    """One list of calls from the flat (single-turn) or per-turn (multi-turn) shape."""
    if not tool_calls:
        return []
    if isinstance(tool_calls[0], tuple):
        return [c for turn in tool_calls for c in turn]  # type: ignore[union-attr]
    return list(tool_calls)  # type: ignore[arg-type]


def calls_to_wire(tool_calls: ToolCalls | None, key: str = "args") -> list[Any] | None:
    if tool_calls is None:
        return None

    def one(c: ToolCall) -> dict[str, Any]:
        return {"name": c.name, key: dict(c.arguments)}

    if tool_calls and isinstance(tool_calls[0], tuple):
        return [[one(c) for c in turn] for turn in tool_calls]  # type: ignore[union-attr]
    return [one(c) for c in tool_calls]  # type: ignore[arg-type]


def _call(item: Any) -> ToolCall:
    if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
        raise JudgeParseError(f"invalid tool call {json.dumps(item, default=str)[:200]}")
    args = item.get("args", item.get("arguments", item.get("parameters"))) or {}
    if not isinstance(args, Mapping):
        raise JudgeParseError(f"tool call {item['name']}: args must be an object")
    return ToolCall(name=item["name"], arguments=dict(args))


def calls_from_wire(raw: Any) -> ToolCalls | None:
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise JudgeParseError("toolCalls must be an array")
    if raw and all(isinstance(t, Sequence) and not isinstance(t, (str, Mapping)) for t in raw):
        return tuple(tuple(_call(c) for c in turn) for turn in raw)
    if any(isinstance(t, Sequence) and not isinstance(t, (str, Mapping)) for t in raw):
        raise JudgeParseError("toolCalls mixes flat calls and per-turn arrays")
    return tuple(_call(c) for c in raw)


@dataclass(frozen=True)
class SeedExample:
    """A few-shot example steering generation style."""

    input: str | tuple[str, ...]
    response: str = ""
    topics: tuple[str, ...] | None = None
    tool_calls: ToolCalls | None = None

    @classmethod
    def from_any(cls, obj: SeedExample | Mapping[str, Any]) -> SeedExample:
        if isinstance(obj, SeedExample):
            return obj
        expected = obj.get("expected") or {}
        inp = obj.get("input")
        return cls(
            input=tuple(inp) if isinstance(inp, list) else str(inp),
            response=str(expected.get("response", "")),
            topics=tuple(expected["topics"]) if expected.get("topics") else None,
            tool_calls=calls_from_wire(expected.get("toolCalls", expected.get("tool_calls"))),
        )

    def to_prompt(self) -> dict[str, Any]:
        expected: dict[str, Any] = {"response": self.response}
        if self.topics is not None:
            expected["topics"] = list(self.topics)
        if self.tool_calls is not None:
            expected["toolCalls"] = calls_to_wire(self.tool_calls)
        inp = list(self.input) if isinstance(self.input, tuple) else self.input
        return {"input": inp, "expected": expected}


@dataclass(frozen=True)
class GeneratedCase:
    """A generated test case with provenance (ETK ``GeneratedTestCase``)."""

    input: str | tuple[str, ...]
    response: str
    topics: tuple[str, ...]
    tool_calls: ToolCalls | None
    provenance: Provenance
    scenario: Scenario | None = None
    requires_review: bool = False

    def flat_calls(self) -> list[ToolCall]:
        return flatten_tool_calls(self.tool_calls)

    def to_prompt(self) -> dict[str, Any]:
        expected: dict[str, Any] = {"response": self.response, "topics": list(self.topics)}
        if self.tool_calls is not None:
            expected["toolCalls"] = calls_to_wire(self.tool_calls)
        inp = list(self.input) if isinstance(self.input, tuple) else self.input
        return {
            "input": inp,
            "expected": expected,
            "context": {"provenance": self.provenance.to_dict()},
        }


@dataclass(frozen=True)
class RawCase:
    """A case as the model emits it, before provenance is attached."""

    input: str | tuple[str, ...]
    response: str
    topics: tuple[str, ...]
    tool_calls: ToolCalls | None

    @classmethod
    def from_prompt(cls, obj: Mapping[str, Any]) -> RawCase:
        inp = obj["input"]
        if isinstance(inp, list):
            if not all(isinstance(t, str) and t.strip() for t in inp):
                raise JudgeParseError("input turns must be non-empty strings")
            inp = tuple(inp)
        expected = obj["expected"]
        return cls(
            input=inp,
            response=str(expected["response"]),
            topics=tuple(str(t) for t in expected.get("topics") or ()),
            tool_calls=calls_from_wire(expected.get("toolCalls", expected.get("tool_calls"))),
        )


# ------------------------------------------------------------------------------- traces


@dataclass(frozen=True)
class ExtractedTurn:
    user_message: str
    assistant_message: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ExtractedConversation:
    """A conversation reconstructed from one trace session; ``outcome`` is
    ``success`` / ``failure`` / ``abandonment`` (from step status, not content)."""

    session_id: str
    trace_id: str
    turns: tuple[ExtractedTurn, ...]
    outcome: str

    def tool_sequence(self) -> list[str]:
        return [c.name for turn in self.turns for c in turn.tool_calls]


@dataclass(frozen=True)
class TraceCoverage:
    observed_tools: tuple[str, ...] = ()
    observed_combinations: tuple[tuple[str, ...], ...] = ()
    success: int = 0
    failure: int = 0
    abandonment: int = 0


@dataclass(frozen=True)
class TraceSampling:
    """Retention of seed conversations (ETK ``TraceConfig`` defaults)."""

    max_conversations: int = 500
    error_sample_rate: float = 1.0
    success_sample_rate: float = 0.1
    abandonment_sample_rate: float = 0.5
    max_per_cluster: Mapping[str, int] = field(
        default_factory=lambda: {"failure": 3, "abandonment": 3, "success": 2}
    )


def tool_specs(tools: Iterable[ToolSpec | Mapping[str, Any]]) -> list[ToolSpec]:
    specs = [ToolSpec.from_any(t) for t in tools]
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate tool names in {names}")
    return specs
