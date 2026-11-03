"""``custom.python``: user-registered Python callables (ETK function scorers / Scorer classes).

Referenced by import path (``params.fn = "package.module:attr"``) or by a name registered with
:func:`register_python_metric`. Local mode only: the eval service denies this method.

The callable is ``fn(case)`` or ``fn(case, args)`` (sync or async) and returns a float, a bool,
a mapping (``score``, ``value``, ``unit``, ``reason``, ``details``), a :class:`MetricResult`
(its score, value, unit, reason and details are used) or an :class:`Outcome`.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from ...types.case import EvalCase
from ...types.result import MetricResult
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register

_FUNCTIONS: dict[str, Callable[..., Any]] = {}


def register_python_metric(name: str, fn: Callable[..., Any]) -> None:
    """Make ``fn`` available to suites as ``{"id": "custom.python", "params": {"fn": name}}``."""
    if ":" in name:
        raise ValueError("registered names must not contain ':' (reserved for import paths)")
    _FUNCTIONS[name] = fn


def _load(ref: str) -> Callable[..., Any]:
    if ref in _FUNCTIONS:
        return _FUNCTIONS[ref]
    if ":" not in ref:
        raise LookupError(f"no python metric registered as {ref!r}")
    module_name, _, attr = ref.partition(":")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"{ref} is not callable")
    return obj


@register
class PythonMetric(BaseMetric):
    spec = MetricSpec(
        id="custom.python",
        category="custom",
        description="A user-registered Python callable.",
        methods=(MethodSpec("rule", Kind.RULE, service_allowed=False),),
        params_schema={
            "type": "object",
            "required": ["fn"],
            "properties": {"fn": {"type": "string", "minLength": 1}, "args": {"type": "object"}},
            "additionalProperties": False,
        },
        default_threshold=None,
        sources=("etk:function-scorers",),
    )

    async def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        fn = _load(ctx.params["fn"])
        args = dict(ctx.params.get("args") or {})
        n_params = len(inspect.signature(fn).parameters)
        result = fn(case, args) if n_params >= 2 else fn(case)
        if inspect.isawaitable(result):
            result = await result
        return _to_outcome(result)


def _to_outcome(result: Any) -> Outcome:
    if isinstance(result, Outcome):
        return result
    if isinstance(result, MetricResult):  # a scorer may return float, dict or MetricResult
        return Outcome(
            score=result.score,
            value=result.value,
            unit=result.unit,
            reason=result.reason,
            details=dict(result.details),
        )
    if isinstance(result, (bool, int, float)):
        return Outcome(score=float(result))
    if isinstance(result, Mapping):
        unknown = set(result) - {"score", "value", "unit", "reason", "details"}
        if unknown:
            raise ValueError(f"unexpected keys from python metric: {sorted(unknown)}")
        score = result.get("score")
        return Outcome(
            score=None if score is None else float(score),
            value=result.get("value"),
            unit=result.get("unit"),
            reason=result.get("reason"),
            details=dict(result.get("details") or {}),
        )
    raise TypeError(f"python metric returned unsupported type {type(result).__name__}")
