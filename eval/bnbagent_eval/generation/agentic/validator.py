"""JSON-Schema validation of generated tool calls (ETK ``agentic.tool_validator``).

Uses the package's dependency-free subset validator (``bnbagent_eval._jsonschema``) instead of
``jsonschema``: unknown tools, non-object arguments and schema violations (types, required,
enum, pattern, bounds, ``additionalProperties``) are reported, all at once, so one repair turn
can fix them all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..._jsonschema import _TYPES, validate
from ...exceptions import JudgeParseError
from ...types.trajectory import ToolCall
from .._llm import GenerationError
from .models import ToolCalls, ToolSpec, flatten_tool_calls

__all__ = ["ToolValidator", "check_tool_calls"]

_COMPOSITES = ("anyOf", "oneOf", "allOf")


def _schema_problems(schema: Any, path: str) -> list[str]:
    if not isinstance(schema, Mapping):
        return [f"{path}: a schema must be an object"]
    problems: list[str] = []
    stype = schema.get("type")
    if stype is not None:
        names = [stype] if isinstance(stype, str) else stype
        if not isinstance(names, list) or any(n not in _TYPES for n in names):
            problems.append(f"{path}: unknown type {stype!r}")
    for key, sub in (schema.get("properties") or {}).items():
        problems.extend(_schema_problems(sub, f"{path}.properties.{key}"))
    if isinstance(schema.get("items"), Mapping):
        problems.extend(_schema_problems(schema["items"], f"{path}.items"))
    for key in _COMPOSITES:
        for i, sub in enumerate(schema.get(key) or ()):
            problems.extend(_schema_problems(sub, f"{path}.{key}[{i}]"))
    required = schema.get("required")
    if required is not None and not (
        isinstance(required, list) and all(isinstance(r, str) for r in required)
    ):
        problems.append(f"{path}: required must be a list of names")
    return problems


class ToolValidator:
    """Validates tool calls against a fixed set of tool definitions."""

    def __init__(self, tools: Iterable[ToolSpec]):
        self._tools = {t.name: t for t in tools}

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def check_schemas(self) -> None:
        """Reject malformed ``parameters`` schemas before any judge call (ETK)."""
        for name, tool in self._tools.items():
            if tool.parameters is None:
                continue
            problems = _schema_problems(tool.parameters, "$")
            if tool.parameters.get("type", "object") != "object":
                problems.append("$: tool parameters must be an object schema")
            if problems:
                raise GenerationError(
                    f"Tool {name!r} has an invalid JSON Schema in its parameters: {problems[0]}"
                )

    def validate(self, calls: Iterable[ToolCall]) -> list[str]:
        """One message per problem (empty if every call is valid)."""
        errors: list[str] = []
        for call in calls:
            tool = self._tools.get(call.name)
            if tool is None:
                errors.append(f"Unknown tool: {call.name}")
                continue
            if not isinstance(call.arguments, Mapping):
                errors.append(f"{call.name}: arguments must be an object")
                continue
            if tool.parameters is None:
                continue
            for problem in sorted(validate(dict(call.arguments), tool.parameters)):
                errors.append(f"{call.name}: {problem}")
        return errors


def check_tool_calls(
    validator: ToolValidator, tool_calls: ToolCalls | None, tools: list[ToolSpec] | None
) -> list[ToolCall]:
    """Flatten and validate a case's tool calls; raise :class:`JudgeParseError` listing every
    failure (the message becomes the repair prompt). Calls are checked only when tools exist."""
    flat = flatten_tool_calls(tool_calls)
    if tools and flat:
        errors = validator.validate(flat)
        if errors:
            raise JudgeParseError(f"Tool validation failed: {'; '.join(errors)}")
    return flat
