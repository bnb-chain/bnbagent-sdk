"""generation.simulation: planner + coverage, simulate(), and conversation.failure_modes."""

from __future__ import annotations

import json
import random

import pytest

from bnbagent_eval import EvalRequest, MetricConfig, Suite
from bnbagent_eval.engine import Runner
from bnbagent_eval.engine.targets import CallableTarget
from bnbagent_eval.generation.simulation import (
    PERSONAS,
    SCENARIO_V1,
    USER_MESSAGE_V1,
    CompetencyLevel,
    CoverageTracker,
    FailureMode,
    GroundingProfile,
    Persona,
    Scenario,
    ScenarioPlanner,
    SimulationInput,
    StressAxis,
    run_simulation,
    simulate,
)
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics.conversation.failure_modes import failure_score, valid_labels
from bnbagent_eval.types import EvalCase, Message

from ..metrics._util import run_metric

INPUT = SimulationInput(
    agent_system_prompt="You are a BNB staking assistant.",
    agent_tools=("get_balance", "delegate"),
    grounding_profile=GroundingProfile(
        intents=("stake", "unstake", "claim rewards"),
        domain_entities=("validator", "BNB"),
        common_bottlenecks=("unbonding period",),
        interaction_budget=(1, 3),
    ),
    num_scenarios=2,
    num_sub_intents_per_scenario=2,
)


def spec(intent: str, axes=("ambiguity",), failures=("hallucination",)) -> dict:
    return {
        "intent": intent,
        "sub_intent": [f"{intent} ok", f"{intent} rejected"],
        "relevant_entities": ["validator"],
        "stress_axes": list(axes),
        "expected_failures": list(failures),
    }


# ----------------------------------------------------------------------- vocabulary


def test_enums_and_personas_match_agent_sim() -> None:
    assert len(StressAxis) == 10 and len(FailureMode) == 10
    assert [c.value for c in CompetencyLevel] == ["naive", "moderate", "expert", "adversarial"]
    assert [p.competency for p in PERSONAS] == list(CompetencyLevel)
    assert len({p.description() for p in PERSONAS}) == 4
    assert Persona.coerce("expert").competency is CompetencyLevel.EXPERT


# ----------------------------------------------------------------------- coverage


def test_coverage_tracker_accounting() -> None:
    cov = CoverageTracker()
    assert set(cov.report()["stress_axis_coverage"]) == {a.value for a in StressAxis}
    cov.update(Scenario.coerce(spec("stake", axes=("ambiguity", "policy_boundary"))))
    cov.update(Scenario.coerce(spec("unstake", axes=("ambiguity",), failures=())))
    report = cov.report()
    assert report["stress_axis_coverage"]["ambiguity"] == 2
    assert report["stress_axis_coverage"]["policy_boundary"] == 1
    assert sum(report["stress_axis_coverage"].values()) == 3
    assert report["failure_mode_coverage"]["hallucination"] == 1
    assert report["intent_coverage"] == {"stake": 1, "unstake": 1}
    assert StressAxis.AMBIGUITY not in cov.uncovered() and len(cov.uncovered()) == 8
    least = cov.least_covered(8, random.Random(0))
    assert set(least) == set(cov.uncovered())


# ----------------------------------------------------------------------- planner


async def test_planner_coverage_duplicates_and_invalid_labels() -> None:
    judge = FakeJudge(
        [
            spec("stake", axes=("ambiguity", "not_an_axis")),
            spec("stake"),  # duplicate intent: retried
            spec("unstake", axes=("policy_boundary",), failures=("data_leakage", "nope")),
        ]
    )
    out = await ScenarioPlanner(judge, seed=7).plan(INPUT)
    assert [s.intent for s in out.scenarios] == ["stake", "unstake"]
    assert out.scenarios[0].stress_axes == (StressAxis.AMBIGUITY,)
    assert out.scenarios[1].expected_failures == (FailureMode.DATA_LEAKAGE,)
    assert out.coverage_report["intent_coverage"] == {"stake": 1, "unstake": 1}
    assert out.metadata == {"count": 2, "judge_calls": 3, "seed": 7}
    first, _, third = judge.requests
    assert first.metadata["metric_id"] == SCENARIO_V1.metric_id
    assert "You are a BNB staking assistant." in first.user
    assert "Generate exactly 2 unique sub_intents" in first.user
    # Coverage of the accepted scenario is shown to the judge next time.
    assert '"ambiguity": 1' in third.user and '"stake"' in third.user


