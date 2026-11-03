"""Golden tests for analysis.confidence, analysis.insights and agent.confidence.

Expected numbers are computed by hand from the AgentSight formulas (deriver / aggregator) with
the default config: weights errors .25, loops .20, depth .10, relevance .20, hedging .10,
deflection .15; risk exponent 1.
"""

from __future__ import annotations

import dataclasses
import json
import math

import pytest

from bnbagent_eval.analysis.confidence import (
    PROFILES,
    RISK_FLAGS,
    ConfidenceScorer,
    ConfidenceSummary,
    detect_query_intent,
    load_scoring_config,
    max_depth,
    risk_level,
)
from bnbagent_eval.analysis.insights import insights
from bnbagent_eval.types import (
    AgentStep,
    DelegationStep,
    EvalCase,
    HandoffStep,
    LlmStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    Usage,
)

from .metrics._util import run_metric

approx = pytest.approx


def session(*steps) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=tuple(steps)),))


def tool(i, name="search", content="ok", error=None, args=None, **kw) -> ToolStep:
    return ToolStep(
        id=f"t{i}",
        call=ToolCall(name=name, arguments={"i": i} if args is None else args),
        result=ToolResult(content=content, error=error),
        **kw,
    )


def chain(n: int) -> list[LlmStep]:
    return [LlmStep(id=str(i), parent_id=str(i - 1) if i else None) for i in range(n)]


def agent(prompt: str, response: str, **kw) -> AgentStep:
    return AgentStep(id=kw.pop("id", "a"), user_prompt=prompt, response=response, **kw)


def score(*steps, **kw):
    return ConfidenceScorer(**kw).score(session(*steps))


# -- derived scores ---------------------------------------------------------------------------


def test_errors_combine_explicit_empty_and_inferred() -> None:
    r = score(
        tool(1, content="", error="boom"),  # explicit + empty
        tool(2, content=""),  # empty
        tool(3, content="request timed out"),  # inferred from content
        tool(4, content="fine"),
    )
    d = r.derived
    assert (d.error_rate, d.empty_result_rate, d.inferred_error_rate) == (0.25, 0.5, 0.25)
    assert d.errors == approx(0.25 * 0.5 + 0.5 * 0.3 + 0.25 * 0.2)  # 0.325
    assert r.confidence == approx(1 - 0.25 * 0.325)
    assert r.signals.tool.unique_error_types == ["boom"]
    assert r.risks == []


def test_step_error_counts_as_tool_error() -> None:
    r = score(ToolStep(id="t", call=ToolCall(name="x"), error="crashed"))
    assert r.signals.tool.tool_error_count == 1 and r.derived.error_rate == 1.0


def test_loops_are_repeated_identical_calls() -> None:
    same = {"q": "bnb"}
    r = score(*(tool(i, args=same) for i in range(4)), tool(9, args={"q": "other"}))
    assert r.signals.structural.repeated_tool_calls == 3
    assert r.derived.loops == approx(3 / 5)
    assert r.confidence == approx(1 - 0.2 * 0.6)


@pytest.mark.parametrize(
    ("n", "depth", "risk"), [(0, 0, 0.0), (3, 3, 0.0), (5, 5, 2 / 3), (9, 9, 1.0)]
)
def test_depth_risk(n: int, depth: int, risk: float) -> None:
    steps = chain(n)
    assert max_depth(steps) == depth
    if steps:
        assert score(*steps).derived.depth == approx(risk)


def test_depth_ignores_orphans_and_cycles() -> None:
    steps = [LlmStep(id="a"), LlmStep(id="b", parent_id="missing"), LlmStep(id="c", parent_id="a")]
    assert max_depth(steps) == 2
    assert max_depth([LlmStep(id="x", parent_id="y"), LlmStep(id="y", parent_id="x")]) == 1


