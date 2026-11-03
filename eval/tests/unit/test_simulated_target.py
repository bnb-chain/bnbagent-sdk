"""SimulatedUserTarget: persona-driven user turns, stop conditions, transcript and metrics."""

from __future__ import annotations

import pytest

from bnbagent_eval.engine.simulated import (
    USER_MESSAGE_V1,
    SimulatedUserTarget,
    history_text,
)
from bnbagent_eval.engine.targets import CallableTarget
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import EvalCase, Message
from bnbagent_eval.types.simulation import (
    COMPETENCY_LEVEL_DESCRIPTIONS,
    CompetencyLevel,
    FailureMode,
    Persona,
    Scenario,
    StressAxis,
)

SCENARIO = Scenario(
    intent="unstake BNB",
    sub_intent=("unstake rejected - still in unbonding period",),
    relevant_entities=("validator",),
    stress_axes=(StressAxis.AMBIGUITY,),
    expected_failures=(FailureMode.HALLUCINATION,),
)


def persona_judge(request):
    """The user's wording depends on the competency description in the prompt."""
    for level, text in COMPETENCY_LEVEL_DESCRIPTIONS.items():
        if text in request.user:
            return {"message": f"{level.value} asks"}
    return {"message": "?"}


def echo_agent(text: str) -> str:
    return f"agent: {text}"


def target(**kw) -> SimulatedUserTarget:
    kw.setdefault("persona", CompetencyLevel.NAIVE)
    kw.setdefault("scenario", SCENARIO)
    kw.setdefault("user_judge", FakeJudge(persona_judge))
    return SimulatedUserTarget(CallableTarget(echo_agent), **kw)


async def test_runs_max_turns_and_records_transcript() -> None:
    run = await target(max_turns=3).invoke(EvalCase(id="c"))
    assert [m.role for m in run.conversation] == ["user", "assistant"] * 3
    assert run.conversation[0].content == "naive asks"
    assert run.output == "agent: naive asks"
    assert run.input == "naive asks"
    sim = run.metadata["simulation"]
    assert sim == {"turns": 3, "max_turns": 3, "stop_reason": "max_turns"}
    assert run.metadata["persona"]["competency"] == "naive"
    assert run.metadata["scenario"]["intent"] == "unstake BNB"
    assert run.metadata["sub_intent"] == SCENARIO.sub_intent[0]
    assert run.app_metrics.error is None and run.app_metrics.latency_ms is not None


async def test_competency_levels_change_user_turns() -> None:
    outs = {}
    for level in CompetencyLevel:
        run = await target(persona=level, max_turns=1).invoke(EvalCase(id=level.value))
        outs[level] = run.conversation[0].content
    assert len(set(outs.values())) == 4
    assert outs[CompetencyLevel.ADVERSARIAL] == "adversarial asks"


async def test_prompt_contains_persona_scenario_and_fenced_history() -> None:
    judge = FakeJudge(persona_judge)
    persona = Persona(CompetencyLevel.EXPERT, traits="Speaks tersely.", utterance_length=12)
    await target(persona=persona, user_judge=judge, max_turns=2).invoke(EvalCase(id="c"))
    first, second = judge.requests
    assert first.metadata["metric_id"] == USER_MESSAGE_V1.metric_id
    assert "Additional persona traits:\nSpeaks tersely." in first.user
    assert "under 12 words" in first.user and "['ambiguity']" in first.user
    assert "No previous conversation" in first.user
    nonce = first.system.split("<untrusted-")[1].split(">")[0]
    assert f'<untrusted-{nonce} field="history">' in first.user
    assert "USER: expert asks\nAGENT: agent: expert asks" in second.user


