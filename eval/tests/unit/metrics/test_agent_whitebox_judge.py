"""Golden tests for the AgentSight whitebox agent.* judge metrics (FakeJudge)."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.judges.prompts.agent.reasoning import DEFAULT_RUBRICS
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.agent.execution_chain import (
    CHAIN_WEIGHTS,
    aggregate_links,
    parse_links,
    reliability_score,
)
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import (
    AgentStep,
    DelegationStep,
    EvalCase,
    LlmStep,
    MetricConfig,
    MetricResult,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

R1 = "The agent calls get_balance first."
R2 = "The agent reports the balance in tBNB."


def trace(tid: str, tools: int = 1, user: str = "What is my balance?", agent: str = "wallet"):
    steps: list[Any] = [
        AgentStep(
            id=f"{tid}a",
            agent_id=agent,
            system_prompt="Answer wallet questions.",
            user_prompt=user,
            response="It is 1.5 tBNB.",
            available_tools=({"name": "get_balance", "description": "Balance lookup"},),
        )
    ]
    for i in range(tools):
        call = ToolCall(name="get_balance", arguments={"address": f"0x{i}"}, id=f"{tid}c{i}")
        steps.append(LlmStep(id=f"{tid}l{i}", response=f"thought {tid}-{i}", tool_calls=(call,)))
        steps.append(ToolStep(id=f"{tid}t{i}", call=call, result=ToolResult(content=str(i))))
    return Trace(id=tid, steps=tuple(steps))


def case(*traces: Trace, **kw: Any) -> EvalCase:
    return EvalCase(id="c", trajectory=Session(id="s", traces=traces), **kw)


async def run(
    metric_id: str, c: EvalCase, script: Any, params: dict[str, Any] | None = None
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=cls.spec.method(),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(c, ctx), judge


def rubrics(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {
        "evaluations": [{"property": t, "rationale": f"r {v}", "verdict": v} for t, v in pairs]
    }


def steps(**lists: list[int]) -> dict[str, Any]:
    return {"analysis": "a", **lists}


def chain(**scores: int) -> dict[str, Any]:
    return {
        "chain_scores": {k: {"score": v, "evidence": f"e{v}"} for k, v in scores.items()},
        "overall_reasoning": "summary",
    }


RUBRIC_PARAMS = {"rubrics": [{"id": "r1", "text": R1}, {"id": "r2", "text": R2}]}


def test_default_thresholds() -> None:
    expected = {
        "agent.goal_success": 0.7,
        "agent.tool_use_quality": 0.5,
        "agent.multi_agent_trajectory": 0.5,
        "agent.reasoning": 0.7,
        "agent.tool_parameter_accuracy": 0.5,
        "agent.adaptivity": 0.5,
        "agent.reasoning_hallucination": 0.5,
        "agent.trajectory_efficiency": 0.5,
        "agent.execution_chain": 0.7,
    }
    for metric_id, thr in expected.items():
        spec = registry.resolve(metric_id)[0].spec
        assert spec.default_threshold == thr
        assert spec.method().required_inputs == frozenset({"trajectory"})


# ------------------------------------------------------------------------ ratios
@pytest.mark.parametrize(
    ("metric_id", "reply", "score"),
    [
        # (adaptive + not relevant) / steps
        ("agent.adaptivity", steps(adaptive_steps=[1], not_adaptive_steps=[0, 2],
                                   not_relevant_steps=[3]), 0.5),
        ("agent.adaptivity", steps(adaptive_steps=[0, 1, 2], not_adaptive_steps=[],
                                   not_relevant_steps=[3]), 1.0),
        # 1 - hallucinated / steps
        ("agent.reasoning_hallucination", steps(hallucination_steps=[2], valid_steps=[]), 0.75),
        # 1 - inefficient / steps
        ("agent.trajectory_efficiency", steps(inefficient_steps=[1, 3], efficient_steps=[]), 0.5),
    ],
)  # fmt: skip
async def test_step_ratios(metric_id: str, reply: dict, score: float) -> None:
    res, judge = await run(metric_id, case(trace("t", tools=4)), reply)
    assert res.status == "scored" and res.score == pytest.approx(score)
    row = res.details["invocations"][0]
    assert row["steps"] == 4 and row["status"] == "scored"
    assert len(judge.requests) == 1


async def test_ratio_is_clamped() -> None:
    reply = steps(hallucination_steps=[0, 0, 1], valid_steps=[])
    res, _ = await run("agent.reasoning_hallucination", case(trace("t", tools=2)), reply)
    assert res.score == 0.0


async def test_step_prompts_show_indexed_steps() -> None:
    _, judge = await run(
        "agent.adaptivity",
        case(trace("t", tools=2)),
        steps(adaptive_steps=[], not_adaptive_steps=[], not_relevant_steps=[0, 1]),
    )
    user = judge.requests[0].user
    assert ' [0] **Agent\'s Thought:**: "thought t-0"' in user
    assert ' [1] Called "get_balance(address=0x1)"' in user
    assert ' [1] Result from get_balance(address=0x1): "1"' in user
    _, judge = await run(
        "agent.trajectory_efficiency",
        case(trace("t", tools=2)),
        steps(inefficient_steps=[], efficient_steps=[0, 1]),
    )
    assert ' [0] agent action: thought t-0: "get_balance(address=0x0)"' in judge.requests[0].user
    assert "It is 1.5 tBNB." in judge.requests[0].user  # final answer


async def test_adaptivity_without_tool_calls_but_delegation_is_one() -> None:
    t = Trace(
        id="t",
        steps=(
            AgentStep(id="a", user_prompt="Hi", response="Hello"),
            DelegationStep(id="d", from_agent="a", to_agent="b"),
        ),
    )
    reply = steps(adaptive_steps=[], not_adaptive_steps=[0], not_relevant_steps=[])
    res, judge = await run("agent.adaptivity", case(t), reply)
    assert res.score == 1.0 and len(judge.requests) == 1  # judged, but 0 steps -> 1
    res, _ = await run(
        "agent.trajectory_efficiency", case(t), steps(inefficient_steps=[0], efficient_steps=[])
    )
    assert res.score == 0.0  # steps floor at 1


# --------------------------------------------------------------- majority vote
async def test_tool_parameter_accuracy_labels_and_vote() -> None:
    for judgment, score in (("All", 1.0), ("Partial", 0.5), ("None", 0.0)):
        res, _ = await run(
            "agent.tool_parameter_accuracy",
            case(trace("t")),
            {"analysis": "a", "judgment": judgment},
        )
        assert res.score == score
    script = [{"analysis": "a", "judgment": j} for j in ("All", "Partial", "Partial")]
    res, judge = await run(
        "agent.tool_parameter_accuracy", case(trace("t")), script, {"samples": 3}
    )
    assert res.score == 0.5 and len(judge.requests) == 3
    assert res.details["invocations"][0]["sample_scores"] == [1.0, 0.5, 0.5]


async def test_ratio_vote_rounds_to_one_decimal() -> None:
    a = steps(inefficient_steps=[0], efficient_steps=[1, 2])  # 2/3
    b = steps(inefficient_steps=[], efficient_steps=[0, 1, 2])  # 1
    res, _ = await run("agent.trajectory_efficiency", case(trace("t", tools=3)), a)
    assert res.score == pytest.approx(2 / 3)  # one sample: not rounded
    res, _ = await run(
        "agent.trajectory_efficiency", case(trace("t", tools=3)), [a, b, a], {"samples": 3}
    )
    assert res.score == 0.7  # most common of (0.7, 1.0, 0.7)


@pytest.mark.parametrize(
    ("votes", "verdict", "score"),
    [
        (["yes", "no", "yes"], "yes", 1.0),
        (["no", "yes"], "no", 0.0),  # tie -> no
        (["nr", "nr", "yes"], "yes", 1.0),  # N/A votes do not count
    ],
)
async def test_rubric_majority_vote(votes: list[str], verdict: str, score: float) -> None:
    script = [rubrics((R1, v)) for v in votes]
    params = {"rubrics": [{"id": "r1", "text": R1}], "samples": len(votes)}
    res, _ = await run("agent.tool_use_quality", case(trace("t")), script, params)
    row = res.details["invocations"][0]["rubrics"][0]
    assert row["verdict"] == verdict and res.score == score


async def test_rubrics_matched_by_property_text() -> None:
    reply = rubrics((R2.upper(), "yes"), ("Unknown property", "no"))
    res, _ = await run("agent.tool_use_quality", case(trace("t")), reply, RUBRIC_PARAMS)
    verdicts = {r["id"]: r["verdict"] for r in res.details["invocations"][0]["rubrics"]}
    assert verdicts == {"r1": "not_applicable", "r2": "yes"} and res.score == 1.0
    res, _ = await run(
        "agent.tool_use_quality", case(trace("t")), rubrics((R1, "nr")), RUBRIC_PARAMS
    )
    assert res.status == "scored" and res.score is None  # nothing applicable


# --------------------------------------------------- invocations and NOT_EVALUATED
async def test_per_invocation_mean_over_two_traces() -> None:
    script = [{"analysis": "a", "judgment": "All"}, {"analysis": "a", "judgment": "None"}]
    res, judge = await run(
        "agent.tool_parameter_accuracy", case(trace("t1"), trace("t2", user="And now?")), script
    )
    assert res.score == 0.5 and len(judge.requests) == 2
    assert [r["score"] for r in res.details["invocations"]] == [1.0, 0.0]
    assert "What is my balance?" in judge.requests[0].user
    assert "And now?" in judge.requests[1].user
    assert res.reason.startswith("[Invocation 1] (PASS, 100%)")


async def test_rubric_scores_pool_verdicts_across_invocations() -> None:
    script = [rubrics((R1, "yes"), (R2, "nr")), rubrics((R1, "no"), (R2, "no"))]
    res, _ = await run(
        "agent.tool_use_quality", case(trace("t1"), trace("t2")), script, RUBRIC_PARAMS
    )
    # MeanInvocationResultsSummarizer: mean of all applicable verdicts (1 + 0 + 0) / 3.
    assert res.score == pytest.approx(1 / 3)
    assert [r["score"] for r in res.details["invocations"]] == [1.0, 0.0]
    assert res.details["rubric_scores"] == {"r1": 0.5, "r2": 0.0}


async def test_previous_turns_in_prompt() -> None:
    script = [rubrics((R1, "yes"))] * 2
    _, judge = await run(
        "agent.tool_use_quality",
        case(trace("t1", user="First question"), trace("t2", user="Second question")),
        script,
        RUBRIC_PARAMS,
    )
    assert "Conversation History:" not in judge.requests[0].user
    second = judge.requests[1].user
    assert "Turn 1:\nUser: First question\nTool use: # Conversation record" in second
    assert "Response: It is 1.5 tBNB." in second


@pytest.mark.parametrize(
    ("metric_id", "reply"),
    [
        ("agent.tool_parameter_accuracy", {"analysis": "a", "judgment": "None"}),
        ("agent.adaptivity", steps(adaptive_steps=[], not_adaptive_steps=[0],
                                   not_relevant_steps=[])),
        ("agent.reasoning_hallucination", steps(hallucination_steps=[0], valid_steps=[])),
        ("agent.trajectory_efficiency", steps(inefficient_steps=[0], efficient_steps=[])),
    ],
)  # fmt: skip
async def test_not_evaluated_invocations_score_one(metric_id: str, reply: dict) -> None:
    res, judge = await run(metric_id, case(trace("t1", tools=0)), reply)
    assert res.score == 1.0 and judge.requests == []
    row = res.details["invocations"][0]
    assert row["status"] == "not_evaluated" and res.details["not_evaluated"] == 1
    assert "NOT_EVALUATED" in res.reason
    # Mixed: the NOT_EVALUATED invocation counts as 1 in the mean.
    res, judge = await run(metric_id, case(trace("t1", tools=0), trace("t2")), reply)
    assert res.score == 0.5 and len(judge.requests) == 1


async def test_not_evaluated_in_rubric_metrics() -> None:
    res, judge = await run(
        "agent.reasoning", case(trace("t1", tools=0)), rubrics((DEFAULT_RUBRICS[0][1], "no"))
    )
    # AgentSight get_average_rubric_score([]) is None: every invocation skipped -> no score
    # (not 1.0, the step-ratio metrics' convention).
    assert res.score is None and judge.requests == []
    # The pooled rubric mean ignores invocations without rubric verdicts.
    res, _ = await run(
        "agent.reasoning",
        case(trace("t1", tools=0), trace("t2")),
        rubrics((DEFAULT_RUBRICS[0][1], "no")),
    )
    assert res.score == 0.0 and res.details["not_evaluated"] == 1


async def test_execution_chain_judges_every_invocation() -> None:
    res, judge = await run("agent.execution_chain", case(trace("t", tools=0)), chain())
    assert res.details["not_evaluated"] == 0 and len(judge.requests) == 3
    assert "No intermediate execution steps recorded." in judge.requests[0].user


async def test_empty_session_scores_zero() -> None:
    res, judge = await run("agent.adaptivity", case(), steps())
    assert res.score == 0.0 and judge.requests == []


# ------------------------------------------------------------- execution chain
def test_execution_chain_weights() -> None:
    assert CHAIN_WEIGHTS == {
        "intent_understanding": 0.2,
        "plan_quality": 0.2,
        "tool_selection": 0.15,
        "tool_execution": 0.15,
        "result_interpretation": 0.15,
        "output_quality": 0.15,
    }
    links = parse_links(
        chain(
            intent_understanding=5,
            plan_quality=3,
            tool_selection=-1,
            tool_execution=1,
            result_interpretation=9,  # clamped to 5
            output_quality=0,  # clamped to 1
        )
    )
    expected = (0.2 * 1 + 0.2 * 0.5 + 0.15 * 0 + 0.15 * 1 + 0.15 * 0) / 0.85
    assert reliability_score(links) == pytest.approx(expected)
    assert parse_links({"chain_scores": {}})["plan_quality"]["score"] == 3  # missing -> 3
    na = parse_links(chain(**dict.fromkeys(CHAIN_WEIGHTS, -1)))
    assert reliability_score(na) == 0.0


def test_execution_chain_median() -> None:
    samples = [
        parse_links(chain(**{**dict.fromkeys(CHAIN_WEIGHTS, s), "tool_selection": t}))
        for s, t in ((5, -1), (2, 4), (4, 3))
    ]
    links = aggregate_links(samples)
    assert links["intent_understanding"]["score"] == 4  # median(5, 2, 4)
    assert links["intent_understanding"]["evidence"] == "e4"  # sample closest to the median
    assert links["tool_selection"]["score"] == 4  # round(median(4, 3)) = round(3.5) = 4


async def test_execution_chain_default_three_samples_median() -> None:
    script = [chain(**dict.fromkeys(CHAIN_WEIGHTS, s)) for s in (5, 1, 3)]
    res, judge = await run("agent.execution_chain", case(trace("t")), script)
    assert len(judge.requests) == 3 and res.provenance.samples == 3
    assert res.score == 0.5  # every link median 3 -> (3 - 1) / 4
    row = res.details["invocations"][0]
    assert row["sample_scores"] == [1.0, 0.0, 0.5]
    res, judge = await run("agent.execution_chain", case(trace("t")), script, {"samples": 1})
    assert len(judge.requests) == 1 and res.score == 1.0
    user = judge.requests[0].user
    assert "#### Agent: wallet\n```\nAnswer wallet questions.\n```" in user
    assert "[0] Thought (by None)" not in user and '[0] Thought: "thought t-0"' in user
    assert "**wallet**: get_balance" in user and "No previous conversation history." in user


# ------------------------------------------------------------------ reasoning
async def test_reasoning_seven_rubrics() -> None:
    texts = [t for _, t in DEFAULT_RUBRICS]
    assert [i for i, _ in DEFAULT_RUBRICS] == [
        "logical_progression",
        "evidence_based",
        "uncertainty_handling",
        "tool_selection",
        "coherent_explanation",
        "problem_decomposition",
        "assumption_clarity",
    ]
    verdicts = ["yes"] * 5 + ["no", "nr"]
    res, judge = await run(
        "agent.reasoning", case(trace("t")), rubrics(*zip(texts, verdicts, strict=True))
    )
    assert res.score == pytest.approx(5 / 6)
    assert [r["id"] for r in res.details["invocations"][0]["rubrics"]] == [
        i for i, _ in DEFAULT_RUBRICS
    ]
    user = judge.requests[0].user
    assert "<properties>\n" + "\n*  ".join(texts) + "\n</properties>" in user
    assert ' [0] **Agent\'s Thought:**: "thought t-0"' in user
    res, _ = await run(
        "agent.reasoning",
        case(trace("t")),
        rubrics((R1, "yes")),
        {"rubrics": [{"id": "own", "text": R1}]},
    )
    assert res.score == 1.0 and res.details["rubric_scores"] == {"own": 1.0}


# ------------------------------------------------------------ tool use quality
async def test_tool_use_quality_prompt_versions() -> None:
    reply = rubrics((R1, "yes"), (R2, "no"))
    c = case(trace("t"))
    res, judge = await run("agent.tool_use_quality", c, reply, RUBRIC_PARAMS)
    assert res.details["prompt_version"] == "v2" and res.score == 0.5  # source default
    assert "<previous_turns>" in judge.requests[0].user
    assert '"verdict": "yes/no/nr"' in judge.requests[0].system
    res, judge = await run(
        "agent.tool_use_quality", c, reply, {**RUBRIC_PARAMS, "prompt_version": "v1"}
    )
    assert res.details["prompt_version"] == "v1" and res.score == 0.5
    assert "<previous_turns>" not in judge.requests[0].user
    assert "Respond in pure text, not json." in judge.requests[0].user
    hashes = {
        registry.resolve("agent.tool_use_quality")[0]()
        .template_for({**RUBRIC_PARAMS, "prompt_version": v})
        .hash()
        for v in ("v0", "v1", "v2")
    }
    assert len(hashes) == 3
    for verdict, score in (("correct", 1.0), ("incorrect", 0.0)):
        res, judge = await run(
            "agent.tool_use_quality", c, {"verdict": verdict}, {"prompt_version": "v0"}
        )
        assert res.score == score and res.details["invocations"][0]["verdict"] == verdict
    user = judge.requests[0].user
    assert "Actual Trajectory:" in user and "get_balance" in user and "User Input:" in user
    assert "`correct` or `incorrect`" in judge.requests[0].system
    res, _ = await run("agent.tool_use_quality", c, reply)
    assert res.status == "error" and "rubrics" in res.error.message


# ------------------------------------------------------------ multi-agent
async def test_multi_agent_trajectory_prompt() -> None:
    t = Trace(
        id="t",
        steps=(
            AgentStep(id="a", agent_id="planner", user_prompt="Swap 1 tBNB", response="Done."),
            DelegationStep(id="d", agent_id="planner", from_agent="planner", to_agent="swapper"),
            ToolStep(id="s", agent_id="swapper", call=ToolCall(name="swap", arguments={"x": 1})),
        ),
    )
    res, judge = await run(
        "agent.multi_agent_trajectory",
        case(t),
        rubrics((R1, "yes")),
        {"rubrics": [R1]},
    )
    assert res.score == 1.0 and res.details["invocations"][0]["rubrics"][0]["id"] == "rubric_1"
    user = judge.requests[0].user
    assert "planner-swapper" in user and "[Delegated: planner -> swapper]" in user
    assert "Action [swapper]: swap" in user and "Done." in user
    assert "No tools available." in user


# ------------------------------------------------------------- goal success
async def test_goal_success_mean_of_goals() -> None:
    goals = [
        {"goal_description": f"g{i}", "goal_success_score": s} for i, s in enumerate((1, 0.5, 0))
    ]
    res, judge = await run(
        "agent.goal_success", case(trace("t")), {"reasoning": "r", "goals": goals}
    )
    assert res.score == 0.5 and res.details["goals"] == goals and res.details["mode"] == "simple"
    assert "# Conversation record" in judge.requests[0].user
    res, _ = await run("agent.goal_success", case(trace("t")), {"reasoning": "r", "goals": []})
    assert res.score == 0.0
    res, judge = await run(
        "agent.goal_success",
        case(trace("t"), expected="The balance is reported."),
        {"reasoning": "r", "goals": goals[:1]},
    )
    assert res.score == 1.0 and res.details["mode"] == "with_expected"
    assert "# Expected outcome" in judge.requests[0].user