def test_relevance_full_mode_by_hand() -> None:
    # keywords explain/staking/rewards; one fragment (the response) holds 2 of 3 -> recall 2/3;
    # 4 content words / 4 words -> density 1 -> substantiveness 0.4; "explain" wants >= 20 words.
    r = score(agent("explain staking rewards", "Staking rewards accrue daily."))
    rel = r.signals.relevance
    assert rel.query_intent == "explain" and rel.format_match is False and not rel.low_signal
    assert rel.query_recall == approx(2 / 3) and rel.topic_coherence == 1.0
    assert rel.substantiveness == approx(0.4)
    composite = 0.35 * 2 / 3 + 0.20 * 0.4 + 0.25 * 1.0
    assert rel.composite_relevance == approx(composite)
    assert r.derived.relevance == approx(1 - composite)
    assert r.confidence == approx(1 - 0.2 * (1 - composite))


def test_relevance_low_signal_mode() -> None:
    # one keyword -> substantiveness * .45 + structural_match * .55; intent unknown -> match.
    r = score(agent("staking", "It is so."))
    assert r.signals.relevance.low_signal and r.signals.relevance.query_recall == 0.0
    assert r.derived.relevance == approx(0.45)


def test_relevance_zero_and_one() -> None:
    assert score(agent("explain staking rewards", "It is so.")).derived.relevance == 1.0
    assert score(agent("hi", "hello")).signals.relevance is None  # no keywords: not assessed
    assert score(tool(1)).signals.relevance is None  # no agent step


def test_substantiveness_artifacts() -> None:
    sx = ConfidenceScorer().extractor.substantiveness
    assert sx("")["score"] == 0.0
    assert sx("see #12")["identifiers"] == 0  # source quirk: \b before "#" needs a word char
    one = sx("see PROJ-12")  # 1 artifact -> 0.6; density 2/2 -> 1.0
    assert one["identifiers"] == 1 and one["score"] == approx(0.6 * 0.6 + 0.4)
    two = sx("see PROJ-12 and v1.2")
    assert two["score"] == approx(0.8 * 0.6 + 0.4)
    assert sx("edit src/app.py for PROJ-1 per https://x.example")["score"] == 1.0


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("How do I stake?", "how_to"),
        ("fix the bug", "action"),
        ("list pools", "list"),
        ("explain gas", "explain"),
        ("gas price?", "question"),
        ("gas price", "unknown"),
    ],
)
def test_query_intent(query: str, intent: str) -> None:
    assert detect_query_intent(query) == intent


@pytest.mark.parametrize(
    ("response", "hedging"),
    [
        ("maybe " + "word " * 9, 0.7),  # 1 phrase / 10 words = 10 per 100 -> capped at 1 * 0.7
        ("maybe " + "word " * 19, 0.35),  # 5 per 100 -> 0.5 * 0.7
        ("word " * 10, 0.0),
    ],
)
def test_hedging(response: str, hedging: float) -> None:
    r = score(agent("hi", response))
    assert r.derived.hedging == approx(hedging)
    assert r.derived.apology_density == 0.0  # the source never counts apologies


def test_deflection_refusal() -> None:
    r = score(agent("explain staking rewards", "I cannot help with staking rewards."))
    assert r.derived.refusal_detected and r.derived.deflection == 0.8
    assert "task_refusal" in r.risks


def test_deflection_pattern_and_flag() -> None:
    # recall 0, coherence 0, substantiveness 0.4 (4 content words / 12) -> relevance 0.92.
    r = score(
        agent("explain staking rewards", "I could not find that. Instead, I can tell you a joke.")
    )
    assert r.signals.relevance.deflection_detected
    assert r.derived.deflection == 0.6 and r.derived.relevance == approx(0.92)
    assert r.risks == ["task_deflection"]
    assert r.confidence == approx(1 - (0.2 * 0.92 + 0.15 * 0.6))


def test_deflection_low_recall_after_failure() -> None:
    # 5 keywords, only "validator" in the activity -> recall 0.2 (>= .15, < .3) -> (0.3-0.2)/0.3.
    r = score(
        agent("explain validator slashing penalties thresholds", "I couldn't load validator data.")
    )
    rel = r.signals.relevance
    assert rel.failure_acknowledged and not rel.deflection_detected
    assert rel.query_recall == approx(0.2)
    assert r.derived.deflection == approx(1 / 3)


