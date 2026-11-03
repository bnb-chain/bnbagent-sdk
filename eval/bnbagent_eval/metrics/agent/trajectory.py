"""Deterministic trajectory metrics: tool-trajectory match and trajectory statistics.

``agent.tool_trajectory_match`` follows Google ADK / AgentSight semantics:
- ``exact``: the actual tool calls equal the expected ones, in order;
- ``in_order``: the expected calls appear in the actual calls as a subsequence;
- ``any_order``: every expected call appears in the actual calls (multiset).
Arguments are compared ``exact`` (AgentSight default), as a ``subset`` of the actual arguments,
or ignored; integral floats equal ints (AgentSight compares with ``==``). With ``expected`` as one
list of calls per invocation (trace), the score is the mean of the per-invocation 0/1 matches
(AgentSight); a flat list is matched against the whole session.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...analysis.confidence import max_depth
from ...hashing import canonical_json
from ...types.case import EvalCase
from ...types.trajectory import DelegationStep, HandoffStep, Session, ToolCall, ToolStep
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register


def _raw_calls(expected: Any) -> Any:
    return expected.get("tool_calls") if isinstance(expected, Mapping) else expected


def per_invocation(expected: Any) -> bool:
    """``expected`` holds one list of tool calls per invocation (AgentSight's trajectory)."""
    raw = _raw_calls(expected)
    return (
        isinstance(raw, Sequence)
        and not isinstance(raw, (str, bytes))
        and bool(raw)
        and all(isinstance(x, Sequence) and not isinstance(x, (str, bytes)) for x in raw)
    )


def expected_calls(expected: Any) -> list[ToolCall]:
    """Expected tool calls from ``expected`` (a list, or a mapping with ``tool_calls``)."""
    raw = _raw_calls(expected)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("expected must be a list of tool calls or {'tool_calls': [...]}")
    calls = []
    for item in raw:
        if isinstance(item, ToolCall):
            calls.append(item)
        elif isinstance(item, Mapping) and "name" in item:
            args = item.get("arguments", item.get("args", item.get("parameters", {}))) or {}
            calls.append(ToolCall(name=str(item["name"]), arguments=dict(args)))
        else:
            raise ValueError(f"invalid expected tool call: {item!r}")
    return calls


def _num(value: Any) -> Any:
    """Integral floats and booleans as ints, recursively (AgentSight compares arguments with
    ``==``, where ``1 == 1.0 == True``)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {k: _num(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_num(v) for v in value]
    return value


def _same(a: Any, b: Any) -> bool:
    return canonical_json(_num(a)) == canonical_json(_num(b))


def _args_match(expected: Mapping[str, Any], actual: Mapping[str, Any], mode: str) -> bool:
    if mode == "ignore":
        return True
    if mode == "subset":
        return all(k in actual and _same(actual[k], v) for k, v in expected.items())
    return _same(dict(expected), dict(actual))


def call_matches(expected: ToolCall, actual: ToolCall, args_mode: str) -> bool:
    return expected.name == actual.name and _args_match(
        expected.arguments, actual.arguments, args_mode
    )


def unmatched(
    n_expected: int, actual: Sequence[Any], match_type: str, pred: Callable[[int, Any], bool]
) -> list[int]:
    """Indices of the expected items left unmatched (``pred(i, actual_item)``).

    ``exact``: position by position (a length difference is not reported here); ``in_order``:
    an ordered subsequence (greedy earliest match, which is optimal); ``any_order``: a maximum
    one-to-one assignment (augmenting paths), so a loose predicate (argument subset, tolerance)
    cannot consume an actual item another expected item needs. With an equivalence predicate
    (exact arguments) this equals AgentSight's first-match scan.
    """
    if match_type == "exact":
        return [i for i in range(n_expected) if i >= len(actual) or not pred(i, actual[i])]
    if match_type == "in_order":
        j, missing = 0, []
        for i in range(n_expected):
            while j < len(actual) and not pred(i, actual[j]):
                j += 1
            if j == len(actual):
                missing.append(i)
            else:
                j += 1
        return missing
    candidates = [[k for k, a in enumerate(actual) if pred(i, a)] for i in range(n_expected)]
    owner: dict[int, int] = {}  # actual index -> expected index

    def assign(i: int, seen: set[int]) -> bool:
        for k in candidates[i]:
            if k not in seen:
                seen.add(k)
                if k not in owner or assign(owner[k], seen):
                    owner[k] = i
                    return True
        return False

    matched = {i for i in range(n_expected) if assign(i, set())}
    return [i for i in range(n_expected) if i not in matched]


def match_trajectory(
    expected: list[ToolCall], actual: list[ToolCall], match_type: str, args_mode: str
) -> tuple[bool, list[int]]:
    """Return (matched, indices of expected calls that were not found)."""
    missing = unmatched(
        len(expected), actual, match_type, lambda i, a: call_matches(expected[i], a, args_mode)
    )
    ok = not missing and (match_type != "exact" or len(expected) == len(actual))
    return ok, missing


@register
class ToolTrajectoryMatch(BaseMetric):
    spec = MetricSpec(
        id="agent.tool_trajectory_match",
        category="agent",
        description="The agent's tool calls match the expected tool calls (exact / in order / any "
        "order; arguments exact / subset / ignored).",
        methods=(
            MethodSpec("rule", Kind.RULE, required_inputs=frozenset({"trajectory", "expected"})),
        ),
        params_schema={
            "type": "object",
            "properties": {
                "match_type": {"enum": ["exact", "in_order", "any_order"]},
                "compare_args": {"enum": ["exact", "subset", "ignore"]},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        sources=(
            "agentsight:AgentToolTrajectoryMatchEvaluator",
            "google-adk:tool_trajectory",
            "ragas:tool_call_accuracy",
            "deepeval:tool_correctness",
        ),
        # v2: boolean arguments equal 1 / 0 (AgentSight ``==``)
        # v3: any_order is a maximum one-to-one assignment (not first match)
        version="3",
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        match_type = ctx.params.get("match_type", "exact")
        args_mode = ctx.params.get("compare_args", "exact")
        if per_invocation(case.expected):
            return self._per_invocation(case, match_type, args_mode)
        expected = expected_calls(case.expected)
        actual = case.trajectory.tool_calls()
        ok, missing = match_trajectory(expected, actual, match_type, args_mode)
        return Outcome(
            score=float(ok),
            reason=None if ok else f"{len(missing)} expected call(s) not matched",
            details={
                "match_type": match_type,
                "compare_args": args_mode,
                "expected": len(expected),
                "actual": len(actual),
                "missing": [expected[i].name for i in missing],
            },
        )

    def _per_invocation(self, case: EvalCase, match_type: str, args_mode: str) -> Outcome:
        """AgentSight: score each (actual trace, expected invocation) pair 0/1, then the mean
        (``zip(..., strict=False)``: extra invocations on either side are ignored)."""
        expected = [expected_calls(x) for x in _raw_calls(case.expected)]
        traces = case.trajectory.traces
        results = []
        for trace, exp in zip(traces, expected, strict=False):
            actual = Session(id=trace.id, traces=(trace,)).tool_calls()
            ok, missing = match_trajectory(exp, actual, match_type, args_mode)
            results.append({"score": float(ok), "missing": [exp[i].name for i in missing]})
        details = {
            "match_type": match_type,
            "compare_args": args_mode,
            "invocations": len(results),
            "per_invocation": results,
        }
        if not results:
            return Outcome(score=None, reason="no invocation to compare", details=details)
        score = sum(r["score"] for r in results) / len(results)
        return Outcome(score=score, details=details)


def _call_key(call: ToolCall) -> tuple[str, str]:
    try:
        return call.name, canonical_json(dict(call.arguments))
    except ValueError:  # e.g. NaN arguments (as analysis.confidence)
        return call.name, str(call.arguments)


def _has_cycle(edges: list[tuple[str, str]]) -> bool:
    graph: dict[str, set[str]] = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in graph.get(node, ()):
            if state.get(nxt) == 1 or (state.get(nxt) is None and visit(nxt)):
                return True
        state[node] = 2
        return False

    return any(state.get(n) is None and visit(n) for n in list(graph))


@register
class TrajectoryStats(BaseMetric):
    spec = MetricSpec(
        id="agent.trajectory_stats",
        category="agent",
        description="Informational trajectory counts (steps, tool calls, repeated calls, errors, "
        "depth, delegation cycles); scored only when limits are configured.",
        methods=(MethodSpec("rule", Kind.RULE, required_inputs=frozenset({"trajectory"})),),
        params_schema={
            "type": "object",
            "properties": {
                k: {"type": "integer", "minimum": 0}
                for k in (
                    "max_steps",
                    "max_tool_calls",
                    "max_repeated_tool_calls",
                    "max_errors",
                    "max_depth",
                )
            },
            "additionalProperties": False,
        },
        default_threshold=None,
        sources=("agentsight:SignalExtractor", "agentsight:insights"),
        version="2",  # v2: a violated limit sets passed=False
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        steps = list(case.trajectory.steps())
        calls = case.trajectory.tool_calls()
        counts = Counter(_call_key(c) for c in calls)
        edges = [
            (s.from_agent, s.to_agent)
            for s in steps
            if isinstance(s, (DelegationStep, HandoffStep)) and s.from_agent and s.to_agent
        ]
        stats = {
            "steps": len(steps),
            "tool_calls": len(calls),
            "repeated_tool_calls": sum(n - 1 for n in counts.values() if n > 1),
            # each step counts once (AgentSight counts each failed tool span once)
            "errors": sum(
                1
                for s in steps
                if s.error or (isinstance(s, ToolStep) and s.result and s.result.error)
            ),
            # roots have depth 1 (AgentSight SignalExtractor._compute_max_depth)
            "depth": max_depth(steps),
            "delegation_cycle": _has_cycle(edges),
        }
        limits = {k: v for k, v in ctx.params.items() if k.startswith("max_")}
        if not limits:
            return Outcome(score=None, details=stats)
        violations = [k for k, v in limits.items() if stats[k.removeprefix("max_")] > v]
        return Outcome(
            score=0.0 if violations else 1.0,
            details={**stats, "violations": violations},
            reason=("exceeds " + ", ".join(violations)) if violations else None,
            passed=not violations,  # no default threshold: a violated limit fails the metric
        )
