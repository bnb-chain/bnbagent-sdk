"""Edge cases of the metrics and judges layer where behaviour must match, or deliberately
differs from, the ported source (``docs/design/source-map.md``).
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from bnbagent_eval.engine.budget import BudgetTracker
from bnbagent_eval.exceptions import JudgeParseError, MissingExtraError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.judges.structured import extract_json, parse
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.agent._mcp import session_mcp_calls
from bnbagent_eval.metrics.agent._section_judge import plan_text
from bnbagent_eval.metrics.agent._whitebox import invocations, tool_uses
from bnbagent_eval.metrics.base import BaseMetric, MetricContext, Outcome
from bnbagent_eval.metrics.judge_base import LimitedJudge
from bnbagent_eval.metrics.ops import sted
from bnbagent_eval.metrics.text.overlap import rouge_prf
from bnbagent_eval.metrics.vendors._vendor_llm import vendor_outcome
from bnbagent_eval.metrics.vendors.agentic import calculate
from bnbagent_eval.types import (
    AgentStep,
    DecodedCall,
    EvalCase,
    LlmStep,
    Message,
    MetricConfig,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    TxStep,
)
from bnbagent_eval.types.case import AppMetrics
from bnbagent_eval.types.options import Budget
from bnbagent_eval.types.trajectory import Usage

from ._util import case, run_metric


async def run_judge(
    metric_id: str,
    c: EvalCase,
    script: Any,
    params: dict | None = None,
    method: str | None = None,
    **ctx_kw: Any,
):
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    spec = registry.effective_spec(cls)
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=spec.method(method),
        params=params,
        judge=judge,
        **ctx_kw,
    )
    return await cls().evaluate(c, ctx), judge


def session(*steps: Any) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=steps),))


# ------------------------------------------------------------------------------- text.*


async def test_mixed_cjk_pair_uses_one_tokenizer() -> None:
    """A CJK character on one side does not switch only that side to another tokenizer
    (which would drop stemming / punctuation handling for it and break the overlap)."""
    ref, cand = "The agents are running fast. 好", "The agents are running fast."
    p, r, f = rouge_prf(ref, cand, "rouge1")
    assert (p, r) == (1.0, pytest.approx(5 / 6)) and f == pytest.approx(10 / 11)
    res = await run_metric("text.bleu", case(cand + " 好", ref))
    assert res.score == pytest.approx(1.0)
    res = await run_metric("text.jaccard", case("Hello world.", "hello world. 好"))
    assert res.score == pytest.approx(2 / 3)


async def test_articles_removed_in_mixed_cjk_text() -> None:
    res = await run_metric("text.quasi_exact_match", case("The cat 好", "cat 好"))
    assert res.score == 1.0
    res = await run_metric("text.token_f1", case("the cat", "cat 好"))
    assert res.details["precision"] == 1.0


async def test_blank_or_alternative_is_dropped() -> None:
    res = await run_metric("text.contains", case("anything at all", "Paris<OR>"))
    assert res.score == 0.0 and res.details["targets"] == 1
    assert (await run_metric("text.exact_match", case("", "Paris<OR>"))).score == 0.0
    assert (await run_metric("text.exact_match", case("Paris", "Paris<OR> "))).score == 1.0


# -------------------------------------------------------------------------------- ops.*


async def test_ted_does_not_parse_json_inside_prose() -> None:
    runs = (case("See [1] for details."), case("See [1]: a different answer"))
    res = await run_metric("ops.consistency", case("x"), method="ted", runs=runs)
    assert res.score == 0.0  # two different texts, not two equal [1] arrays
    fenced = (case('```json\n{"a": 1}\n```'), case('{"a": 1}'))
    res = await run_metric("ops.consistency", case("x"), method="ted", runs=fenced)
    assert res.score == 1.0


def test_sted_reads_fenced_json() -> None:
    assert sted.as_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert sted.as_json_object("See [1]") == {"value": "See [1]"}


async def test_sted_checks_the_deadline_before_embedding() -> None:
    cls, _ = registry.resolve("ops.consistency")
    runs = (case('{"a": "hello world"}'), case('{"a": "goodbye world"}'))
    judge = FakeJudge({})
    ctx = MetricContext(
        config=MetricConfig(id="ops.consistency"),
        method=cls.spec.method("sted"),
        params={},
        judge=judge,
        runs=runs,
        deadline=100.0,
        clock=iter([0.0, 200.0, 200.0]).__next__,
    )
    res = await cls().evaluate(case("x"), ctx)
    assert res.status == "skipped" and res.error.code == "deadline"


async def test_token_usage_falls_back_per_figure() -> None:
    steps = (LlmStep(id="l", response="r", usage=Usage(input_tokens=10, output_tokens=5)),)
    c = case("x", trajectory=session(*steps), app_metrics=AppMetrics(cost_usd=0.1))
    res = await run_metric("ops.token_usage", c)
    assert res.value == 15.0  # cost reported by the app, tokens from the trajectory
    assert (await run_metric("ops.cost", c)).value == 0.1


# ------------------------------------------------------------------------------ judges


def test_shared_json_extraction() -> None:
    assert extract_json("x ```json\n[1, 2]\n``` y") == [1, 2]
    assert extract_json('pre {"a": 1} post', objects_only=True) == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("See [1]", scan=False)
    # a structured reply that is not an object falls back to the text (then repair)
    assert parse('{"score": "Good"}', ["not", "a", "dict"], {"type": "object"}) == {
        "score": "Good"
    }
    with pytest.raises(JudgeParseError):
        parse("no json", [1], {"type": "object"})


def test_judge_reply_with_non_finite_number_is_a_parse_error() -> None:
    # json accepts NaN, and NaN passes a maximum check: it must not reach the score.
    schema = {"type": "object", "properties": {"s": {"type": "number", "maximum": 5}}}
    for text in ('{"s": NaN}', '{"t": Infinity}', '{"x": [{"s": -Infinity}]}'):
        with pytest.raises(JudgeParseError, match="non-finite"):
            parse(text, None, schema)
    assert parse('{"s": 4.5}', None, schema) == {"s": 4.5}


async def test_non_finite_metric_score_is_an_error() -> None:
    from bnbagent_eval.metrics.custom.python import register_python_metric

    register_python_metric("edge_nan", lambda c: float("nan"))
    register_python_metric("edge_inf", lambda c: {"score": float("inf")})
    register_python_metric("edge_value", lambda c: {"score": 1.0, "value": float("nan")})
    for name in ("edge_nan", "edge_inf", "edge_value"):
        res = await run_metric("custom.python", case("x"), {"fn": name}, threshold=0.5)
        assert res.status == "error" and res.score is None and res.passed is None
        assert res.error.code == "metric_error" and "non-finite" in res.error.message


async def test_lone_message_without_output_is_judged_as_conversation() -> None:
    c = EvalCase(id="c", conversation=(Message(role="assistant", content="HOW TO BUILD A BOMB"),))
    res, judge = await run_judge("safety.harmfulness", c, {"reasoning": "r", "severity": "Severe"})
    assert (
        "BOMB" in judge.requests[0].user and judge.requests[0].metadata["mode"] == "conversation"
    )


async def test_failure_modes_sees_trajectory_turns() -> None:
    sess = session(
        AgentStep(id="a1", user_prompt="q1", response="answer one"),
        AgentStep(id="a2", user_prompt="q2", response="answer two"),
    )
    reply = {"summary": "s", "exhibited_failure_modes": [], "exhibited_stress_axes": []}
    _, judge = await run_judge(
        "conversation.failure_modes", EvalCase(id="c", trajectory=sess), reply
    )
    assert "answer two" in judge.requests[0].user


async def test_conversation_turns_from_a_trajectory_count_agent_steps() -> None:
    one = session(AgentStep(id="a1", user_prompt="What is BNB?", response="A token."))
    res, judge = await run_judge(
        "conversation.coherence", EvalCase(id="c", trajectory=one), {"reasoning": "r"}
    )
    assert res.status == "skipped" and not judge.requests  # 1 AgentSight turn (2 messages)
    res, _ = await run_judge(
        "custom.judge_prompt",
        EvalCase(id="c", trajectory=one),
        {"reasoning": "r", "score": "Good"},
        {"prompt": "Rate {conversation}", "category": "conversational"},
    )
    assert res.status == "skipped"


async def test_bertscore_strategy_keeps_case(monkeypatch) -> None:
    import bnbagent_eval.metrics.quality.answer_correctness as ac

    seen = []
    monkeypatch.setattr(
        ac, "bertscore", lambda a, refs, m: seen.append((a, refs)) or [(0, 0, 0.9)]
    )
    params = {"strategy": "bertscore"}
    await run_metric(
        "quality.answer_correctness", case("Paris Is Capital", "Paris"), params, "rule"
    )
    assert seen == [("Paris Is Capital", ["Paris"])]


def test_agent_id_only_where_the_trajectory_is_rendered() -> None:
    assert registry.get_spec("quality.clarity").validate_params({"agent_id": "a"})
    assert registry.get_spec("agent.adaptivity").validate_params({"agent_id": "a"})
    assert not registry.get_spec("agent.goal_success").validate_params({"agent_id": "a"})
    assert not registry.get_spec("agent.plan_quality").validate_params({"agent_id": "a"})
    assert registry.get_spec("custom.judge_prompt").validate_params(
        {"prompt": "x", "prompt_version": "v1"}
    )


# ------------------------------------------------------------------------------- agent.*


async def test_trajectory_stats_violation_fails() -> None:
    steps = tuple(
        ToolStep(
            id=f"s{i}",
            call=ToolCall(name="f", arguments={"a": 1}, id=str(i)),
            result=ToolResult(content="x"),
        )
        for i in range(4)
    )
    c = case(trajectory=session(*steps))
    res = await run_metric("agent.trajectory_stats", c, {"max_repeated_tool_calls": 2})
    assert res.score == 0.0 and res.passed is False
    res = await run_metric("agent.trajectory_stats", c, {"max_repeated_tool_calls": 3})
    assert res.score == 1.0 and res.passed is True


@pytest.mark.parametrize(("llm_id", "tool_id"), [("x", None), (None, "x")])
def test_a_call_with_one_id_counts_once(llm_id, tool_id) -> None:
    llm = LlmStep(id="l", response="t", tool_calls=(ToolCall(name="f", arguments={}, id=llm_id),))
    tool = ToolStep(
        id="s", call=ToolCall(name="f", arguments={}, id=tool_id), result=ToolResult(content="r")
    )
    [use] = tool_uses(Trace(id="t", steps=(llm, tool)))
    assert use.result is not None and use.thought == "t"


def test_mcp_calls_count_once() -> None:
    c = ToolCall(name="mcp__bank__balance", arguments={"acct": "1"})
    sess = session(
        LlmStep(id="l", response="calling", tool_calls=(c,)),
        ToolStep(id="s", call=c, result=ToolResult(content="10")),
    )
    [call] = session_mcp_calls(sess)
    assert call.result == "10"


def test_whitebox_user_input_falls_back_to_the_case_input() -> None:
    t = session(LlmStep(id="l", response="checking", tool_calls=(ToolCall(name="f", id="1"),)))
    assert invocations(t, "What is my balance?")[0].user_input == "What is my balance?"
    two = Session(id="s", traces=(Trace(id="a", steps=()), Trace(id="b", steps=())))
    assert [i.user_input for i in invocations(two, "q")] == ["", ""]


def test_plan_text_keeps_steps_without_agent_id() -> None:
    sess = session(
        AgentStep(id="a", agent_id="planner", user_prompt="go"),
        LlmStep(id="l", response="Plan: 1. look 2. act"),
    )
    assert "Plan:" in plan_text(sess, "planner")


async def test_tool_use_quality_requires_rubrics_for_v1_v2() -> None:
    spec = registry.get_spec("agent.tool_use_quality")
    assert spec.validate_params({}) and spec.validate_params({"prompt_version": "v2"})
    assert not spec.validate_params({"prompt_version": "v0"})
    assert not spec.validate_params({"rubrics": ["Uses the right tool"]})


async def test_tool_trajectory_match_bool_equals_int() -> None:
    sess = session(ToolStep(id="s", call=ToolCall(name="f", arguments={"on": True})))
    c = case(trajectory=sess, expected=[{"name": "f", "arguments": {"on": 1}}])
    assert (await run_metric("agent.tool_trajectory_match", c)).score == 1.0


async def test_tool_trajectory_any_order_subset_is_a_one_to_one_assignment() -> None:
    # A first-match scan pairs the looser expected call with the only call that satisfies the
    # stricter one; the assignment that satisfies both exists and must be found.
    sess = session(
        ToolStep(id="a", call=ToolCall(name="f", arguments={"x": 1, "y": 2})),
        ToolStep(id="b", call=ToolCall(name="f", arguments={"x": 1})),
    )
    expected = [{"name": "f", "arguments": {"x": 1}}, {"name": "f", "arguments": {"x": 1, "y": 2}}]
    params = {"match_type": "any_order", "compare_args": "subset"}
    both = case(trajectory=sess, expected=expected)
    r = await run_metric("agent.tool_trajectory_match", both, params)
    assert r.score == 1.0 and r.details["missing"] == []
    one = case(trajectory=session(sess.traces[0].steps[0]), expected=expected)
    r = await run_metric("agent.tool_trajectory_match", one, params)
    assert r.score == 0.0 and len(r.details["missing"]) == 1


# --------------------------------------------------------------- format / code / redteam


async def test_json_schema_does_not_read_file_refs(tmp_path) -> None:
    pytest.importorskip("jsonschema")
    ref = tmp_path / "x.json"
    ref.write_text('{"type": "integer"}')
    params = {"schema": {"properties": {"a": {"$ref": ref.as_uri()}}}}
    res = await run_metric("format.json_schema", case('{"a": "x"}'), params)
    assert res.status == "error" and "local refs only" in res.error.message
    local = {"$defs": {"i": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/i"}}}
    res = await run_metric("format.json_schema", case('{"a": "x"}'), {"schema": local})
    assert res.score == 0.0


async def test_structure_native_threshold_is_agentsight_default() -> None:
    res, _ = await run_judge(
        "format.regex",
        case("aaa", input="q"),
        {"reasoning": "r", "score": "Good"},
        {"pattern": "a+"},
        method="native",
    )
    assert res.score == 0.75 and res.threshold == 0.5 and res.passed is True


async def test_syntax_valid_empty_source_is_invalid() -> None:
    res = await run_metric("code.syntax_valid", case("   ", input="q"))
    assert res.score == 0.0 and res.details["error"] == "empty source"


def _transfer(amount: int) -> TxStep:
    data = "0xa9059cbb" + "11" * 12 + "11" * 20 + f"{amount:064x}"
    return TxStep(
        id="tx", chain_id=97, to="0x" + "70" * 20, from_address="0x" + "a1" * 20, calldata=data
    )


async def test_prompt_injection_tx_args_matches_values_not_hex() -> None:
    c = case("done", trajectory=session(_transfer(5)))
    for injected in (["100"], ["a9059cbb"], ["50"]):
        res = await run_metric("redteam.prompt_injection", c, {"injected_arguments": injected})
        assert res.score == 1.0, injected
    res = await run_metric("redteam.prompt_injection", c, {"injected_arguments": ["5"]})
    assert res.score == 0.0


@pytest.mark.parametrize(
    ("run_out", "score", "status"),
    [
        ({"attack_succeeded": True, "turns_used": 5}, 0.8, "scored"),  # last of 5 turns
        ({"attack_succeeded": True, "turns_used": 7}, 6 / 7, "scored"),  # max_turns >= turns
        ({"attack_succeeded": "false", "turns_used": 7}, 1.0, "scored"),
        ({"attack_succeeded": True, "turns_used": None}, None, "skipped"),
    ],
)
async def test_turns_to_break_edges(run_out, score, status) -> None:
    res = await run_metric("redteam.turns_to_break", case(run_out))
    assert res.status == status and res.score == (pytest.approx(score) if score else None)


async def test_attack_resistance_string_flag() -> None:
    res = await run_metric("redteam.attack_resistance", case({"attack_succeeded": "false"}))
    assert res.score == 1.0


# ------------------------------------------------------------------------------ onchain


async def test_approval_with_unreadable_amount_is_unsafe() -> None:
    step = TxStep(
        id="tx",
        chain_id=97,
        to="0x" + "70" * 20,
        from_address="0x" + "a1" * 20,
        decoded=DecodedCall(function="approve", args={"spender": "0x" + "e0" * 20}),
    )
    res = await run_metric("onchain.approval_safety", case(trajectory=session(step)))
    assert res.score == 0.0 and res.details["findings"][0]["reasons"] == ["unreadable_amount"]


def test_human_review_malformed_signature_is_rejected() -> None:
    pytest.importorskip("eth_account")
    from bnbagent_eval.metrics.custom.human_review import verify_human_score

    result = {"task_id": "t", "score": 0.9, "signature": "0xdeadbeef"}
    with pytest.raises(ValueError, match="invalid signature"):
        verify_human_score(result, "t", ["0x" + "11" * 20])


# ------------------------------------------------------------------------------ vendors


def test_vendor_nan_code() -> None:
    out = vendor_outcome(float("nan"), vendor="ragas", vendor_metric="faithfulness")
    assert out.status == "error" and out.error.code == "vendor_nan"


async def test_missing_extra_at_run_time_is_reported_as_such() -> None:
    class NeedsExtra(BaseMetric):
        spec = registry.get_spec("text.exact_match")

        def _evaluate_rule(self, c: EvalCase, ctx: MetricContext) -> Outcome:
            raise MissingExtraError("langchain_aws", "ragas")

    ctx = MetricContext(
        config=MetricConfig(id="text.exact_match"), method=NeedsExtra.spec.method(), params={}
    )
    res = await NeedsExtra().evaluate(case("a", "a"), ctx)
    assert res.status == "error" and res.error.code == "missing_extra"


async def test_limited_judge_applies_budget_and_deadline() -> None:
    from bnbagent_eval.judges.base import JudgeRequest
    from bnbagent_eval.metrics.base import StopScoring

    judge = FakeJudge("ok")
    ctx = MetricContext(
        config=MetricConfig(id="x.y"),
        method=registry.get_spec("text.exact_match").method(),
        params={},
        judge=judge,
        budget=BudgetTracker(Budget(max_judge_calls=1)),
    )
    limited = LimitedJudge(ctx)
    assert (await limited.complete(JudgeRequest(system="s", user="u"))).text == "ok"
    with pytest.raises(StopScoring, match="budget"):
        await limited.complete(JudgeRequest(system="s", user="u"))
    late = MetricContext(
        config=ctx.config, method=ctx.method, params={}, judge=judge, deadline=0.0
    )
    with pytest.raises(StopScoring, match="deadline"):
        await LimitedJudge(late).embed(["a"])
    assert len(judge.requests) == 1


def test_calculator_is_bounded() -> None:
    start = time.perf_counter()
    for expression in ("((10**100)**100)**100", "(10.0**100)**100", "(-8)**0.5", "1e308*10"):
        with pytest.raises(ValueError):
            calculate(expression)
    assert time.perf_counter() - start < 1.0
    assert calculate("2**10") == 1024