def test_bare_failure_root_agent() -> None:
    # A sub-agent's substantive answer masks the root's bare failure in the combined response.
    sub = agent("x", "Validators v1.2 #3 #4 at src/stake.py", id="sub", parent_id="root")
    root = agent("explain validator slashing", "I could not do it so it is not to be", id="root")
    r = score(root, sub)
    assert r.signals.relevance.deflection_detected
    assert r.derived.deflection == 0.6


# -- risk flags -------------------------------------------------------------------------------


def test_flag_infinite_loop_and_limit_override() -> None:
    steps = [tool(i, args={"q": 1}) for i in range(7)]  # 6 repeats > 5
    assert score(*steps).flags["infinite_loop"]
    assert not score(*steps, limits={"max_repeated_calls": 6}).flags["infinite_loop"]


def test_flag_excessive_depth() -> None:
    assert score(*chain(11)).risks == ["excessive_depth"]
    assert score(*chain(10)).risks == []
    assert score(*chain(11), limits={"max_depth": 11}).risks == []


def test_flag_high_error_rate() -> None:
    r = score(tool(1, content="", error="e"), tool(2, content="", error="e"))
    assert r.derived.errors == approx(0.8) and r.risks == ["high_error_rate"]
    assert score(tool(1, content="", error="e"), limits={"max_error_rate": 0.8}).risks == []


def test_flag_hidden_tool_failures() -> None:
    r = score(*(tool(i, content="Error: upstream exception") for i in range(4)))
    assert r.derived.inferred_error_rate == 1.0 and r.derived.errors == approx(0.2)
    assert r.risks == ["hidden_tool_failures"]


def test_flags_cover_all_six() -> None:
    r = score(tool(1))
    assert list(r.flags) == list(RISK_FLAGS) and not any(r.flags.values())
    assert r.explanation.summary.startswith("Trace shows healthy execution")


# -- risk levels, weights, profiles -----------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "level"),
    [
        (1.0, "low"),
        (0.8, "low"),
        (0.7999, "medium"),
        (0.6, "medium"),
        (0.5999, "high"),
        (0.4, "high"),
        (0.3999, "critical"),
        (0.0, "critical"),
    ],
)
def test_risk_level_boundaries(confidence: float, level: str) -> None:
    assert risk_level(confidence) == level


def test_weight_overrides_are_normalized() -> None:
    cfg = load_scoring_config(weight_overrides={"errors": 0.5})  # total 1.25
    assert cfg.weights["errors"] == approx(0.4) and cfg.weights["loops"] == approx(0.16)
    assert math.fsum(cfg.weights.values()) == approx(1.0)
    near = load_scoring_config(weight_overrides={"errors": 0.2505})  # within 0.001: kept
    assert near.weights["errors"] == 0.2505
    with pytest.raises(ValueError, match="unknown weight"):
        load_scoring_config(weight_overrides={"speed": 1})
    with pytest.raises(ValueError, match="zero"):
        load_scoring_config(weight_overrides=dict.fromkeys(PROFILES["general"]["weights"], 0))


def test_profiles_and_exponent() -> None:
    errs = (tool(1, content="", error="boom"), tool(2, content=""), tool(3, content="failed"))
    errs = (*errs, tool(4, content="fine"))  # errors = 0.325 (see above)
    assert score(*errs).confidence == approx(1 - 0.25 * 0.325)
    assert score(*errs, profile="coding").confidence == approx(1 - 0.30 * 0.325)
    assert score(*errs, profile="support").confidence == approx(1 - 0.15 * 0.325)
    assert ConfidenceScorer(profile="analysis").weights["hedging"] == 0.2
    loops = [tool(i, args={}) for i in range(4)]  # loops = 0.75
    assert score(*loops, risk_exponent=2.0).confidence == approx(1 - 0.2 * 0.75**2)
    with pytest.raises(ValueError, match="profile"):
        ConfidenceScorer(profile="trading")


# -- batch summary ----------------------------------------------------------------------------


