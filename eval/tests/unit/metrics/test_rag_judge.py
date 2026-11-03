"""Golden tests for rag.* judge metrics (FakeJudge)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.judges.sampling import median_low
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.metrics.rag.context_freshness import (
    config_section,
    format_timestamp,
    parse_timestamp,
    timestamp_rules,
)
from bnbagent_eval.types import Context, EvalCase, MetricConfig, MetricResult

UTC = timezone.utc


async def judged(
    metric_id: str, case: EvalCase, script: Any, params: dict | None = None, **ctx_kw: Any
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=cls.spec.method(),
        params=params,
        judge=judge,
        **ctx_kw,
    )
    return await cls().evaluate(case, ctx), judge


def rag_case(n_contexts: int = 2, **kw: Any) -> EvalCase:
    contexts = kw.pop(
        "contexts", tuple(Context(text=f"context {i}") for i in range(1, n_contexts + 1))
    )
    return EvalCase(
        id="r", input="How do I stake?", output="Use the staking page.", contexts=contexts, **kw
    )


# --------------------------------------------------------------------------- faithfulness


def faith_reply(overall: str, *verdicts: str) -> str:
    return json.dumps(
        {
            "atomic_claims_analysis": [
                {"claim": f"c{i}", "verdict": v} for i, v in enumerate(verdicts)
            ],
            "omission_analysis": "nothing missing",
            "reasoning_summary": "summary",
            "overall_score": overall,
        }
    )


@pytest.mark.parametrize(
    ("label", "score"),
    [("Poor", 0.0), ("Below Average", 0.25), ("Average", 0.5), ("Good", 0.75), ("Excellent", 1)],
)
async def test_faithfulness_label_mapping(label: str, score: float) -> None:
    res, _ = await judged("rag.faithfulness", rag_case(), faith_reply(label, "Supported"))
    assert res.score == score and res.details["label"] == label and res.reason == "summary"


async def test_faithfulness_per_claim_details_and_prompt() -> None:
    reply = faith_reply("Good", "Supported", "Partial", "Hallucinated", "Contradicted")
    res, judge = await judged("rag.faithfulness", rag_case(), reply)
    claims = res.details["atomic_claims_analysis"]
    assert [c["score"] for c in claims] == [1.0, 0.5, 0.25, 0.0]
    assert res.details["avg_claim_score"] == pytest.approx(0.4375)
    assert res.details["omission_analysis"] == "nothing missing"
    assert res.score == 0.75  # overall rating, not the claim average (AgentSight)
    user = judge.requests[0].user
    assert "# Retrieval Context (Source Documents):" in user and "context 2" in user
    assert "Factual Correctness Evaluator" in judge.requests[0].system


async def test_faithfulness_input_is_optional_and_no_claims_average_zero() -> None:
    case = EvalCase(id="r", output="x", contexts=(Context(text="c"),))
    res, _ = await judged("rag.faithfulness", case, faith_reply("Average"))
    assert res.status == "scored" and res.score == 0.5 and res.details["avg_claim_score"] == 0.0


# --------------------------------------------------------------------------- groundedness


async def test_groundedness_does_not_need_a_query() -> None:
    case = EvalCase(id="g", output="answer", contexts=(Context(text="c"),))
    res, judge = await judged(
        "rag.groundedness",
        case,
        {"reasoning": "r", "score": "Good", "unsupported_statements": ["x"]},
    )
    assert res.score == 0.75 and res.details["unsupported_statements"] == ["x"]
    assert "# Retrieved Contexts:" in judge.requests[0].user


# --------------------------------------------------------------------------- hallucination


async def test_hallucination_inverts_and_keeps_raw() -> None:
    reply = {
        "reasoning": "one contradiction",
        "verdicts": [
            {"context_id": 1, "verdict": "yes"},
            {"context_id": 2, "verdict": "no", "reason": "contradicts"},
            {"context_id": 3, "verdict": "yes"},
            {"context_id": 4, "verdict": "yes"},
        ],
    }
    res, _ = await judged("rag.hallucination", rag_case(4), reply)
    assert res.details["raw"] == 0.25 and res.score == 0.75
    assert res.details["hallucinated_contexts"] == 1 and res.details["num_verdicts"] == 4
    assert res.reason == "one contradiction"


# --------------------------------------------------------------------------- context quality


def cq_reply(overall: float, *verdicts: str, llm: float = 0.42) -> str:
    return json.dumps(
        {
            "query_analysis": "q",
            "context_analysis": [
                {"context_id": i, "verdict": v, "reasoning": f"r{i}"}
                for i, v in enumerate(verdicts, 1)
            ],
            "overall_relevance_score": overall,
            "context_precision": llm,
            "retrieval_quality_label": "Average",
            "score_reasoning": "because",
        }
    )


async def test_context_relevance_divides_by_five() -> None:
    reply = cq_reply(3, "Relevant", "Not Relevant")
    res, judge = await judged("rag.context_relevance", rag_case(), reply)
    assert res.score == pytest.approx(0.6) and res.details["raw"] == 3.0
    assert res.passed is True and res.threshold == 0.6
    assert [c["score"] for c in res.details["context_analysis"]] == [0.75, 0.0]
    assert res.details["label"] == "Average" and res.reason == "because"
    assert "**Context 1:**\ncontext 1\n" in judge.requests[0].user


async def test_context_precision_share_and_threshold() -> None:
    verdicts = ("Highly Relevant", "Partially Relevant", "Marginally Relevant", "Not Relevant")
    reply = cq_reply(2, *verdicts)
    res, _ = await judged("rag.context_precision", rag_case(4), reply)
    assert res.score == 0.5  # Highly + Partially reach the default 0.5
    assert res.details["relevant_context_count"] == 2 and res.details["num_contexts"] == 4
    assert (
        res.details["context_precision_llm"] == 0.42 and res.details["relevance_threshold"] == 0.5
    )
    strict, _ = await judged(
        "rag.context_precision", rag_case(4), reply, {"relevance_threshold": 0.75}
    )
    assert strict.score == 0.25
    loose, _ = await judged(
        "rag.context_precision", rag_case(4), reply, {"relevance_threshold": 0}
    )
    assert loose.score == 1.0


async def test_context_relevance_and_precision_share_one_prompt() -> None:
    from bnbagent_eval.judges.cache import MemoryCache

    reply = cq_reply(5, "Highly Relevant", "Highly Relevant")
    cache = MemoryCache()
    _, j1 = await judged("rag.context_relevance", rag_case(), reply, cache=cache)
    res, j2 = await judged("rag.context_precision", rag_case(), reply, cache=cache)
    assert len(j1.requests) == 1 and j2.requests == [] and res.score == 1.0


# --------------------------------------------------------------------------- context recall


async def test_context_recall_share_of_attributed_statements() -> None:
    reply = {
        "reasoning": "r",
        "statements": [
            {"statement": "a", "attributed": "yes"},
            {"statement": "b", "attributed": "no"},
            {"statement": "c", "attributed": "yes"},
            {"statement": "d", "attributed": "yes"},
        ],
    }
    res, judge = await judged("rag.context_recall", rag_case(expected="ref answer"), reply)
    assert res.score == 0.75 and res.details["attributed_count"] == 3
    assert "# Expected Answer (Reference):" in judge.requests[0].user
    missing, _ = await judged("rag.context_recall", rag_case(), reply)
    assert missing.status == "skipped" and missing.error.code == "missing_input"


# --------------------------------------------------------------------------- freshness


def fresh_reply(score: float, sensitivity: str = "HIGH") -> str:
    return json.dumps(
        {
            "temporal_sensitivity": sensitivity,
            "temporal_sensitivity_reasoning": "r",
            "context_analysis": [
                {
                    "context_id": 1,
                    "has_timestamp": True,
                    "age_in_days": 1,
                    "age_assessment": "appropriate",
                    "reasoning": "r",
                }
            ],
            "missing_timestamps_count": 0,
            "missing_timestamps_impact": "none",
            "overall_freshness_score": score,
            "score_reasoning": "fresh enough",
            "potential_issues": [],
            "recommendations": [],
        }
    )


@pytest.mark.parametrize(
    ("value", "iso"),
    [
        ("2026-10-30T12:00:00Z", "2026-10-30T12:00:00Z"),
        ("2026-10-30T12:00:00.5Z", "2026-10-30T12:00:00Z"),
        ("2026-10-30T14:00:00+02:00", "2026-10-30T14:00:00Z"),  # strftime keeps local time
        ("2026-10-30", "2026-10-30T00:00:00Z"),
        ("2026/10/30", "2026-10-30T00:00:00Z"),
        ("10/30/2026", "2026-10-30T00:00:00Z"),
        ("30/10/2026", "2026-10-30T00:00:00Z"),
        ("Oct 30, 2026", "2026-10-30T00:00:00Z"),
        ("October 30, 2026", "2026-10-30T00:00:00Z"),
        ("30 Oct 2026", "2026-10-30T00:00:00Z"),
        ("2026-10-30 08:30:00", "2026-10-30T08:30:00Z"),
        (1793361600, "2026-10-30T12:00:00Z"),
        (datetime(2026, 10, 30, 12), "2026-10-30T12:00:00Z"),
    ],
)
def test_parse_timestamp_formats(value: Any, iso: str) -> None:
    assert format_timestamp(parse_timestamp(value)) == iso


def test_parse_timestamp_failures() -> None:
    assert parse_timestamp(None) is None and parse_timestamp("  ") is None
    assert parse_timestamp("not a date") is None
    assert format_timestamp(None) == "No timestamp available"


QUERY = datetime(2026, 10, 30, tzinfo=UTC)


def aged_case() -> EvalCase:
    contexts = (
        Context(text="a", timestamp="2026-10-29T00:00:00Z"),  # 1 day
        Context(text="b", timestamp="2026-10-28"),  # 2 days
        Context(text="c", timestamp="2026-09-01"),  # 59 days
        Context(text="d", timestamp="2025-06-01"),  # 516 days
        Context(text="e"),  # no timestamp
    )
    return rag_case(contexts=contexts)


@pytest.mark.parametrize(
    ("sensitivity", "limit", "within"),
    [
        ("CRITICAL", 1, [True, False, False, False, None]),
        ("HIGH", 30, [True, True, False, False, None]),
        ("MODERATE", 365, [True, True, True, False, None]),
        ("LOW", None, [True, True, True, True, None]),
    ],
)
def test_timestamp_rules_per_tier(sensitivity: str, limit: int | None, within: list) -> None:
    from bnbagent_eval.metrics.rag.context_freshness import DEFAULT_FRESHNESS_THRESHOLDS

    rules = timestamp_rules(aged_case(), QUERY, sensitivity, DEFAULT_FRESHNESS_THRESHOLDS)
    assert rules["max_age_days"] == limit
    assert [c["age_in_days"] for c in rules["contexts"]] == [1, 2, 59, 516, None]
    assert [c["within_threshold"] for c in rules["contexts"]] == within
    assert rules["missing_timestamps"] == 1
    assert rules["fresh_share"] == sum(1 for w in within if w) / 5


def test_config_section_is_agentsight_text() -> None:
    thresholds = {"CRITICAL": 1, "HIGH": 7, "MODERATE": 365, "LOW": None}
    section = config_section(thresholds, "HIGH")
    assert "- HIGH: Maximum 7 days old\n" in section and "- LOW: No age limit" in section
    assert "pre-determined as: **HIGH**" in section
    assert "corresponding threshold of 7 days." in section
    assert "Override" not in config_section(thresholds, None)


async def test_freshness_score_override_and_details() -> None:
    params = {
        "temporal_sensitivity": "CRITICAL",
        "freshness_thresholds": {"CRITICAL": 2},
        "query_timestamp": "2026-10-30",
    }
    res, judge = await judged("rag.context_freshness", aged_case(), fresh_reply(4), params)
    assert res.score == pytest.approx(0.8) and res.details["raw"] == 4.0
    assert res.reason == "fresh enough" and res.details["label"] == "HIGH"
    rules = res.details["timestamp_rules"]
    assert rules["sensitivity"] == "CRITICAL" and rules["max_age_days"] == 2
    assert [c["within_threshold"] for c in rules["contexts"]][:2] == [True, True]
    assert res.details["query_timestamp"] == "2026-10-30T00:00:00Z"
    assert res.details["temporal_sensitivity_override"] == "CRITICAL"
    req = judge.requests[0]
    assert "- CRITICAL: Maximum 2 days old" in req.system and "**CRITICAL**" in req.system
    assert "Published/Updated: 2026-10-29T00:00:00Z" in req.user
    assert "Published/Updated: No timestamp available" in req.user


async def test_freshness_uses_judge_sensitivity_metadata_timestamp_and_prompt_hash() -> None:
    case = rag_case(contexts=aged_case().contexts, metadata={"query_timestamp": "2026-10-30"})
    res, _ = await judged("rag.context_freshness", case, fresh_reply(5, "MODERATE"))
    assert res.score == 1.0
    assert res.details["timestamp_rules"]["sensitivity"] == "MODERATE"
    assert res.details["timestamp_rules"]["max_age_days"] == 365
    cls, _ = registry.resolve("rag.context_freshness")
    method = cls.spec.method()
    assert cls().prompt_hash(method, {}) != cls().prompt_hash(
        method, {"temporal_sensitivity": "LOW"}
    )


async def test_freshness_defaults_query_time_to_clock() -> None:
    case = rag_case(contexts=(Context(text="a", timestamp="2026-10-29"),))
    res, judge = await judged(
        "rag.context_freshness", case, fresh_reply(3), clock=lambda: QUERY.timestamp()
    )
    assert res.details["query_timestamp"] == "2026-10-30T00:00:00Z"
    assert "2026-10-30T00:00:00Z" in judge.requests[0].user
    assert "(No answer provided)" not in judge.requests[0].user


async def test_samples_take_lower_median() -> None:
    script = [faith_reply("Excellent"), faith_reply("Poor"), faith_reply("Good")]
    res, judge = await judged("rag.faithfulness", rag_case(), script, {"samples": 3})
    assert len(judge.requests) == 3 and res.score == median_low([1.0, 0.0, 0.75]) == 0.75
    assert res.details["sample_scores"] == [1.0, 0.0, 0.75]


async def test_hallucination_samples_aggregate_towards_worse() -> None:
    from bnbagent_eval.judges.providers.fake import FakeJudge
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.metrics.base import MetricContext
    from bnbagent_eval.types import Context, EvalCase, MetricConfig

    clean = {"reasoning": "r", "verdicts": [{"verdict": "yes", "reason": "ok"}]}
    bad = {"reasoning": "r", "verdicts": [{"verdict": "no", "reason": "contradicts"}]}
    cls, _ = registry.resolve("rag.hallucination")
    params = {"samples": 2}
    ctx = MetricContext(
        config=MetricConfig(id="rag.hallucination", params=params),
        method=cls.spec.method(),
        params=params,
        judge=FakeJudge([clean, bad]),
    )
    case = EvalCase(id="h", output="a", contexts=(Context(text="c"),))
    res = await cls().evaluate(case, ctx)
    assert res.score == 0.0 and res.details["raw"] == 1.0  # the worse sample wins
