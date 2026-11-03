"""Golden tests for format.structure, custom.* judge metrics and ops.consistency:judge."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bnbagent_eval.exceptions import SuiteError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import Context, EvalCase, Message, MetricConfig, MetricResult


async def judged(
    metric_id: str,
    case: EvalCase,
    script: Any,
    params: dict | None = None,
    method: str | None = None,
    runs: tuple[EvalCase, ...] = (),
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=cls.spec.method(method),
        params=params,
        judge=judge,
        runs=runs,
    )
    return await cls().evaluate(case, ctx), judge


def qa(**kw: Any) -> EvalCase:
    return EvalCase(id="q", **{"input": "What is BNB?", "output": "A token.", **kw})


def prompt_hash(metric_id: str, params: dict, method: str | None = None) -> str | None:
    cls, _ = registry.resolve(metric_id)
    return cls().prompt_hash(cls.spec.method(method), params)


# --------------------------------------------------------------------------- format.structure


async def test_structure_label_mapping_spec_in_prompt_and_hash() -> None:
    params = {"expected_structure": 'A JSON object like {"title": str}.'}
    reply = {"reasoning": "r", "score": "Good", "missing_elements": ["date"]}
    res, judge = await judged("format.structure", qa(), reply, params)
    assert res.score == 0.75 and res.details["missing_elements"] == ["date"]
    assert res.details["validation_type"] == "description" and res.details["label"] == "Good"
    user = judge.requests[0].user
    assert '# Structure Specification:\nA JSON object like {"title": str}.' in user
    assert "# Expected Structure Type: DESCRIPTION" in user
    assert prompt_hash("format.structure", params) != prompt_hash(
        "format.structure", {"expected_structure": "XML"}
    )


async def test_structure_conversation_mode_uses_assistant_turns() -> None:
    conv = (
        Message(role="user", content="hi"),
        Message(role="assistant", content="part one"),
        Message(role="user", content="more"),
        Message(role="assistant", content="part two"),
    )
    res, judge = await judged(
        "format.structure",
        qa(conversation=conv),
        {"reasoning": "r", "score": "Excellent"},
        {"expected_structure": "two parts"},
    )
    assert res.score == 1.0 and res.details["mode"] == "conversation"
    assert "part one part two" in judge.requests[0].user


# --------------------------------------------------------------------------- judge_prompt


async def test_judge_prompt_placeholders_and_aliases() -> None:
    params = {
        "prompt": "Q={input} A={output} A2={actual_output} E={expected} E2={expected_output} "
        'C={contexts} literal {"json": 1}',
    }
    case = qa(expected="ref", contexts=(Context(text="ctx one"), Context(text="ctx two")))
    res, judge = await judged(
        "custom.judge_prompt", case, {"reasoning": "ok", "score": "Average"}, params
    )
    assert res.score == 0.5 and res.reason == "ok" and res.details["label"] == "Average"
    user = judge.requests[0].user
    for text in ("What is BNB?", "A token.", "ref", "ctx one\nctx two", 'literal {"json": 1}'):
        assert text in user
    assert user.count("A token.") == 2 and user.count("\nref\n") == 2
    assert "SAFETY RULES" in judge.requests[0].system


async def test_judge_prompt_auto_appends_content_without_placeholders() -> None:
    res, judge = await judged(
        "custom.judge_prompt",
        qa(),
        {"reasoning": "r", "score": "Good"},
        {"prompt": "Is the answer polite?"},
    )
    user = judge.requests[0].user
    assert user.startswith("Is the answer polite?\n\n# Content to Evaluate:\n")
    assert "## Input:\nWhat is BNB?" in user and "## Response:\nA token." in user
    assert "## Expected:" not in user and res.score == 0.75


def test_judge_prompt_unknown_placeholder_is_a_clear_error() -> None:
    with pytest.raises(SuiteError, match=r"unknown placeholder\(s\) \{answer\}"):
        prompt_hash("custom.judge_prompt", {"prompt": "Rate {answer}"})
    with pytest.raises(SuiteError, match="requires the {contexts} placeholder"):
        prompt_hash("custom.judge_prompt", {"prompt": "Rate {output}", "category": "rag"})
    with pytest.raises(SuiteError, match="requires the {expected_output} or {expected}"):
        prompt_hash("custom.judge_prompt", {"prompt": "{output}", "category": "comparison"})


async def test_judge_prompt_unknown_placeholder_errors_at_evaluation() -> None:
    res, _ = await judged("custom.judge_prompt", qa(), {"score": "Good"}, {"prompt": "{nope}"})
    assert res.status == "error" and "unknown placeholder" in res.error.message


def test_judge_prompt_hash_covers_prompt_and_scale() -> None:
    a = prompt_hash("custom.judge_prompt", {"prompt": "Rate {output}"})
    b = prompt_hash("custom.judge_prompt", {"prompt": "Grade {output}"})
    c = prompt_hash("custom.judge_prompt", {"prompt": "Rate {output}", "scale": "numeric"})
    assert len({a, b, c}) == 3


@pytest.mark.parametrize(
    ("reply", "score", "label"),
    [
        ({"label": "pass", "explanation": "e"}, 1.0, "pass"),
        ({"label": "fail", "explanation": "e"}, 0.0, "fail"),
        ({"label": "not_applicable", "explanation": "e"}, None, "not_applicable"),
    ],
)
async def test_judge_prompt_pass_fail_na_scale(reply: dict, score: Any, label: str) -> None:
    params = {"prompt": "Check {output}", "scale": "pass_fail_na"}
    res, judge = await judged("custom.judge_prompt", qa(), json.dumps(reply), params)
    assert res.score == score and res.details["label"] == label and res.reason == "e"
    assert res.status == "scored" and (res.passed is None) == (score is None)
    assert judge.requests[0].output_schema["required"] == ["label", "explanation"]


async def test_judge_prompt_pass_fail_na_samples_skip_na() -> None:
    script = [
        json.dumps({"label": "not_applicable", "explanation": "na"}),
        json.dumps({"label": "pass", "explanation": "p"}),
        json.dumps({"label": "not_applicable", "explanation": "na"}),
    ]
    params = {"prompt": "Check {output}", "scale": "pass_fail_na", "samples": 3}
    res, _ = await judged("custom.judge_prompt", qa(), script, params)
    assert res.score == 1.0 and res.details["sample_scores"] == [None, 1.0, None]


async def test_judge_prompt_numeric_scale_is_etk() -> None:
    params = {"prompt": "Score clarity of {output}", "scale": "numeric"}
    res, judge = await judged("custom.judge_prompt", qa(), {"score": 0.3, "context": "c"}, params)
    assert res.score == 0.3 and res.reason == "c"
    assert judge.requests[0].system.startswith("<instructions>\nYou are an expert analyst")


async def test_judge_prompt_category_checks_inputs() -> None:
    params = {"prompt": "Grounded? {output} {contexts}", "category": "rag"}
    res, judge = await judged("custom.judge_prompt", qa(), {"score": "Good"}, params)
    assert res.status == "skipped" and res.error.code == "missing_input"
    assert judge.requests == []


# --------------------------------------------------------------------------- rubric

RUBRICS = [
    {"id": "a", "text": "Mentions BNB."},
    {"id": "b", "text": "Is concise.", "weight": 3},
    {"id": "c", "text": "Cites a source."},
]


def rubric_reply(**verdicts: str) -> str:
    return json.dumps(
        {
            "evaluations": [
                {"rubric_id": rid, "property": "p", "rationale": f"why {rid}", "verdict": v}
                for rid, v in verdicts.items()
            ]
        }
    )


async def test_rubric_weights_and_na_exclusion() -> None:
    reply = rubric_reply(a="yes", b="no", c="not_applicable")
    res, judge = await judged("custom.rubric", qa(), reply, {"rubrics": RUBRICS})
    assert res.score == pytest.approx(1 / 4)  # a (w1) yes, b (w3) no, c excluded
    rows = {r["id"]: r for r in res.details["rubrics"]}
    assert rows["c"]["verdict"] == "not_applicable" and res.details["applicable"] == 2
    assert "[a] PASS: why a" in res.reason and "[b] FAIL: why b" in res.reason
    assert "*  [b] Is concise." in judge.requests[0].user


async def test_rubric_unweighted_is_agentsight_mean_and_property_matching() -> None:
    rubrics = [{"id": "x", "text": "Mentions BNB."}, {"id": "y", "text": "Is concise."}]
    reply = json.dumps(
        {
            "evaluations": [
                {"property": "mentions bnb.", "rationale": "r", "verdict": "yes"},
                {"rubric_id": "y", "property": "?", "rationale": "r", "verdict": "no"},
            ]
        }
    )
    res, _ = await judged("custom.rubric", qa(), reply, {"rubrics": rubrics})
    assert res.score == 0.5


async def test_rubric_majority_vote_with_three_samples() -> None:
    script = [
        rubric_reply(a="yes", b="no", c="yes"),
        rubric_reply(a="no", b="no", c="yes"),
        rubric_reply(a="yes", b="yes", c="not_applicable"),
    ]
    params = {"rubrics": RUBRICS, "samples": 3}
    res, judge = await judged("custom.rubric", qa(), script, params)
    assert len(judge.requests) == 3
    rows = {r["id"]: r for r in res.details["rubrics"]}
    assert rows["a"]["verdict"] == "yes" and rows["a"]["votes"] == {
        "yes": 2,
        "no": 1,
        "not_applicable": 0,
    }
    assert rows["b"]["verdict"] == "no"
    assert rows["c"]["verdict"] == "yes"  # N/A votes do not count against yes
    assert res.score == pytest.approx(2 / 5)  # a (1) + c (1) of a+b+c weights 1+3+1


async def test_rubric_tie_is_no_and_all_na_is_none() -> None:
    script = [rubric_reply(a="yes"), rubric_reply(a="no")]
    rubrics = [{"id": "a", "text": "t"}]
    res, _ = await judged("custom.rubric", qa(), script, {"rubrics": rubrics, "samples": 2})
    assert res.score == 0.0
    res, _ = await judged(
        "custom.rubric", qa(), rubric_reply(a="not_applicable"), {"rubrics": rubrics}
    )
    assert res.score is None and res.passed is None and res.reason == "no rubric is applicable"


def test_rubric_prompt_hash_covers_rubrics() -> None:
    a = prompt_hash("custom.rubric", {"rubrics": RUBRICS})
    b = prompt_hash("custom.rubric", {"rubrics": RUBRICS[:2]})
    assert a and b and a != b


# --------------------------------------------------------------------------- requirements


def req_reply(*rows: tuple[str, float, float | None]) -> str:
    reqs = []
    for rid, score, weight in rows:
        r = {"id": rid, "description": f"d {rid}", "score": score, "rationale": "r"}
        if weight is not None:
            r["weight"] = weight
        reqs.append(r)
    return json.dumps({"requirements": reqs, "summary": "sum", "issues": ["issue 1"]})


async def test_requirements_weighted_mean_from_judge_weights() -> None:
    reply = req_reply(("REQ-1", 1.0, 3), ("REQ-2", 0.5, None), ("REQ-3", 0.0, 0))
    res, judge = await judged("custom.requirements_compliance", qa(), reply)
    assert res.score == round((3 * 1.0 + 1 * 0.5) / 4, 3) == 0.875
    assert res.reason == "sum" and res.details["issues"] == ["issue 1"]
    assert res.details["requirements_source"] == "extracted"
    assert "extract the requirements from the task input" in judge.requests[0].user


async def test_requirements_from_params_with_weights_and_missing_items() -> None:
    params = {
        "requirements": ["Answer in one sentence.", {"text": "Mention BNB Chain.", "weight": 2}]
    }
    reply = req_reply(("REQ-1", 0.5, 10))  # REQ-2 not assessed -> 0
    res, judge = await judged("custom.requirements_compliance", qa(), reply, params)
    rows = res.details["requirements"]
    assert [r["weight"] for r in rows] == [10.0, 2.0]  # judge weight, then param weight
    assert rows[1]["assessed"] is False and rows[1]["score"] == 0.0
    assert res.score == round(10 * 0.5 / 12, 3)
    user = judge.requests[0].user
    assert "REQ-1: Answer in one sentence.\nREQ-2: Mention BNB Chain." in user


async def test_requirements_zero_total_weight_is_an_error_and_needs_input() -> None:
    res, _ = await judged("custom.requirements_compliance", qa(), req_reply(("REQ-1", 1.0, 0)))
    assert res.status == "error" and "sum to zero" in res.error.message
    res, _ = await judged(
        "custom.requirements_compliance", EvalCase(id="x", output="o"), req_reply(("R", 1, 1))
    )
    assert res.status == "skipped" and res.error.code == "missing_input"


# --------------------------------------------------------------------------- consistency


def runs(*outputs: str) -> tuple[EvalCase, ...]:
    return tuple(EvalCase(id="r", input="q", output=o) for o in outputs)


async def test_consistency_judge_scores_every_pair() -> None:
    def script(request):
        a = request.user.split("# Response A:")[1].split("# Response B:")[0]
        b = request.user.split("# Response B:")[1]
        same = ("alpha" in a) == ("alpha" in b)
        return {"reasoning": "r", "score": "yes" if same else "no"}

    res, judge = await judged(
        "ops.consistency",
        EvalCase(id="r", input="q", output="alpha 1"),
        script,
        method="judge",
        runs=runs("alpha 1", "alpha one", "beta"),
    )
    assert len(judge.requests) == 3 and res.details["pairs"] == 3
    assert res.score == pytest.approx(1 / 3) and res.details["min_pair"] == 0.0
    assert [(p["a"], p["b"], p["score"]) for p in res.details["pair_verdicts"]] == [
        (0, 1, 1.0),
        (0, 2, 0.0),
        (1, 2, 0.0),
    ]
    assert res.provenance.prompt_hash and res.provenance.samples == 1
    # Each pair has its own nonce (tagged calls).
    nonces = {r.system.split("<untrusted-")[1].split(">")[0] for r in judge.requests}
    assert len(nonces) == 3


async def test_consistency_rule_methods_unchanged_and_judge_params_accepted() -> None:
    res, judge = await judged(
        "ops.consistency", EvalCase(id="r", output="a"), None, method="exact", runs=runs("a", "a")
    )
    assert res.score == 1.0 and judge.requests == []
    cls, _ = registry.resolve("ops.consistency")
    assert cls.spec.validate_params({"samples": 3}) == []
    assert prompt_hash("ops.consistency", {}, "exact") is None
    assert prompt_hash("ops.consistency", {}, "judge")