def test_summary_statistics() -> None:
    base = score(tool(1))
    specs = {"a": (0.9, []), "b": (0.7, ["infinite_loop"]), "c": (0.5, ["infinite_loop"])}
    specs["d"] = (0.3, ["task_refusal"])
    results = {
        k: dataclasses.replace(base, confidence=c, risk_level=risk_level(c), risks=r)
        for k, (c, r) in specs.items()
    }
    s = ConfidenceSummary.from_results(results, min_confidence=0.6)
    assert s.mean_score == approx(0.6) and s.median_score == approx(0.6)
    assert s.std_dev == approx(math.sqrt(0.2 / 3))
    assert (s.min_score, s.max_score, s.total_traces) == (0.3, 0.9, 4)
    assert s.risk_distribution == {"low": 1, "medium": 1, "high": 1, "critical": 1}
    assert s.common_risks == ["infinite_loop"]  # 2 of 4 traces (>= 50%)
    assert s.risk_frequency == {"infinite_loop": 2, "task_refusal": 1}
    assert s.gated_count == 2
    assert s.lowest_scoring == [("d", 0.3), ("c", 0.5), ("b", 0.7)]
    json.dumps(s.to_dict())
    assert ConfidenceSummary.from_results([base]).std_dev == 0.0
    empty = ConfidenceSummary.from_results({})
    assert empty.total_traces == 0 and empty.risk_distribution["critical"] == 0


# -- insights ---------------------------------------------------------------------------------


def test_insights_hotspots_loops_and_cycles() -> None:
    steps = [
        AgentStep(id="root", agent_id="main", start_ms=0, end_ms=2000),
        *(tool(i, name="fast", parent_id="root", start_ms=0, end_ms=10) for i in range(5)),
        tool(5, name="slow", parent_id="root", start_ms=0, end_ms=1000),
        tool(6, name="flaky", parent_id="root", error="rate limited", content=""),
        *(tool(i, name="flaky", parent_id="root") for i in (7, 8)),
        *(tool(9, name="retry", args={"q": 1}, agent_id="worker") for _ in range(3)),
        DelegationStep(id="d1", from_agent="main", to_agent="worker"),
        DelegationStep(id="d2", from_agent="worker", to_agent="main"),
        HandoffStep(id="h1", from_agent="main", to_agent="billing"),
        LlmStep(
            id="l1",
            model="m1",
            start_ms=0,
            end_ms=100,
            usage=Usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=30,
                cache_write_tokens=10,
                cost_usd=0.01,
            ),
        ),
        LlmStep(id="l2", model="m1", usage=Usage(input_tokens=200, output_tokens=80)),
    ]
    out = insights(session(*steps))
    json.dumps(out)
    issues = out["issues"]
    # tool latencies: 5 x 10 + 1000 + 6 x 0 over 12 calls -> mean 87.5, x3 = 262.5.
    assert [(h["entity_type"], h["entity_name"]) for h in issues["latency_hotspots"]] == [
        ("tool", "slow")
    ]
    assert issues["error_hotspots"] == [
        {
            "tool_name": "flaky",
            "agent_id": "main",
            "error_count": 1,
            "total_calls": 3,
            "error_rate": approx(1 / 3),
            "error_types": ["rate limited"],
        }
    ]
    assert issues["failing_tool_agent_pairs"] == [["flaky", "main"]]
    assert issues["most_failing_tool"] == "flaky" and issues["slowest_tool"] == "slow"
    assert issues["loop_patterns"] == []  # 2 repeated "retry" calls < threshold 3
    cycles = {(c["source_agent"], c["target_agent"]) for c in issues["agent_interaction_issues"]}
    assert cycles == {("main", "worker"), ("worker", "main")}
    stats = out["detailed_stats"]
    model = stats["per_model_stats"][0]
    assert model["model_id"] == "m1" and model["call_count"] == 2
    assert model["total_tokens"] == 430 and model["cache_hit_rate"] == 0.75
    assert model["total_cost"] == 0.01
    flaky = next(t for t in stats["per_tool_stats"] if t["tool_name"] == "flaky")
    assert flaky["error_rate"] == approx(1 / 3) and flaky["empty_result_count"] == 1
    main = next(a for a in stats["per_agent_stats"] if a["agent_id"] == "main")
    assert main["tool_call_count"] == 9 and main["delegations_sent"] == 1
    assert main["handoffs_initiated"] == 1
    assert stats["token_breakdown"]["cache_hit_rate"] == 0.75
    summary = out["summary"]
    assert summary["tool_spans"] == 12 and summary["delegation_spans"] == 2
    assert summary["overall_error_rate"] == approx(1 / 12)


