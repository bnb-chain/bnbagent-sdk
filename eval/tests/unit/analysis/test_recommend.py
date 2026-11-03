"""authoring.recommend: data prerequisites, metric prerequisites, ranking, suite output."""

from __future__ import annotations

import pytest

from bnbagent_eval.authoring.recommend import check_prerequisites, recommend
from bnbagent_eval.engine import resolve_suite
from bnbagent_eval.metrics.base import Kind, MethodSpec, MetricSpec
from bnbagent_eval.types import (
    AgentStep,
    Context,
    EvalCase,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)


def _session(i: int, *, result: bool = True) -> Session:
    steps = (
        AgentStep(id=f"a{i}", user_prompt="What is my balance?", response="You have 12 BNB."),
        ToolStep(
            id=f"t{i}",
            call=ToolCall(name="get_balance", arguments={"address": "0xabc"}),
            result=ToolResult(content="12") if result else None,
        ),
    )
    return Session(id=f"s{i}", traces=(Trace(id=f"tr{i}", steps=steps),))


def _ids(rec) -> list[str]:
    return [m.config.id for m in rec.metrics]


def test_dataset_validity() -> None:
    empty = check_prerequisites([])
    assert not empty.blackbox_valid and not empty.whitebox_valid and empty.issues
    dupes = check_prerequisites([EvalCase(id="a", output="x"), EvalCase(id="a", output="y")])
    assert not dupes.blackbox_valid and "duplicate" in dupes.issues[0]
    nothing = check_prerequisites([EvalCase(id="a", input="q")])
    assert not nothing.blackbox_valid
    assert recommend([EvalCase(id="a", input="q")]).metrics == ()


def test_trace_availability_and_quality() -> None:
    full = check_prerequisites([EvalCase(id=str(i), trajectory=_session(i)) for i in range(2)])
    assert full.has_traces and full.whitebox_valid
    # Blackbox is possible from the traces: first user message + final response captured.
    assert full.blackbox_valid and full.trace_quality["final_agent_response"] == 2
    partial = check_prerequisites(
        [EvalCase(id="0", output="x", trajectory=_session(0)), EvalCase(id="1", output="y")]
    )
    assert partial.has_traces and not partial.whitebox_valid and partial.blackbox_valid
    assert any("1/2 cases have a trajectory" in i for i in partial.issues)
    weak = check_prerequisites([EvalCase(id="0", trajectory=_session(0, result=False))])
    assert weak.trace_quality["tool_results_present"] == 0
    assert any("trace quality" in i for i in weak.issues)
    told = check_prerequisites([EvalCase(id="0", output="x")], traces_available=True)
    assert not told.has_traces and any("traces_available" in i for i in told.issues)


def test_blackbox_recommendation_without_judge() -> None:
    cases = [EvalCase(id=str(i), input="q", output="a", expected="a") for i in range(3)]
    rec = recommend(cases, judge=False)
    ids = _ids(rec)
    assert "text.exact_match" in ids and "quality.answer_correctness" in ids
    assert all(m.kind == "rule" for m in rec.metrics)
    # Reference-based metrics rank before generic ones.
    assert rec.metrics[0].priority == "high"
    assert ids.index("text.rouge") < ids.index("text.embedding_similarity")
    # text.length has no usable default (a bound is required), so it is skipped with a reason.
    length = next(sk for sk in rec.skipped if "text.length" in str(sk))
    assert "needs one of the params" in str(length)
    # answer_correctness falls back to its rule method (native judge not enabled).
    ac = next(m for m in rec.metrics if m.config.id == "quality.answer_correctness")
    assert ac.config.method == "rule"
    skipped = {s.id: s for s in rec.skipped}
    assert "trajectory" in skipped["agent.tool_selection"].missing
    assert skipped["custom.python"].reason.startswith("needs params")
    assert "runs" in skipped["ops.consistency"].missing
    assert "red-team" in skipped["redteam.attack_resistance"].reason
    assert skipped["format.json_valid"].reason == "outputs are not JSON"
    assert "no data" in skipped["ops.latency"].reason
    assert skipped["quality.clarity"].reason == "method kind judge not enabled"
    resolve_suite(rec.suite(name="auto"))  # suite-ready


def test_judge_whitebox_rag_and_stage() -> None:
    cases = [
        EvalCase(
            id=str(i),
            input="What is my balance?",
            output='{"balance": 12}',
            contexts=(Context(text="Balance 12 BNB"),),
            trajectory=_session(i),
        )
        for i in range(2)
    ]
    rec = recommend(cases)
    ids = _ids(rec)
    for mid in (
        "agent.tool_selection",
        "rag.faithfulness",
        "quality.clarity",
        "format.json_valid",
    ):
        assert mid in ids
    assert rec.prerequisites.whitebox_valid
    high = [m.config.id for m in rec.metrics if m.priority == "high"]
    assert "agent.tool_selection" in high and "quality.clarity" not in high
    # Negotiated stage: deterministic (rule) methods first within a priority tier.
    staged = recommend(cases, stage="review")
    kinds = [m.kind for m in staged.metrics if m.priority == "high"]
    assert kinds == sorted(kinds, key=lambda k: k != "rule")
    # Category filter, and coverage below 100% skips the metric.
    only = recommend(cases, categories=["rag"])
    assert {m.config.id.split(".")[0] for m in only.metrics} == {"rag"}
    half = [cases[0], EvalCase(id="x", input="q", output="a", trajectory=_session(9))]
    loose = recommend(half, categories=["rag"], min_coverage=0.5)
    strict = recommend(half, categories=["rag"])
    assert loose.metrics and not strict.metrics
    with pytest.raises(ValueError):
        recommend(cases, min_coverage=0)


def test_missing_extra_is_reported() -> None:
    spec = MetricSpec(
        id="text.fake_extra",
        category="text",
        description="needs a missing module",
        methods=(
            MethodSpec(
                "model",
                Kind.MODEL,
                required_inputs=frozenset({"output"}),
                extra="nope",
                extra_module="bnbagent_eval_no_such_module",
            ),
        ),
    )
    rec = recommend([EvalCase(id="a", output="x")], model=True, specs=[spec])
    assert not rec.metrics
    assert "pip install 'bnbagent-eval[nope]'" in rec.skipped[0].reason
    assert rec.to_dict()["skipped"][0]["missing"] == ["bnbagent_eval_no_such_module"]
