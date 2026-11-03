"""Golden tables for ops.* and deterministic agent.* metrics."""

from __future__ import annotations

import pytest

from bnbagent_eval.metrics.ops.consistency import Node, structural_similarity, tree_edit_distance
from bnbagent_eval.types import (
    AppMetrics,
    DelegationStep,
    EvalCase,
    LlmStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    Usage,
)

from ._util import case, run_metric


def session(*steps) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=tuple(steps)),))


TRAJ = session(
    LlmStep(
        id="1", model="m1", start_ms=0, end_ms=400, usage=Usage(input_tokens=100, output_tokens=50)
    ),
    ToolStep(
        id="2",
        parent_id="1",
        call=ToolCall(name="search", arguments={"q": "bnb"}),
        start_ms=400,
        end_ms=700,
        result=ToolResult(content="ok"),
    ),
    ToolStep(
        id="3",
        parent_id="2",
        call=ToolCall(name="search", arguments={"q": "bnb"}),
        start_ms=700,
        end_ms=800,
        result=ToolResult(error="rate limited"),
    ),
    LlmStep(
        id="4",
        model="m1",
        start_ms=800,
        end_ms=1000,
        usage=Usage(input_tokens=200, output_tokens=80),
    ),
)


@pytest.mark.parametrize(
    ("value", "cfg", "score", "passed"),
    [
        (50, {"value_max": 100}, 1.0, True),
        (150, {"value_max": 100}, 0.0, False),
        (150, {"value_target": 100, "value_max": 200}, 0.5, True),
        (90, {"value_target": 100, "value_max": 200}, 1.0, True),
        (250, {"value_target": 100, "value_max": 200}, 0.0, False),
        (50, {}, None, None),
    ],
)
async def test_latency_scoring(value, cfg, score, passed) -> None:
    r = await run_metric(
        "ops.latency", EvalCase(id="c", app_metrics=AppMetrics(latency_ms=value)), **cfg
    )
    assert (r.score, r.passed, r.value, r.unit) == (score, passed, value, "ms")


@pytest.mark.parametrize(
    ("component", "value"),
    [
        ("total", 1000),
        ("inference", 600),
        ("tool", 400),
        ("other", 0),
    ],
)
async def test_latency_components_from_trajectory(component, value) -> None:
    r = await run_metric(
        "ops.latency", EvalCase(id="c", trajectory=TRAJ), {"component": component}
    )
    assert r.value == value


async def test_latency_missing_input_is_skipped() -> None:
    r = await run_metric("ops.latency", case("x"), value_max=10)
    assert r.status == "skipped" and r.error.code == "missing_input"


async def test_cost_and_tokens() -> None:
    reported = await run_metric(
        "ops.cost", EvalCase(id="c", app_metrics=AppMetrics(cost_usd=0.02)), value_max=0.05
    )
    assert reported.value == 0.02 and reported.passed is True
    table = {"m1": {"input_per_1k": 1.0, "output_per_1k": 2.0}}
    priced = await run_metric(
        "ops.cost", EvalCase(id="c", trajectory=TRAJ), {"price_table": table}
    )
    assert priced.value == pytest.approx((300 * 1.0 + 130 * 2.0) / 1000)
    tokens = await run_metric("ops.token_usage", EvalCase(id="c", trajectory=TRAJ))
    assert tokens.value == 430
    out = await run_metric(
        "ops.token_usage", EvalCase(id="c", trajectory=TRAJ), {"component": "output"}
    )
    assert out.value == 130


async def test_reliability_over_runs() -> None:
    runs = tuple(
        EvalCase(id="c", output="x", app_metrics=AppMetrics(error=e))
        for e in (None, "timeout", None, None)
    )
    r = await run_metric("ops.reliability", runs[0], runs=runs)
    assert r.score == 0.75 and r.details == {"runs": 4, "errors": 1}


async def test_consistency_methods() -> None:
    runs = (case("The answer is 42"), case("The answer is 42"), case("the answer: 42"))
    exact = await run_metric("ops.consistency", runs[0], method="exact", runs=runs)
    assert exact.score == pytest.approx(1 / 3)
    rouge = await run_metric("ops.consistency", runs[0], method="rouge", runs=runs)
    assert rouge.score > exact.score
    json_runs = (case('{"a": 1, "b": [1, 2]}'), case('{"b": [1, 2], "a": 1}'))
    ted = await run_metric("ops.consistency", json_runs[0], method="ted", runs=json_runs)
    assert ted.score == 1.0