def test_insights_retry_loop_threshold() -> None:
    steps = [tool(i, args={"q": 1}, start_ms=10 - i, end_ms=20) for i in range(4)]  # 3 repeats
    three = insights(session(*steps, tool(9, name="other")))
    assert three["issues"]["loop_patterns"] == [
        {
            "loop_type": "tool_retry",
            "entities_involved": ["other", "search"],
            "repetition_count": 3,
            "sequence": ["search"] * 4 + ["other"],  # by start time; untimed steps last
        }
    ]
    two = insights(session(*(tool(i, args={"q": 1}) for i in range(3))))
    assert two["issues"]["loop_patterns"] == []


def test_insights_quality_issues() -> None:
    out = insights(session(agent("explain staking rewards", "I cannot. Maybe later.")))
    kinds = {q["issue_type"]: q["severity"] for q in out["issues"]["quality_issues"]}
    # 1 hedge / 4 words = 25 per 100 -> high; recall 0 < 0.1 -> medium.
    assert kinds == {"high_hedging": "high", "task_refusal": "high", "low_relevance": "medium"}


def test_scorer_attaches_insights() -> None:
    r = score(tool(1))
    assert r.insights is not None and r.insights["summary"]["tool_spans"] == 1
    assert ConfidenceScorer(include_insights=False).score(session(tool(1))).insights is None
    json.dumps(r.to_dict())


# -- the agent.confidence metric --------------------------------------------------------------


async def test_metric_details_and_threshold() -> None:
    traj = session(tool(1, content="", error="e"), tool(2, content="", error="e"))
    res = await run_metric("agent.confidence", EvalCase(id="c", trajectory=traj))
    assert res.score == approx(1 - 0.25 * 0.8) and res.threshold == 0.6 and res.passed
    d = res.details
    assert d["risk_level"] == "low" and d["flags"]["high_error_rate"]
    assert d["risks"] == ["high_error_rate"] and d["derived_scores"]["errors"] == approx(0.8)
    assert d["insights"]["summary"]["tool_spans"] == 2
    res = await run_metric(
        "agent.confidence",
        EvalCase(id="c", trajectory=traj),
        params={"profile": "coding", "max_error_rate": 0.9},
    )
    assert res.score == approx(1 - 0.30 * 0.8) and res.details["risks"] == []


async def test_metric_weight_overrides_and_exponent() -> None:
    traj = session(*(tool(i, args={}) for i in range(4)))  # loops 0.75
    res = await run_metric(
        "agent.confidence",
        EvalCase(id="c", trajectory=traj),
        params={"weight_overrides": {"loops": 0.45}, "risk_exponent": 2},  # total 1.25
    )
    assert res.details["weights"]["loops"] == approx(0.36)
    assert res.score == approx(1 - 0.36 * 0.75**2)


async def test_metric_uses_case_text_without_agent_steps() -> None:
    case = EvalCase(
        id="c",
        input="explain staking rewards",
        output="It is so.",
        trajectory=session(LlmStep(id="l")),
    )
    res = await run_metric("agent.confidence", case)
    assert res.details["derived_scores"]["relevance"] == 1.0 and res.score == approx(0.8)


def test_metric_params_schema() -> None:
    from bnbagent_eval.metrics import registry

    spec = registry.resolve("agent.confidence")[0].spec
    assert not spec.validate_params({"profile": "support", "weight_overrides": {"errors": 1}})
    assert spec.validate_params({"profile": "trading"})
    assert spec.validate_params({"weight_overrides": {"speed": 1}})
    assert spec.validate_params({"risk_exponent": 0})
