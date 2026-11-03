"""analysis.diagnosis: two-stage judge diagnosis, confidence floor, rule fallback, aggregator."""

from __future__ import annotations

import dataclasses

import pytest

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.analysis.diagnosis import (
    CaseDiagnosis,
    DiagnosisAggregator,
    Recommendation,
    RemediationTarget,
    RootCause,
    RootCauseCategory,
    Severity,
    diagnose,
    diagnose_rules,
    failing_cases,
)
from bnbagent_eval.exceptions import JudgeError
from bnbagent_eval.judges.prompts.analysis.diagnosis import (
    RECOMMENDATION_V1,
    REMEDIATION_TARGETS,
    ROOT_CAUSE_CATEGORIES,
    ROOT_CAUSE_V1,
)
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import ErrorInfo, EvalResponse

RC = "analysis.diagnosis.root_cause"
REC = "analysis.diagnosis.recommendation"

CASES = [
    EvalCase(id="ok", input="Say hi", output="hi", expected="hi"),
    EvalCase(id="bad", input="Return JSON with the balance", output="balance: 12", expected="x"),
    EvalCase(id="text", input="Say hi", output="hello", expected="hi"),
]


def _response(required: bool = False) -> EvalResponse:
    suite = Suite(
        name="diag",
        version="1",
        metrics=(
            MetricConfig(id="text.exact_match", required=required),
            MetricConfig(id="format.json_valid"),
        ),
    )
    cases = [dataclasses.replace(CASES[0], output='{"a": 1}', expected='{"a": 1}'), *CASES[1:]]
    return EvalClient().evaluate_sync(suite=suite, cases=cases)


def _root_causes(*confidences: float) -> dict:
    return {
        "thinking": "The output is not JSON although the task asked for JSON.",
        "root_causes": [
            {
                "category": "prompt_engineering",
                "description": f"Missing format specification ({c})",
                "evidence": ["output: balance: 12"],
                "confidence": c,
                "severity": "high",
            }
            for c in confidences
        ],
    }


RECS = {
    "thinking": "Add an explicit format instruction.",
    "recommendations": [
        {"target": "model_selection", "action": "Try a stronger model.", "priority": "low"},
        {
            "target": "system_prompt",
            "action": 'Add: "Always reply with a JSON object {"balance": <number>}".',
            "priority": "critical",
        },
    ],
}


def test_prompt_tables_match_agentsight() -> None:
    assert list(ROOT_CAUSE_CATEGORIES) == [c.value for c in RootCauseCategory]
    assert list(REMEDIATION_TARGETS) == [t.value for t in RemediationTarget]
    assert len(ROOT_CAUSE_CATEGORIES) == 7 and len(REMEDIATION_TARGETS) == 7
    assert "tool_output_quality" in ROOT_CAUSE_V1.system
    assert "agent_orchestration" in RECOMMENDATION_V1.system
    assert ROOT_CAUSE_V1.hash() != RECOMMENDATION_V1.hash()


def test_failing_cases_selection() -> None:
    resp = _response()
    ids = [r.case_id for r, _ in failing_cases(resp)]
    assert ids == ["bad", "text"]
    # A low-score floor alone selects nothing new here (scores are 0 / 1).
    assert [r.case_id for r, _ in failing_cases(resp, low_score=0.5)] == ["bad", "text"]


def test_rules_fallback_maps_categories_and_severity() -> None:
    diag = diagnose_rules(_response(required=True))
    by_id = {c.case_id: c for c in diag.cases}
    bad = by_id["bad"]
    assert bad.source == "rules"
    cats = {rc.category for rc in bad.root_causes}
    assert cats == {RootCauseCategory.MODEL_CAPABILITY, RootCauseCategory.PROMPT_ENGINEERING}
    sev = {rc.category: rc.severity for rc in bad.root_causes}
    # Required exact_match scored 0 -> critical; optional json_valid scored 0 -> high.
    assert sev[RootCauseCategory.MODEL_CAPABILITY] is Severity.CRITICAL
    assert sev[RootCauseCategory.PROMPT_ENGINEERING] is Severity.HIGH
    assert [r.priority for r in bad.recommendations] == [Severity.CRITICAL, Severity.HIGH]
    assert {r.target for r in bad.recommendations} == {
        RemediationTarget.MODEL_SELECTION,
        RemediationTarget.SYSTEM_PROMPT,
    }
    assert all(rc.confidence >= 0.5 for rc in bad.root_causes)
    s = diag.summary
    assert (s.total_samples, s.samples_with_root_causes) == (2, 2)
    assert s.top_causes[0] == "model_capability"  # both failing cases miss exact_match
    assert s.summarized_actions[0].priority is Severity.CRITICAL
    assert diag.to_dict()["summary"]["severity_overview"]


def test_rules_error_codes_are_evaluation_issues() -> None:
    resp = _response()
    bad = resp.results[1]
    errored = dataclasses.replace(
        bad.metrics[0],
        status="error",
        score=None,
        passed=None,
        error=ErrorInfo("judge_unavailable", "timeout"),
    )
    target = dataclasses.replace(
        bad.metrics[1], status="error", score=None, error=ErrorInfo("target_error", "500")
    )
    resp = dataclasses.replace(
        resp, results=(dataclasses.replace(bad, metrics=(errored, target)),)
    )
    case = diagnose_rules(resp).cases[0]
    by_target = {r.target: r for r in case.recommendations}
    assert RemediationTarget.OTHER in by_target and RemediationTarget.TOOL_RESPONSE in by_target
    other = next(rc for rc in case.root_causes if rc.category is RootCauseCategory.OTHER)
    assert "not an agent failure" in other.description and other.confidence == 0.9