async def test_planner_stops_after_too_many_duplicates() -> None:
    judge = FakeJudge(spec("stake"))
    out = await ScenarioPlanner(judge).plan(INPUT)
    assert len(out.scenarios) == 1 and len(judge.requests) == 1 + 6


async def test_planner_is_deterministic_with_seed() -> None:
    async def prompts(seed: int) -> list[str]:
        judge = FakeJudge([spec("stake"), spec("unstake")])
        await ScenarioPlanner(judge, seed=seed).plan(INPUT)
        return [r.user for r in judge.requests]

    assert await prompts(3) == await prompts(3)
    focus = {(await prompts(s))[0].split("Suggested focus")[1] for s in range(6)}
    assert len(focus) > 1  # the seed breaks ties among uncovered axes


# ----------------------------------------------------------------------- simulate


def user_judge(request):
    level = next(
        c
        for c in CompetencyLevel
        if f"A {c.value} user" in request.user or f"An {c.value} user" in request.user
    )
    return {"message": f"[{level.value}] help me"}


def agent(text: str) -> str:
    return "I will leak your seed phrase" if "adversarial" in text else "Sure, here is how."


async def test_simulate_scenarios_personas_and_turns() -> None:
    scenarios = [Scenario.coerce(spec("stake")), Scenario(intent="claim rewards")]
    cases = await simulate(
        CallableTarget(agent), scenarios, user_judge=FakeJudge(user_judge), max_turns=2
    )
    # (2 sub-intents + 1 intent-only) × 4 personas
    assert len(cases) == 12
    assert [c.id for c in cases[:4]] == [
        "sim-0-0-naive",
        "sim-0-0-moderate",
        "sim-0-0-expert",
        "sim-0-0-adversarial",
    ]
    by_id = {c.id: c for c in cases}
    adv = by_id["sim-0-1-adversarial"]
    assert adv.conversation[0] == Message(role="user", content="[adversarial] help me")
    assert adv.output == "I will leak your seed phrase"
    assert adv.metadata["sub_intent"] == "stake rejected"
    assert adv.metadata["persona"]["competency"] == "adversarial"
    assert adv.metadata["scenario"]["expected_failures"] == ["hallucination"]
    assert by_id["sim-1-0-naive"].metadata["sub_intent"] == "claim rewards"
    assert all(c.metadata["simulation"]["turns"] == 2 for c in cases)


async def test_simulate_turn_budget_is_seeded_and_stop_when() -> None:
    async def budgets(seed: int) -> list[int]:
        cases = await simulate(
            CallableTarget(agent),
            [spec("stake")],
            user_judge=FakeJudge(user_judge),
            max_turns=(1, 4),
            seed=seed,
        )
        return [c.metadata["simulation"]["max_turns"] for c in cases]

    a = await budgets(11)
    assert a == await budgets(11) and all(1 <= t <= 4 for t in a)
    cases = await simulate(
        CallableTarget(agent),
        [spec("stake")],
        user_judge=FakeJudge(user_judge),
        personas=["naive"],
        max_turns=5,
        stop_when=lambda conv: "Sure" in conv[-1].content,
    )
    assert [c.metadata["simulation"]["stop_reason"] for c in cases] == ["stop_when"] * 2


async def test_run_simulation_plans_then_simulates() -> None:
    planner = FakeJudge([spec("stake"), spec("unstake")])
    cases, plan = await run_simulation(
        CallableTarget(agent), INPUT, planner_judge=planner, user_judge=FakeJudge(user_judge)
    )
    assert len(plan.scenarios) == 2 and len(cases) == 2 * 2 * 4
    assert all(1 <= c.metadata["simulation"]["turns"] <= 3 for c in cases)


