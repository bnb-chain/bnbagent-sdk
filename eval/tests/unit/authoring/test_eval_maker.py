"""authoring.eval_maker: a scripted-judge loop produces a valid, lint-clean suite."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.authoring.eval_maker import (
    EvalMakerError,
    InputQualityError,
    make_suite,
)
from bnbagent_eval.authoring.lint import lint_suite
from bnbagent_eval.exceptions import JudgeParseError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import EvalCase, Suite

ASSESS = "authoring.eval_maker_assess"
STRATEGY = "authoring.eval_maker_strategy"
MATCH = "authoring.eval_maker_match"
GENERATE = "authoring.eval_maker_generate"
REFLECT = "authoring.eval_maker_reflect"

DESCRIPTION = (
    "A support agent answers billing questions.\n"
    "- The reply must be valid JSON.\n"
    "- The reply must cite the invoice number and the refund policy section."
)

ASSESSMENT = {
    "domain": "billing support",
    "risks": ["wrong refund promises"],
    "qualities": ["valid JSON", "cites invoice and policy"],
    "clarity_score": 0.9,
    "completeness_score": 0.8,
    "correlation_score": 0.7,
    "warnings": [],
}
STRATEGY_OUT = {
    "dimensions": [
        {
            "name": "Valid JSON",
            "description": "The reply parses as JSON",
            "approach": "catalogue",
            "candidate_metric": "format.json_valid",
        },
        {
            "name": "citations",
            "description": "Cites the invoice number and refund policy section",
            "approach": "custom",
        },
    ],
    "rationale": "one dimension per requirement",
}
MATCH_OUT = {
    "matches": [
        {
            "dimension": "valid_json",
            "metric_id": "format.json_valid",
            "required": True,
            "confidence": 0.95,
            "reasoning": "deterministic JSON check",
        },
        {"dimension": "citations", "metric_id": None, "confidence": 0.0},
    ]
}
RUBRIC_METRIC = {
    "name": "Citations",
    "metric_id": "custom.rubric",
    "dimensions": ["citations"],
    "params": {
        "rubrics": [
            {"id": "R1", "text": "The reply states the invoice number from the question"},
            {"id": "R2", "text": "The reply names the refund policy section it relies on"},
        ]
    },
    "threshold": 0.8,
    "required": True,
    "rationale": "no catalogue metric checks citations",
}
GENERATE_OUT = {"metrics": [RUBRIC_METRIC]}
SATISFIED = {"criteria_quality_score": 0.9, "coverage_score": 1.0, "should_regenerate": False}
UNSATISFIED = {
    "criteria_quality_score": 0.4,
    "coverage_score": 0.5,
    "issues_found": ["weak"],
    "improvements_suggested": ["tighten the rubric"],
    "should_regenerate": True,
}


def script(**overrides: Any) -> dict[str, Any]:
    base = {
        ASSESS: ASSESSMENT,
        STRATEGY: STRATEGY_OUT,
        MATCH: MATCH_OUT,
        GENERATE: GENERATE_OUT,
        REFLECT: SATISFIED,
    }
    base.update(overrides)
    return base


def calls(judge: FakeJudge, metric_id: str) -> list:
    return [r for r in judge.requests if r.metadata["metric_id"] == metric_id]


async def test_happy_path_produces_lint_clean_suite() -> None:
    judge = FakeJudge(script())
    result = await make_suite(DESCRIPTION, judge=judge, stage="pre_submit")
    suite = result.suite
    assert [m.key for m in suite.metrics] == ["format.json_valid", "citations"]
    assert not [i for i in result.lint if i.level != "info"]
    assert lint_suite(suite, use="negotiated") == list(result.lint)
    # the judge is pinned for disputes; the suite is locked and reloads unchanged
    assert suite.judge is not None and suite.judge.temperature == 0.0
    assert suite.judge.samples >= 3 and suite.pass_rule.inconclusive_band > 0
    assert suite.lock is not None and set(suite.lock) == {"format.json_valid", "custom.rubric"}
    assert Suite.load(suite.to_dict()) == suite
    assert suite.name == "billing-support-suite"
    assert [s.stage for s in result.steps] == [
        "assess",
        "strategy",
        "match",
        "generate",
        "reflect",
        "validate",
        "build",
    ]
    assert "citations" in result.rationale["citations"]
    # user text is fenced and the system prompt carries the fence clause
    first = judge.requests[0]
    assert "<untrusted-" in first.user and "Untrusted Content" in first.system
    assert result.to_dict()["suite"]["name"] == suite.name


async def test_dev_stage_has_no_negotiation_extras() -> None:
    judge = FakeJudge(script())
    result = await make_suite(DESCRIPTION, judge=judge, name="mine")
    assert result.suite.name == "mine"
    assert result.suite.pass_rule.inconclusive_band == 0.0
    assert result.suite.judge is not None and result.suite.judge.samples == 1


async def test_hallucinated_ids_are_dropped_or_repaired() -> None:
    match = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valud", "confidence": 0.9},
            {"dimension": "citations", "metric_id": "quality.citation_magic", "confidence": 0.9},
            {"dimension": "valid_json", "metric_id": "deepeval.json_correctness"},
        ]
    }
    judge = FakeJudge(script(**{MATCH: match}))
    result = await make_suite(DESCRIPTION, judge=judge)
    ids = [m.id for m in result.suite.metrics]
    assert ids == ["format.json_valid", "custom.rubric"]  # repaired; hallucination -> custom
    notes = " ".join(n for s in result.steps if s.stage == "match" for n in s.notes)
    assert "repaired to 'format.json_valid'" in notes
    assert "'quality.citation_magic' dropped" in notes
    # the dropped dimension was handed to the custom generator
    [gen] = calls(judge, GENERATE)
    assert "citations" in gen.user


async def test_alias_and_invalid_params_are_validated() -> None:
    match = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valid", "required": True},
            {
                "dimension": "valid_json",
                "metric_id": "format.regex",
                "params": {"patern": "x"},
                "confidence": 0.9,
            },
        ]
    }
    judge = FakeJudge(script(**{MATCH: match}))
    result = await make_suite(DESCRIPTION, judge=judge)
    assert "format.regex" not in [m.id for m in result.suite.metrics]
    notes = " ".join(n for s in result.steps for n in s.notes)
    assert "format.regex for 'valid_json' dropped" in notes


async def test_low_confidence_match_is_dropped() -> None:
    match = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valid", "confidence": 0.5}
        ]
    }
    judge = FakeJudge(script(**{MATCH: match}))
    result = await make_suite(DESCRIPTION, judge=judge)
    assert [m.id for m in result.suite.metrics] == ["custom.rubric"]
    [gen] = calls(judge, GENERATE)
    assert "valid_json" in gen.user


async def test_reflection_loop_is_capped() -> None:
    judge = FakeJudge(script(**{REFLECT: UNSATISFIED}))
    result = await make_suite(DESCRIPTION, judge=judge)
    assert result.reflections == 2
    assert len(calls(judge, REFLECT)) == 2
    assert len(calls(judge, MATCH)) == 3 and len(calls(judge, GENERATE)) == 3
    # the critique's improvements are fed back to the next round
    assert "tighten the rubric" in calls(judge, GENERATE)[1].user


async def test_custom_cap_and_zero_reflections() -> None:
    judge = FakeJudge(script(**{REFLECT: UNSATISFIED}))
    result = await make_suite(DESCRIPTION, judge=judge, max_reflections=1)
    assert result.reflections == 1
    judge = FakeJudge(script(**{REFLECT: UNSATISFIED}))
    result = await make_suite(DESCRIPTION, judge=judge, max_reflections=0)
    assert result.reflections == 0 and not calls(judge, REFLECT)


async def test_lint_findings_drive_reflection() -> None:
    vague = {
        "metrics": [
            {
                **RUBRIC_METRIC,
                "params": {"rubrics": [{"id": "R1", "text": "The answer is good"}]},
            }
        ]
    }
    # The judge is "satisfied", but lint is not: the vague rubric forces a regeneration.
    judge = FakeJudge(script(**{GENERATE: [vague, GENERATE_OUT]}))
    result = await make_suite(DESCRIPTION, judge=judge, stage="pre_submit")
    first_reflect = calls(judge, REFLECT)[0]
    second_generate = calls(judge, GENERATE)[1]
    assert "ambiguous_criterion" in first_reflect.user
    assert "ambiguous_criterion" in second_generate.user  # the finding reaches the generator
    reflect_steps = [s for s in result.steps if s.stage == "reflect"]
    assert reflect_steps[0].data["lint"][0]["code"] == "ambiguous_criterion"
    assert not [i for i in result.lint if i.code == "ambiguous_criterion"]


async def test_unfixable_lint_errors_raise() -> None:
    match = {
        "matches": [{"dimension": "valid_json", "metric_id": "ops.latency", "required": True}]
    }
    judge = FakeJudge(script(**{MATCH: match, GENERATE: {"metrics": []}}))
    with pytest.raises(EvalMakerError) as info:
        await make_suite(DESCRIPTION, judge=judge, stage="pre_submit")
    assert any(i.code == "required_without_threshold" for i in info.value.issues)
    assert len(calls(judge, REFLECT)) == 2


async def test_empty_draft_raises() -> None:
    judge = FakeJudge(script(**{MATCH: {"matches": []}, GENERATE: {"metrics": []}}))
    with pytest.raises(EvalMakerError, match="empty_suite"):
        await make_suite(DESCRIPTION, judge=judge, max_reflections=0)


async def test_invalid_json_is_repaired() -> None:
    judge = FakeJudge(script(**{ASSESS: ["not json at all", ASSESSMENT]}))
    result = await make_suite(DESCRIPTION, judge=judge)
    assessed = calls(judge, ASSESS)
    assert len(assessed) == 2 and "could not be parsed" in assessed[1].user
    assert result.steps[0].data["domain"] == "billing support"


async def test_schema_violation_is_repaired_once_then_raises() -> None:
    bad = {"dimensions": [{"name": "x"}]}  # missing description / approach
    judge = FakeJudge(script(**{STRATEGY: [bad, STRATEGY_OUT]}))
    await make_suite(DESCRIPTION, judge=judge)
    assert len(calls(judge, STRATEGY)) == 2
    judge = FakeJudge(script(**{STRATEGY: bad}))
    with pytest.raises(JudgeParseError):
        await make_suite(DESCRIPTION, judge=judge)


async def test_custom_metric_generation() -> None:
    strategy = {
        "dimensions": [
            {"name": "citations", "description": "cites sources", "approach": "custom"},
            {"name": "policy", "description": "follows refund policy", "approach": "custom"},
            {"name": "tone", "description": "calm wording", "approach": "custom"},
            {"name": "bad", "description": "broken prompt", "approach": "custom"},
        ]
    }
    generate = {
        "metrics": [
            RUBRIC_METRIC,
            {
                "name": "policy",
                "metric_id": "custom.requirements_compliance",
                "params": {
                    "requirements": [
                        {"id": "Q1", "text": "Refunds over 30 days are declined with the reason"}
                    ]
                },
            },
            {
                "name": "tone",
                "metric_id": "custom.judge_prompt",
                "params": {
                    "prompt": "Rate how calm the wording of {output} is.",
                    "scale": "numeric",
                },
            },
            {
                "name": "bad",
                "metric_id": "custom.judge_prompt",
                "params": {"prompt": "Judge {answer} please"},
            },
        ]
    }
    match = {"matches": [{"dimension": "x", "metric_id": "format.json_valid"}]}
    judge = FakeJudge(script(**{STRATEGY: strategy, MATCH: match, GENERATE: generate}))
    result = await make_suite(DESCRIPTION, judge=judge)
    by_key = {m.key: m for m in result.suite.metrics}
    assert set(by_key) == {"format.json_valid", "citations", "policy", "tone"}
    assert by_key["policy"].id == "custom.requirements_compliance"
    assert by_key["tone"].params["scale"] == "numeric"
    assert by_key["citations"].threshold == 0.8 and by_key["citations"].required
    notes = " ".join(n for s in result.steps if s.stage == "generate" for n in s.notes)
    assert "custom metric 'bad' dropped" in notes and "{answer}" in notes


async def test_pre_submit_prefers_rule_methods() -> None:
    match = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valid", "required": True},
            {"dimension": "citations", "metric_id": "quality.answer_correctness"},
        ]
    }
    strict_judge = FakeJudge(script(**{MATCH: match}))
    strict = await make_suite(DESCRIPTION, judge=strict_judge, stage="pre_submit")
    dev_judge = FakeJudge(script(**{MATCH: match}))
    dev = await make_suite(DESCRIPTION, judge=dev_judge, stage="dev")
    method = {m.id: m.method for m in strict.suite.metrics}
    assert method["quality.answer_correctness"] == "rule"
    assert {m.id: m.method for m in dev.suite.metrics}["quality.answer_correctness"] is None
    # the stage guidance reaches the prompts
    strict_prompt = calls(strict_judge, MATCH)[0].user
    assert "Prefer deterministic (rule) metrics" in strict_prompt and "pre_submit" in strict_prompt
    assert "Prefer deterministic" not in calls(dev_judge, MATCH)[0].user


async def test_pre_submit_lint_asks_for_a_deterministic_metric() -> None:
    judge_only = {
        "matches": [{"dimension": "valid_json", "metric_id": "quality.clarity", "required": True}]
    }
    with_rule = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valid", "required": True}
        ]
    }
    judge = FakeJudge(script(**{MATCH: [judge_only, with_rule]}))
    result = await make_suite(DESCRIPTION, judge=judge, stage="pre_submit")
    assert "no_deterministic_metric" in calls(judge, MATCH)[1].user
    assert "format.json_valid" in [m.id for m in result.suite.metrics]
    # in dev, the same judge-only draft is lint-clean, so there is no second round
    judge = FakeJudge(script(**{MATCH: [judge_only, with_rule]}))
    dev = await make_suite(DESCRIPTION, judge=judge, stage="dev")
    assert len(calls(judge, MATCH)) == 1 and dev.reflections == 1


async def test_sample_cases_inform_inputs_and_compatibility() -> None:
    samples = [
        EvalCase(id="1", input="Where is my refund?", output='{"answer": "soon"}'),
        {"id": "2", "input": "Invoice 42?", "output": "{}"},
    ]
    match = {
        "matches": [
            {"dimension": "valid_json", "metric_id": "format.json_valid", "required": True},
            {"dimension": "citations", "metric_id": "rag.faithfulness"},
        ]
    }
    judge = FakeJudge(script(**{MATCH: match}))
    result = await make_suite(DESCRIPTION, judge=judge, sample_cases=samples)
    m = calls(judge, MATCH)[0]
    assert "input, output" in m.user
    assert "Where is my refund?" in calls(judge, ASSESS)[0].user
    reflect = calls(judge, REFLECT)[0]
    assert "rag.faithfulness needs inputs ['contexts']" in reflect.user
    assert result.suite.metrics


async def test_low_quality_input_is_rejected() -> None:
    poor = {**ASSESSMENT, "clarity_score": 0.1, "clarity_issues": ["no criteria stated"]}
    judge = FakeJudge(script(**{ASSESS: poor}))
    with pytest.raises(InputQualityError, match="no criteria stated") as info:
        await make_suite("make it good", judge=judge)
    assert info.value.assessment["clarity_score"] == 0.1
    assert len(judge.requests) == 1


async def test_bad_arguments() -> None:
    with pytest.raises(EvalMakerError):
        await make_suite("  ", judge=FakeJudge(script()))
    with pytest.raises(ValueError):
        await make_suite(DESCRIPTION, judge=FakeJudge(script()), max_reflections=-1)