async def test_two_stage_judge_with_confidence_floor() -> None:
    judge = FakeJudge({RC: _root_causes(0.9, 0.3), REC: RECS})
    diag = await diagnose(_response(), judge=judge, cases=CASES)
    assert [c.case_id for c in diag.cases] == ["bad", "text"]
    bad = diag.cases[0]
    assert bad.source == "judge" and bad.error is None
    assert [rc.confidence for rc in bad.root_causes] == [0.9] and bad.filtered == 1
    # Stage-2 recommendations are sorted by priority (critical first).
    assert [r.priority for r in bad.recommendations] == [Severity.CRITICAL, Severity.LOW]
    assert bad.root_cause_thinking and bad.recommendation_thinking
    stages = [r.metadata["metric_id"] for r in judge.requests]
    assert stages.count(RC) == 2 and stages.count(REC) == 2
    first = judge.requests[0]
    # Untrusted case content is fenced; the failed evaluation is shown.
    assert "<untrusted-" in first.user and "Return JSON with the balance" in first.user
    assert "Evaluator: text.exact_match" in first.user and "Untrusted Content" in first.system
    stage2 = next(r for r in judge.requests if r.metadata["metric_id"] == REC)
    assert "prompt_engineering" in stage2.user and "Missing format specification (0.9)" in (
        stage2.user
    )
    assert "Missing format specification (0.3)" not in stage2.user
    assert diag.summary.top_causes == ["prompt_engineering"]
    assert diag.summary.summarized_actions[0].target is RemediationTarget.SYSTEM_PROMPT


async def test_no_confident_cause_skips_stage_two() -> None:
    judge = FakeJudge({RC: _root_causes(0.4), REC: RECS})
    diag = await diagnose(_response(), judge=judge, cases=CASES, max_cases=1)
    assert len(diag.cases) == 1
    assert diag.cases[0].root_causes == () and diag.cases[0].recommendations == ()
    assert [r.metadata["metric_id"] for r in judge.requests] == [RC]
    assert diag.summary.samples_with_root_causes == 0
    # Raising the floor to 0.3 keeps the cause.
    judge = FakeJudge({RC: _root_causes(0.4), REC: RECS})
    diag = await diagnose(_response(), judge=judge, max_cases=1, min_confidence=0.3)
    assert len(diag.cases[0].root_causes) == 1


async def test_parse_repair_and_judge_failure_fallback() -> None:
    judge = FakeJudge({RC: ["not json", _root_causes(0.8)], REC: RECS})
    diag = await diagnose(_response(), judge=judge, cases=CASES, max_cases=1)
    assert diag.cases[0].source == "judge"
    assert "could not be parsed" in judge.requests[1].user

    failing = FakeJudge({RC: JudgeError("down"), REC: RECS})
    diag = await diagnose(_response(), judge=failing, cases=CASES)
    assert all(c.source == "rules" and "down" in (c.error or "") for c in diag.cases)
    with pytest.raises(JudgeError):
        await diagnose(_response(), judge=failing, fallback=False)
    with pytest.raises(ValueError):
        await diagnose(_response(), min_confidence=1.5)


async def test_no_judge_uses_rules() -> None:
    diag = await diagnose(_response())
    assert diag.cases and all(c.source == "rules" for c in diag.cases)


def test_aggregator_counts_and_priorities() -> None:
    def rc(cat: RootCauseCategory, sev: Severity, conf: float) -> RootCause:
        return RootCause(cat, f"{cat.value} issue", conf, sev)

    agg = DiagnosisAggregator(max_sample_actions=2)
    agg.add(
        CaseDiagnosis(
            "a",
            ("m",),
            (rc(RootCauseCategory.TOOL_USAGE, Severity.MEDIUM, 0.8),),
            (Recommendation(RemediationTarget.TOOL_DEFINITIONS, "fix schema", Severity.MEDIUM),),
        )
    )
    agg.add(
        CaseDiagnosis(
            "b",
            ("m",),
            (
                rc(RootCauseCategory.TOOL_USAGE, Severity.HIGH, 0.6),
                rc(RootCauseCategory.MODEL_CAPABILITY, Severity.LOW, 0.9),
            ),
            (
                Recommendation(RemediationTarget.TOOL_DEFINITIONS, "fix schema", Severity.HIGH),
                Recommendation(RemediationTarget.MODEL_SELECTION, "bigger model", Severity.LOW),
            ),
        )
    )
    agg.add(CaseDiagnosis("c", ("m",)))
    s = agg.summarize()
    assert (s.total_samples, s.samples_with_root_causes, s.samples_with_recommendations) == (
        3,
        2,
        2,
    )
    top = s.top_root_cause_categories[0]
    assert top["category"] == "tool_usage" and top["count"] == 2
    assert top["percentage"] == pytest.approx(66.7) and top["avg_confidence"] == 0.7
    assert s.severity_overview == {"medium": 1, "high": 1, "low": 1}
    first = s.summarized_actions[0]
    assert first.target is RemediationTarget.TOOL_DEFINITIONS
    assert first.priority is Severity.HIGH and first.occurrence_count == 2
    assert first.sample_actions == ("fix schema",)  # de-duplicated
    assert "(2 issues identified)" in first.summary
    assert len(agg.by_category(RootCauseCategory.MODEL_CAPABILITY)) == 1
    assert len(agg.by_target(RemediationTarget.TOOL_DEFINITIONS)) == 2
    agg.reset()
    assert agg.summarize().total_samples == 0