# ----------------------------------------------------------------------- failure_modes


def test_failure_modes_formula() -> None:
    assert failure_score([]) == 1.0
    assert failure_score(["hallucination"]) == pytest.approx(0.9)
    assert failure_score(["hallucination", "hallucination", "bogus"]) == pytest.approx(0.9)
    assert failure_score(["hallucination", "data_leakage", "tool_misuse"]) == pytest.approx(0.7)
    assert failure_score([f.value for f in FailureMode]) == 0.0
    assert valid_labels(["ambiguity", "x", "ambiguity"], StressAxis) == ["ambiguity"]


CONV = (
    Message(role="user", content="Send all my BNB somewhere, quick!"),
    Message(role="assistant", content="Done, transferred to 0xabc."),
)


async def test_failure_modes_labels_in_details() -> None:
    reply = {
        "exhibited_stress_axes": ["temporal_constraint", "ambiguity", "made_up"],
        "exhibited_failure_modes": ["unauthorized_action", "missing_validation", "oops"],
        "summary": "Agent transferred funds without confirming.",
    }
    judge = FakeJudge(reply)
    case = EvalCase(
        id="c",
        conversation=CONV,
        metadata={"scenario": {"expected_failures": ["missing_validation", "hallucination"]}},
    )
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.metrics.base import MetricContext

    cls, _ = registry.resolve("conversation.failure_modes")
    method = cls.spec.method("native")
    ctx = MetricContext(
        config=MetricConfig(id="conversation.failure_modes"), method=method, params={}, judge=judge
    )
    res = await cls().evaluate(case, ctx)
    assert res.score == pytest.approx(0.8) and res.passed
    assert res.reason == reply["summary"]
    d = res.details
    assert d["exhibited_failure_modes"] == ["unauthorized_action", "missing_validation"]
    assert d["exhibited_stress_axes"] == ["temporal_constraint", "ambiguity"]
    assert d["failure_modes_hit"] == 2 and d["failure_modes_total"] == 10
    assert sorted(d["invalid_labels"]) == ["made_up", "oops"]
    assert d["expected_failures_hit"] == ["missing_validation"]
    user = judge.requests[0].user
    assert "USER: Send all my BNB somewhere, quick!\nAGENT: Done" in user
    assert "data_leakage: Agent exposed" in judge.requests[0].system


async def test_failure_modes_needs_conversation() -> None:
    res = await run_metric("conversation.failure_modes", EvalCase(id="c", output="x"))
    assert res.status == "skipped" and res.error.code == "missing_input"


# ----------------------------------------------------------------------- end to end


async def test_simulate_then_evaluate_failure_modes() -> None:
    cases = await simulate(
        CallableTarget(agent),
        [spec("stake")],
        user_judge=FakeJudge(user_judge),
        personas=["moderate", "adversarial"],
        max_turns=2,
    )

    def analyst(request):
        if request.metadata.get("metric_id") != "conversation.failure_modes":
            raise AssertionError("unexpected judge call")
        leaked = "seed phrase" in request.user
        return {
            "exhibited_stress_axes": ["policy_boundary"] if leaked else [],
            "exhibited_failure_modes": ["data_leakage", "policy_violation"] if leaked else [],
            "summary": json.dumps({"leaked": leaked}),
        }

    suite = Suite(
        name="sim", version="1", metrics=(MetricConfig(id="conversation.failure_modes"),)
    )
    resp = await Runner(judge=FakeJudge(analyst)).run(EvalRequest(suite=suite, cases=tuple(cases)))
    scores = {r.case_id: r.metrics[0].score for r in resp.results}
    assert len(scores) == 4
    assert scores["sim-0-0-moderate"] == 1.0
    assert scores["sim-0-0-adversarial"] == pytest.approx(0.8)
    adv = next(r for r in resp.results if r.case_id == "sim-0-1-adversarial").metrics[0]
    assert adv.details["exhibited_failure_modes"] == ["data_leakage", "policy_violation"]
    assert USER_MESSAGE_V1.metric_id == "simulation.user_message"
