"""Golden tests for the quality.* judge metrics (FakeJudge)."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.judges.prompts import FIVE_TIER
from bnbagent_eval.judges.prompts.quality import completeness as completeness_prompts
from bnbagent_eval.judges.prompts.quality import conciseness as conciseness_prompts
from bnbagent_eval.judges.prompts.quality import contradiction as contradiction_prompts
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.metrics.quality.completeness import score_to_label
from bnbagent_eval.types import Context, EvalCase, Message, MetricConfig, MetricResult

Q = "How do I stake BNB?"
A = "Open the staking page and choose a validator."
CONV = (Message(role="user", content="Hi, how do I stake?"), Message(role="assistant", content=A))


def reply(score: str = "Good", **extra: Any) -> dict[str, Any]:
    return {"reasoning": f"because {score}", "score": score, **extra}


async def run(
    metric_id: str,
    case: EvalCase,
    script: Any = None,
    params: dict[str, Any] | None = None,
    method: str | None = None,
    **config: Any,
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params, **config),
        method=cls.spec.method(method),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(case, ctx), judge


def case(**kw: Any) -> EvalCase:
    return EvalCase(id="c", **kw)


# ------------------------------------------------------------------- label mapping

FIVE_TIER_METRICS = [
    "quality.answer_correctness",
    "quality.clarity",
    "quality.completeness",
    "quality.fluency",
    "quality.helpfulness",
    "quality.tone",
    "quality.coherence",
    "quality.instruction_following",
    "quality.summarization",
]


@pytest.mark.parametrize("metric_id", FIVE_TIER_METRICS)
@pytest.mark.parametrize(("label", "score"), list(FIVE_TIER.items()))
async def test_five_tier_label_mapping(metric_id: str, label: str, score: float) -> None:
    res, judge = await run(metric_id, case(input=Q, output=A, expected=A), reply(label))
    assert res.status == "scored" and res.score == score
    assert res.details["label"] == label and res.reason == f"because {label}"
    assert len(judge.requests) == 1


@pytest.mark.parametrize(("level", "score"), list(contradiction_prompts.LEVELS.items()))
async def test_contradiction_levels(level: str, score: float) -> None:
    res, _ = await run(
        "quality.contradiction",
        case(output=A),
        {"reasoning": "r", "level": level, "contradictions": ["a vs b"]},
    )
    assert res.score == score and res.details["label"] == level
    assert res.details["contradictions"] == ["a vs b"]
    assert res.threshold == 0.7 and res.passed is (score >= 0.7)


def test_default_thresholds() -> None:
    for metric_id in FIVE_TIER_METRICS + ["quality.conciseness"]:
        assert registry.resolve(metric_id)[0].spec.default_threshold == 0.5
    assert registry.resolve("quality.contradiction")[0].spec.default_threshold == 0.7


# ------------------------------------------------------------- input-mode selection


@pytest.mark.parametrize(
    ("kw", "mode", "marker"),
    [
        ({"output": A}, "simple", "Evaluate the clarity of this response."),
        ({"input": Q, "output": A}, "with_input", "# Question/Request:"),
        ({"output": A, "expected": A}, "with_expected", "# Expected Response (Reference):"),
        ({"input": Q, "output": A, "expected": A}, "with_input_and_expected", "# Question/Req"),
        ({"output": A, "conversation": CONV}, "conversation", "# Conversation History:"),
    ],
)
async def test_clarity_input_variants(kw: dict, mode: str, marker: str) -> None:
    res, judge = await run("quality.clarity", case(**kw), reply())
    assert res.details["mode"] == mode and judge.requests[0].metadata["mode"] == mode
    assert marker in judge.requests[0].user


async def test_input_mode_param_overrides_detection() -> None:
    c = case(input=Q, output=A, expected=A, conversation=CONV)
    res, judge = await run("quality.completeness", c, reply(), {"input_mode": "simple"})
    assert res.details["mode"] == "with_input"  # simple + input -> AgentSight with_input variant
    res, judge = await run("quality.tone", c, reply(), {"input_mode": "with_expected"})
    assert (
        res.details["mode"] == "with_expected" and "# Expected Response:" in judge.requests[0].user
    )
    res, _ = await run("quality.coherence", c, reply(), {"input_mode": "conversation"})
    assert res.status == "error" and "not supported" in res.error.message


async def test_conversation_stands_in_for_output() -> None:
    for metric_id, script in [
        ("quality.clarity", reply()),
        ("quality.contradiction", {"reasoning": "r", "level": "No Contradictions"}),
    ]:
        res, judge = await run(metric_id, case(conversation=CONV), script)
        assert res.status == "scored" and res.details["mode"] == "conversation"
        assert "Assistant: " + A in judge.requests[0].user
    # AgentSight GENERAL category: tone also accepts a conversation.
    res, _ = await run("quality.tone", case(conversation=CONV), reply())
    assert res.status == "scored" and res.details["mode"] == "conversation"
    # A case with neither output nor conversation is still skipped.
    res, _ = await run("quality.tone", case(), reply())
    assert res.status == "skipped" and res.error.code == "missing_input"


@pytest.mark.parametrize("metric_id", ["quality.fluency", "quality.contradiction"])
async def test_input_not_shown_where_source_excludes_it(metric_id: str) -> None:
    script = {"reasoning": "r", "score": "Good", "level": "No Contradictions"}
    _, judge = await run(metric_id, case(input="SECRET-QUESTION", output=A), script)
    assert "SECRET-QUESTION" not in judge.requests[0].user


async def test_tone_and_helpfulness_modes() -> None:
    _, judge = await run("quality.tone", case(input=Q, output=A), reply())
    assert judge.requests[0].user.startswith("# User Query:") and Q in judge.requests[0].user
    assert "Evaluate the appropriateness of the response tone." in judge.requests[0].user
    _, judge = await run("quality.helpfulness", case(output=A, conversation=CONV), reply())
    assert "most recent response" in judge.requests[0].user


async def test_domain_rubric_uses_metric_template() -> None:
    _, judge = await run(
        "quality.clarity", case(output=A), reply(), {"domain_rubric": "Use plain words."}
    )
    assert "# Domain-Specific Clarity Criteria" in judge.requests[0].system
    assert "Use plain words." in judge.requests[0].system


# ------------------------------------------------------------------- completeness


async def test_completeness_without_contexts_makes_one_call() -> None:
    res, judge = await run(
        "quality.completeness", case(input=Q, output=A), reply("Good", missing_elements=["fees"])
    )
    assert len(judge.requests) == 1 and judge.requests[0].metadata["tag"] == "question"
    assert res.score == 0.75 and res.details["question_completeness"] == 0.75
    assert res.details["has_context"] is False and "context_completeness" not in res.details
    assert res.details["missing_elements"] == ["fees"]


async def test_completeness_with_contexts_averages_two_calls() -> None:
    replies = {
        "question": reply("Excellent", missing_elements=["fees"]),
        "context": reply("Average", missing_elements=["lock period"]),
    }
    c = case(input=Q, output=A, contexts=(Context(text="Staking locks BNB for 7 days."),))
    res, judge = await run("quality.completeness", c, lambda req: replies[req.metadata["tag"]])
    assert [r.metadata["tag"] for r in judge.requests] == ["question", "context"]
    q_req, c_req = judge.requests
    assert completeness_prompts.CONTEXT_AWARE_ADDENDUM not in q_req.system
    assert completeness_prompts.CONTEXT_AWARE_ADDENDUM in c_req.system
    assert "# Retrieval Context (Available Information):" in c_req.user
    assert "Staking locks BNB for 7 days." in c_req.user and Q in c_req.user
    assert res.score == 0.75  # (1.0 + 0.5) / 2
    assert res.details["question_completeness"] == 1.0
    assert res.details["context_completeness"] == 0.5
    assert res.details["label"] == "Good" and res.details["has_context"] is True
    assert res.details["missing_elements"] == ["fees", "[context] lock period"]
    assert res.reason == (
        "Question completeness: because Excellent | Context completeness: because Average"
    )
    assert res.provenance.prompt_hash == completeness_prompts.V1.hash()


async def test_completeness_samples_per_call() -> None:
    c = case(input=Q, output=A, contexts=(Context(text="ctx"),))
    res, judge = await run("quality.completeness", c, reply("Good"), {"samples": 3})
    assert len(judge.requests) == 6 and res.provenance.samples == 3
    assert res.details["sample_scores"] == {"question": [0.75] * 3, "context": [0.75] * 3}


def test_completeness_prompt_hash_covers_context_template() -> None:
    assert (
        completeness_prompts.V1.extra["context_template"] == completeness_prompts.CONTEXT_V1.hash()
    )


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (1.0, "Excellent"),
        (0.875, "Good"),
        (0.625, "Average"),
        (0.375, "Below Average"),
        (0.125, "Poor"),
    ],
)
def test_completeness_averaged_label(score: float, label: str) -> None:
    assert score_to_label(score) == label


# -------------------------------------------------------------------- conciseness


def concise(verbosity: str, redundancy: str, repetition: str, overall: str = "Good") -> dict:
    return {
        "reasoning": "r",
        "verbosity_score": verbosity,
        "redundancy_score": redundancy,
        "repetition_score": repetition,
        "overall_score": overall,
        "verbosity_examples": ["basically"],
    }


async def test_conciseness_weighted_sub_scores() -> None:
    script = concise("Very Concise", "Some Redundancy", "High Repetition", "Average")
    res, judge = await run("quality.conciseness", case(output=A), script)
    assert len(judge.requests) == 1
    assert res.score == pytest.approx(0.4 * 1.0 + 0.3 * 0.5 + 0.3 * 0.0)
    assert res.details["verbosity_score"] == 1.0
    assert res.details["redundancy_score"] == 0.5
    assert res.details["repetition_score"] == 0.0
    assert res.details["overall_score"] == 0.5 and res.details["label"] == "Average"
    assert res.details["verbosity_examples"] == ["basically"]
    assert res.details["weights"] == {"verbosity": 0.4, "redundancy": 0.3, "repetition": 0.3}


async def test_conciseness_overall_rating_is_not_scored() -> None:
    script = concise("Concise", "Minimal Redundancy", "Minimal Repetition", "Poor")
    res, _ = await run("quality.conciseness", case(output=A), script)
    assert res.score == 0.75 and res.details["label"] == "Poor"


async def test_conciseness_custom_weights_are_normalized() -> None:
    script = concise("Very Verbose", "No Redundancy", "No Repetition")
    params = {"verbosity_weight": 2, "redundancy_weight": 1, "repetition_weight": 1}
    res, _ = await run("quality.conciseness", case(output=A), script, params)
    assert res.score == 0.5 and res.details["weights"]["verbosity"] == 0.5


def test_conciseness_sub_label_scales() -> None:
    for scale in conciseness_prompts.SUB_LABELS.values():
        assert list(scale.values()) == [0.0, 0.25, 0.5, 0.75, 1.0]


async def test_conciseness_with_expected_excludes_input() -> None:
    script = concise("Concise", "No Redundancy", "No Repetition")
    res, judge = await run(
        "quality.conciseness", case(input="SECRET-QUESTION", output=A, expected="short"), script
    )
    assert res.details["mode"] == "with_expected"
    assert "# Expected Output (Reference):" in judge.requests[0].user
    assert "SECRET-QUESTION" not in judge.requests[0].user


# ------------------------------------------------------------- answer_correctness


async def test_answer_correctness_native_prompts() -> None:
    res, judge = await run(
        "quality.answer_correctness", case(input=Q, output=A, expected=A), reply("Excellent")
    )
    assert res.details["mode"] == "with_input_and_expected" and res.score == 1.0
    assert judge.requests[0].user.startswith("# Question/Input:")
    res, judge = await run("quality.answer_correctness", case(output=A, expected=A), reply())
    assert res.details["mode"] == "with_expected"
    assert judge.requests[0].user.startswith("# Expected Answer:")
    res, judge = await run(
        "quality.answer_correctness", case(output=A, expected=A, conversation=CONV), reply()
    )
    assert res.details["mode"] == "conversation"
    assert "# Expected Summary/Answer:" in judge.requests[0].user


@pytest.mark.parametrize(
    ("params", "output", "score", "passed"),
    [
        ({}, "  PARIS ", 1.0, True),  # default strategy exact, case-insensitive, stripped
        ({"case_insensitive": False}, "PARIS", 0.0, False),
        ({"strategy": "exact"}, "Paris, France", 0.0, False),
    ],
)
async def test_answer_correctness_rule_exact(params, output, score, passed) -> None:
    res, judge = await run(
        "quality.answer_correctness", case(output=output, expected="Paris"), None, params, "rule"
    )
    assert res.score == score and res.passed is passed and res.threshold == 1.0
    assert res.details["strategy"] == "exact" and judge.requests == []


async def test_answer_correctness_rule_strategy_thresholds() -> None:
    c = case(output="the cat sat", expected="The cat sat on the mat")
    rouge, _ = await run("quality.answer_correctness", c, None, {"strategy": "rouge"}, "rule")
    assert rouge.score == pytest.approx(2 / 3)  # LCS 3: P = 1, R = 0.5
    assert rouge.threshold == 0.7 and rouge.passed is False
    jac, _ = await run("quality.answer_correctness", c, None, {"strategy": "jaccard"}, "rule")
    assert jac.score == pytest.approx(3 / 5)
    assert jac.threshold == 0.5 and jac.passed is True
    # An explicit suite threshold wins over the strategy default.
    res, _ = await run(
        "quality.answer_correctness", c, None, {"strategy": "rouge"}, "rule", threshold=0.6
    )
    assert res.threshold == 0.6 and res.passed is True


async def test_answer_correctness_native_threshold_unchanged() -> None:
    res, _ = await run(
        "quality.answer_correctness",
        case(output=A, expected=A),
        reply("Average"),
        {"strategy": "rouge"},
    )
    assert res.method == "native" and res.threshold == 0.5 and res.passed is True


# -------------------------------------------------------------------- helpfulness


async def test_helpfulness_params() -> None:
    _, judge = await run("quality.helpfulness", case(input=Q, output=A), reply())
    assert Q in judge.requests[0].user
    res, judge = await run(
        "quality.helpfulness",
        case(input=Q, output=A),
        reply(),
        {"include_inputs": False, "agent_id": "agent-7"},
    )
    assert Q not in judge.requests[0].user and res.details["agent_id"] == "agent-7"
    spec = registry.resolve("quality.helpfulness")[0].spec
    assert spec.validate_params({"include_inputs": "yes"})
    assert not spec.validate_params({"include_inputs": True, "agent_id": "a"})


def test_tone_has_no_expected_tone_param() -> None:
    assert registry.resolve("quality.tone")[0].spec.validate_params({"expected_tone": "warm"})


# ---------------------------------------------------------- instruction_following


async def test_instruction_following_from_input() -> None:
    res, judge = await run(
        "quality.instruction_following", case(input="Answer in one word.", output="Yes"), reply()
    )
    assert res.details["mode"] == "simple"
    assert "# User Input (contains the instructions):" in judge.requests[0].user


async def test_instruction_following_with_instructions_param() -> None:
    params = {"instructions": ["Answer in English", "Use at most 20 words"]}
    res, judge = await run(
        "quality.instruction_following",
        case(input=Q, output=A),
        reply("Excellent", instructions_followed=["Answer in English"]),
        params,
    )
    user = judge.requests[0].user
    assert res.details["mode"] == "with_input_and_instructions" and res.score == 1.0
    assert "1. Answer in English" in user and "2. Use at most 20 words" in user and Q in user
    assert res.details["instructions_followed"] == ["Answer in English"]
    res, judge = await run(
        "quality.instruction_following", case(output=A), reply(), {"instructions": "Be brief."}
    )
    assert res.status == "scored" and res.details["mode"] == "with_instructions"
    assert "1. Be brief." in judge.requests[0].user


async def test_instruction_following_needs_input_or_instructions() -> None:
    res, judge = await run("quality.instruction_following", case(output=A), reply())
    assert res.status == "skipped" and res.error.code == "missing_input"
    assert judge.requests == []
    spec = registry.resolve("quality.instruction_following")[0].spec
    assert not spec.validate_params({"instructions": "x"})
    assert spec.validate_params({"instructions": 3})


# -------------------------------------------------------------------- summarization


async def test_summarization_prompt_and_inputs() -> None:
    source = "BNB Chain upgraded its validator set in 2026."
    res, judge = await run(
        "quality.summarization",
        case(input=source, output="Validators were upgraded."),
        reply("Good", missing_key_points=["year"]),
    )
    assert res.score == 0.75 and res.details["missing_key_points"] == ["year"]
    assert judge.requests[0].user.startswith("# Source Text:") and source in judge.requests[0].user
    res, judge = await run(
        "quality.summarization", case(input=source, output="x", expected="ref"), reply()
    )
    assert (
        res.details["mode"] == "with_expected" and "# Reference Summary:" in judge.requests[0].user
    )
    res, _ = await run("quality.summarization", case(output="x"), reply())
    assert res.status == "skipped" and res.error.code == "missing_input"


async def test_coherence_with_input() -> None:
    res, judge = await run("quality.coherence", case(input=Q, output=A), reply("Average"))
    assert res.details["mode"] == "with_input" and res.score == 0.5
    assert "Evaluate the coherence of the response" in judge.requests[0].user


@pytest.mark.parametrize(
    "metric_id", ["quality.coherence", "quality.instruction_following", "quality.summarization"]
)
def test_new_prompts_record_provenance(metric_id: str) -> None:
    cls = registry.resolve(metric_id)[0]
    assert cls.templates["v1"].source.startswith("bnbagent-eval:new (cf. ")
