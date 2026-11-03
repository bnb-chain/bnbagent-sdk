"""Helpers shared by text metrics."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...types.case import EvalCase, content_text
from ..base import Kind, MethodSpec, Outcome

OR_DELIMITER = "<OR>"


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    if isinstance(value, tuple):
        return content_text(value) or ""
    return str(value)


def references(expected: Any, delimiter: str | None = OR_DELIMITER) -> list[str]:
    """Reference answers: a list, or a string split on ``<OR>``. Blank alternatives (e.g. from a
    trailing ``<OR>``) are dropped unless every alternative is blank."""
    if isinstance(expected, (list, tuple)) and all(isinstance(x, str) for x in expected):
        refs = list(expected)
    elif isinstance(expected, Mapping) and "answers" in expected:
        refs = [as_text(x) for x in expected["answers"]]
    else:
        text = as_text(expected)
        refs = text.split(delimiter) if delimiter else [text]
    refs = [r.strip() for r in refs]
    return [r for r in refs if r] or [""]


def output_text(case: EvalCase) -> str:
    return as_text(case.output)


def best_of(refs: Sequence[str], score: Callable[[str], float]) -> tuple[float, int]:
    """Max score over references, and the index of the best reference."""
    scores = [score(r) for r in refs]
    best = max(range(len(scores)), key=lambda i: scores[i])
    return scores[best], best


def rule_method(*required: str, **kw: Any) -> MethodSpec:
    return MethodSpec("rule", Kind.RULE, required_inputs=frozenset(required), **kw)


def ref_outcome(score: float, refs: Sequence[str], best: int, **details: Any) -> Outcome:
    d = dict(details)
    if len(refs) > 1:
        d["best_reference"] = best
    return Outcome(score=score, details=d)