def test_tree_edit_distance_known_values() -> None:
    f = Node("f", [Node("d", [Node("a"), Node("c", [Node("b")])]), Node("e")])
    g = Node("f", [Node("c", [Node("d", [Node("a"), Node("b")])]), Node("e")])
    assert tree_edit_distance(f, g) == 2  # Zhang & Shasha (1989) example
    assert structural_similarity({"a": 1}, {"a": 1}) == 1.0
    assert structural_similarity({"a": 1, "b": 2}, {"a": 1, "b": 3}) == pytest.approx(2 / 3)


def _calls(*names_args):
    return [{"name": n, "arguments": a} for n, a in names_args]


@pytest.mark.parametrize(
    ("expected", "match_type", "compare_args", "score"),
    [
        (_calls(("search", {"q": "bnb"}), ("search", {"q": "bnb"})), "exact", "exact", 1.0),
        (_calls(("search", {"q": "bnb"})), "exact", "exact", 0.0),
        (_calls(("search", {"q": "bnb"})), "in_order", "exact", 1.0),
        (_calls(("search", {"q": "eth"})), "in_order", "exact", 0.0),
        (_calls(("search", {"q": "eth"})), "in_order", "ignore", 1.0),
        (_calls(("search", {})), "any_order", "subset", 1.0),
        (
            _calls(
                ("search", {"q": "bnb"}),
            )
            * 3,
            "any_order",
            "exact",
            0.0,
        ),
        (_calls(("browse", {})), "any_order", "ignore", 0.0),
    ],
)
async def test_tool_trajectory_match(expected, match_type, compare_args, score) -> None:
    r = await run_metric(
        "agent.tool_trajectory_match",
        EvalCase(id="c", trajectory=TRAJ, expected={"tool_calls": expected}),
        {"match_type": match_type, "compare_args": compare_args},
    )
    assert r.score == score


async def test_trajectory_stats() -> None:
    cyc = session(
        *TRAJ.traces[0].steps,
        DelegationStep(id="5", from_agent="a", to_agent="b"),
        DelegationStep(id="6", from_agent="b", to_agent="a"),
    )
    info = await run_metric("agent.trajectory_stats", EvalCase(id="c", trajectory=cyc))
    assert info.score is None and info.passed is None
    assert info.details == {
        "steps": 6,
        "tool_calls": 2,
        "repeated_tool_calls": 1,
        "errors": 1,
        "depth": 3,  # roots count as depth 1, as in AgentSight
        "delegation_cycle": True,
    }
    limited = await run_metric(
        "agent.trajectory_stats", EvalCase(id="c", trajectory=cyc), {"max_repeated_tool_calls": 0}
    )
    assert limited.score == 0.0 and limited.details["violations"] == ["max_repeated_tool_calls"]


async def test_ops_edge_cases() -> None:
    low = await run_metric(
        "ops.token_usage",
        EvalCase(id="c", app_metrics=AppMetrics(output_tokens=3)),
        {"component": "output"},
        value_min=10,
    )
    assert (low.score, low.passed) == (0.0, False)
    enough = await run_metric(
        "ops.token_usage",
        EvalCase(id="c", app_metrics=AppMetrics(output_tokens=30)),
        {"component": "output"},
        value_min=10,
    )
    assert (enough.score, enough.passed) == (1.0, True)
    ttft = await run_metric(
        "ops.latency",
        EvalCase(id="c", app_metrics=AppMetrics(ttft_ms=120)),
        {"component": "ttft"},
        value_max=200,
    )
    assert ttft.value == 120 and ttft.passed is True
    by_tokens = EvalCase(
        id="c",
        app_metrics=AppMetrics(input_tokens=1000, output_tokens=500),
        metadata={"model": "m2"},
    )
    table = {"*": {"input_per_1k": 0.5, "output_per_1k": 1.0}}
    priced = await run_metric("ops.cost", by_tokens, {"price_table": table})
    assert priced.value == pytest.approx(1.0) and priced.details["source"] == "price_table"
    unpriced = await run_metric(
        "ops.cost",
        EvalCase(id="c", trajectory=TRAJ),
        {"price_table": {"other-model": {"input_per_1k": 1.0}}},
    )
    assert unpriced.status == "skipped"
