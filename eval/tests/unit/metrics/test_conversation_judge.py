"""Golden tests for conversation.* judge metrics (FakeJudge)."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import Context, EvalCase, Message, MetricConfig, MetricResult

CONVERSATION = (
    Message(role="user", content="My validator is Alpha Node. How do I stake?"),
    Message(role="assistant", content="Open the staking page and pick Alpha Node."),
    Message(role="user", content="What is the minimum?"),
    Message(role="assistant", content="The minimum delegation is 1 BNB."),
)
CONTEXTS = (Context(text="The minimum delegation is 1 BNB.", source="docs/staking.md"),)
EXPECTED = "Explain how to stake with Alpha Node and state the 1 BNB minimum."

PORTED = {
    "conversation.coherence": (
        "level",
        ["Incoherent", "Mostly Incoherent", "Somewhat Coherent", "Coherent", "Highly Coherent"],
        "coherence_issues",
        "Evaluate the conversational coherence across these turns.",
    ),
    "conversation.completeness": (
        "level",
        ["Incomplete", "Mostly Incomplete", "Partially Complete", "Mostly Complete"]
        + ["Fully Complete"],
        "unaddressed_items",
        "Evaluate the conversation completeness across these turns.",
    ),
    "conversation.knowledge_retention": (
        "level",
        ["No Retention", "Poor Retention", "Partial Retention", "Good Retention"]
        + ["Perfect Retention"],
        "forgotten_items",
        "Evaluate the knowledge retention across these turns.",
    ),
    "conversation.entity_consistency": (
        "score",
        ["Poor", "Below Average", "Average", "Good", "Excellent"],
        "inconsistencies",
        "Evaluate entity consistency across these conversation turns.",
    ),
}
FIVE_TIER = ["Poor", "Below Average", "Average", "Good", "Excellent"]
NEW = {
    "conversation.turn_relevancy": {},
    "conversation.role_adherence": {"role": "staking support agent"},
    "conversation.topic_adherence": {"topics": ["staking"]},
    "conversation.turn_faithfulness": {},
    "conversation.turn_context_precision": {},
    "conversation.turn_context_recall": {},
    "conversation.turn_context_relevancy": {},
}
ALL = [*PORTED, *NEW]


def reply(metric_id: str, tier: int = 4) -> dict[str, Any]:
    field, labels = PORTED[metric_id][:2] if metric_id in PORTED else ("score", FIVE_TIER)
    return {"reasoning": "r", field: labels[tier]}


async def judge_run(
    metric_id: str, case: EvalCase, script: Any, params: dict[str, Any] | None = None
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params if params is not None else NEW.get(metric_id, {})
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=cls.spec.method(),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(case, ctx), judge


def conv(**kw: Any) -> EvalCase:
    return EvalCase(id="c", conversation=CONVERSATION, contexts=CONTEXTS, expected=EXPECTED, **kw)


@pytest.mark.parametrize("metric_id", list(PORTED))
@pytest.mark.parametrize("tier", range(5))
async def test_ported_label_mapping(metric_id: str, tier: int) -> None:
    field, labels, extra, _ = PORTED[metric_id]
    reply = {"reasoning": "why", field: labels[tier], extra: ["turn 2"]}
    res, _ = await judge_run(metric_id, conv(), reply)
    assert res.status == "scored" and res.score == tier * 0.25
    assert res.details["label"] == labels[tier] and res.details[extra] == ["turn 2"]
    assert res.details["mode"] == "conversation" and res.reason == "why"
    assert field not in res.details
    assert res.passed is (tier * 0.25 >= 0.5)


@pytest.mark.parametrize("metric_id", list(NEW))
@pytest.mark.parametrize("tier", range(5))
async def test_new_five_tier_label_mapping(metric_id: str, tier: int) -> None:
    res, _ = await judge_run(metric_id, conv(), {"reasoning": "r", "score": FIVE_TIER[tier]})
    assert res.status == "scored" and res.score == tier * 0.25
    assert res.details["label"] == FIVE_TIER[tier]


@pytest.mark.parametrize("metric_id", list(PORTED))
async def test_ported_prompt_is_agentsight_text(metric_id: str) -> None:
    field, labels, _, task = PORTED[metric_id]
    _, judge = await judge_run(metric_id, conv(), {"reasoning": "r", field: labels[-1]})
    user = judge.requests[0].user
    assert user.startswith("# Conversation History:\n")
    assert user.endswith(f"\n\n# Task:\n{task}")
    template = registry.resolve(metric_id)[0].templates["v1"]
    assert judge.requests[0].system.startswith(template.system)
    assert template.source.startswith("agentsight:")


@pytest.mark.parametrize("metric_id", ALL)
async def test_conversation_is_formatted_as_role_turns(metric_id: str) -> None:
    res, judge = await judge_run(metric_id, conv(), reply(metric_id, 3))
    assert res.score == 0.75
    user = judge.requests[0].user
    assert (
        "User: My validator is Alpha Node. How do I stake?\n\n"
        "Assistant: Open the staking page and pick Alpha Node.\n\n"
        "User: What is the minimum?\n\n"
        "Assistant: The minimum delegation is 1 BNB."
    ) in user


@pytest.mark.parametrize("metric_id", list(NEW))
def test_new_prompts_record_deepeval_provenance(metric_id: str) -> None:
    template = registry.resolve(metric_id)[0].templates["v1"]
    assert template.source.startswith("bnbagent-eval:new (cf. deepeval:")
    assert template.labels == dict(zip(FIVE_TIER, (0.0, 0.25, 0.5, 0.75, 1.0), strict=True))


@pytest.mark.parametrize(
    ("metric_id", "params", "rendered"),
    [
        (
            "conversation.role_adherence",
            {"role": "polite staking helper"},
            "polite staking helper",
        ),
        ("conversation.topic_adherence", {"topics": ["staking", "fees"]}, "staking, fees"),
    ],
)
async def test_params_reach_the_prompt(metric_id: str, params: dict, rendered: str) -> None:
    _, judge = await judge_run(metric_id, conv(), {"reasoning": "r", "score": "Good"}, params)
    assert rendered in judge.requests[0].user


@pytest.mark.parametrize(
    ("metric_id", "param"),
    [("conversation.role_adherence", "role"), ("conversation.topic_adherence", "topics")],
)
def test_params_required(metric_id: str, param: str) -> None:
    spec = registry.resolve(metric_id)[0].spec
    assert any(param in e for e in spec.validate_params({}))


def test_topics_must_be_a_non_empty_list() -> None:
    spec = registry.resolve("conversation.topic_adherence")[0].spec
    assert spec.validate_params({"topics": []})
    assert spec.validate_params({"topics": "staking"})
    assert not spec.validate_params({"topics": ["staking"]})


def test_input_mode_is_conversation_only() -> None:
    spec = registry.resolve("conversation.coherence")[0].spec
    assert not spec.validate_params({"input_mode": "conversation"})
    assert spec.validate_params({"input_mode": "simple"})


@pytest.mark.parametrize(
    ("metric_id", "inputs"),
    [
        ("conversation.turn_faithfulness", "contexts"),
        ("conversation.turn_context_precision", "contexts expected"),
        ("conversation.turn_context_recall", "contexts expected"),
        ("conversation.turn_context_relevancy", "contexts"),
    ],
)
async def test_context_metrics_skip_without_contexts(metric_id: str, inputs: str) -> None:
    method = registry.resolve(metric_id)[0].spec.method()
    assert method.required_inputs == {"conversation", *inputs.split()}
    case = EvalCase(id="c", conversation=CONVERSATION, expected=EXPECTED)
    res, judge = await judge_run(metric_id, case, {"reasoning": "r", "score": "Excellent"})
    assert res.status == "skipped" and res.error.code == "missing_input"
    assert judge.requests == []


async def test_context_and_expected_reach_the_prompt() -> None:
    reply = {"reasoning": "r", "score": "Excellent"}
    _, judge = await judge_run("conversation.turn_context_recall", conv(), reply)
    user = judge.requests[0].user
    assert "[1] The minimum delegation is 1 BNB. (source: docs/staking.md)" in user
    assert EXPECTED in user


@pytest.mark.parametrize("metric_id", ALL)
async def test_all_skip_without_conversation(metric_id: str) -> None:
    case = EvalCase(id="c", input="hi", output="hello", contexts=CONTEXTS, expected=EXPECTED)
    res, judge = await judge_run(metric_id, case, reply(metric_id))
    assert res.status == "skipped" and res.error.code == "missing_input"
    assert judge.requests == []


async def test_single_message_conversation_still_uses_conversation_mode() -> None:
    case = EvalCase(id="c", conversation=CONVERSATION[:1])
    res, judge = await judge_run(
        "conversation.turn_relevancy", case, {"reasoning": "r", "score": "Good"}
    )
    assert res.details["mode"] == "conversation"
    assert "User: My validator is Alpha Node." in judge.requests[0].user


async def test_samples_take_lower_median() -> None:
    script = [
        {"reasoning": "a", "level": "Highly Coherent"},
        {"reasoning": "b", "level": "Incoherent"},
        {"reasoning": "c", "level": "Coherent"},
    ]
    res, _ = await judge_run("conversation.coherence", conv(), script, {"samples": 3})
    assert res.score == 0.75 and res.details["sample_scores"] == [1.0, 0.0, 0.75]
    assert res.reason == "c"


async def test_trajectory_turns_count_as_conversation() -> None:
    from bnbagent_eval.judges.providers.fake import FakeJudge
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.metrics.base import MetricContext
    from bnbagent_eval.types import AgentStep, EvalCase, MetricConfig, Session, Trace

    session = Session(
        id="s",
        traces=(
            Trace(
                id="t",
                steps=(
                    AgentStep(
                        id="a1", user_prompt="What is BNB?", response="The BNB Chain token."
                    ),
                ),
            ),
        ),
    )
    cls, _ = registry.resolve("conversation.turn_relevancy")
    judge = FakeJudge({"reasoning": "r", "score": "Good"})
    ctx = MetricContext(
        config=MetricConfig(id="conversation.turn_relevancy"),
        method=cls.spec.method(),
        params={},
        judge=judge,
    )
    res = await cls().evaluate(EvalCase(id="c", trajectory=session), ctx)
    assert res.score == 0.75 and "User: What is BNB?" in judge.requests[0].user