async def test_stop_when_and_user_done() -> None:
    run = await target(max_turns=5, stop_when=lambda conv: len(conv) >= 4).invoke(EvalCase(id="c"))
    assert run.metadata["simulation"]["stop_reason"] == "stop_when"
    assert len(run.conversation) == 4

    async def async_stop(conv) -> bool:
        return "agent:" in conv[-1].content

    run = await target(max_turns=5, stop_when=async_stop).invoke(EvalCase(id="c"))
    assert run.metadata["simulation"]["turns"] == 1

    judge = FakeJudge([{"message": "hi"}, {"message": "", "done": True}])
    run = await target(user_judge=judge, max_turns=5).invoke(EvalCase(id="c"))
    assert run.metadata["simulation"] == {"turns": 1, "max_turns": 5, "stop_reason": "user_done"}


async def test_inner_receives_prior_turns_and_errors_stop() -> None:
    seen = []

    def agent(case: EvalCase) -> str:
        seen.append((case.input, len(case.conversation)))
        if len(seen) == 2:
            raise RuntimeError("down")
        return "ok"

    sim = SimulatedUserTarget(
        CallableTarget(agent, pass_case=True), "moderate", SCENARIO, FakeJudge(persona_judge), 4
    )
    run = await sim.invoke(EvalCase(id="c"))
    assert seen == [("moderate asks", 0), ("moderate asks", 2)]
    assert run.metadata["simulation"]["stop_reason"] == "target_error"
    assert "down" in run.app_metrics.error and run.output == "ok"


async def test_judge_failure_is_recorded_not_raised() -> None:
    run = await target(user_judge=FakeJudge(RuntimeError("judge down"))).invoke(EvalCase(id="c"))
    assert run.metadata["simulation"]["stop_reason"] == "error"
    assert "judge down" in run.app_metrics.error and run.conversation == ()


async def test_scenario_and_persona_from_case_metadata() -> None:
    sim = SimulatedUserTarget(
        CallableTarget(echo_agent), user_judge=FakeJudge(persona_judge), max_turns=1
    )
    case = EvalCase(
        id="c",
        metadata={"persona": {"competency": "expert"}, "scenario": SCENARIO.to_dict()},
    )
    run = await sim.invoke(case)
    assert run.conversation[0].content == "expert asks"
    # Fallback: the case input is the intent.
    run = await sim.invoke(EvalCase(id="d", input="swap tokens"))
    assert run.metadata["scenario"]["intent"] == "swap tokens"
    assert run.metadata["persona"]["competency"] == "moderate"


def test_validation_and_history_format() -> None:
    with pytest.raises(ValueError):
        SimulatedUserTarget(CallableTarget(echo_agent), max_turns=1)
    with pytest.raises(ValueError):
        target(max_turns=0)
    assert history_text(()) == "No previous conversation"
    msgs = (Message(role="user", content="a"), Message(role="assistant", content="b"))
    assert history_text(msgs) == "USER: a\nAGENT: b"
    data = Scenario.coerce({"intent": "x", "stress_axes": ["ambiguity", "bogus"]})
    assert data.stress_axes == (StressAxis.AMBIGUITY,)


async def test_runner_builds_simulated_user_target_from_ref() -> None:
    from bnbagent_eval import EvalRequest, MetricConfig, Suite
    from bnbagent_eval.engine import Runner
    from bnbagent_eval.engine.simulated import SimulatedUserTarget
    from bnbagent_eval.engine.targets import from_ref, register_target
    from bnbagent_eval.exceptions import RequestError
    from bnbagent_eval.judges.providers.fake import FakeJudge
    from bnbagent_eval.types import EvalCase, TargetRef

    register_target("sim_echo", lambda text: f"echo: {text}")
    ref = TargetRef(
        kind="callable",
        uri="sim_echo",
        simulated_user={"persona": "expert", "scenario": "check BNB fees", "max_turns": 2},
    )
    with pytest.raises(RequestError, match="judge"):
        from_ref(ref)
    judge = FakeJudge({"message": "What are the fees?", "done": False})
    assert isinstance(from_ref(ref, user_judge=judge), SimulatedUserTarget)
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="text.length", params={"max_words": 100}),)
    )
    resp = await Runner(judge=judge).run(
        EvalRequest(suite=suite, cases=(EvalCase(id="1"),), target=ref)
    )
    assert resp.results[0].metrics[0].status == "scored"
