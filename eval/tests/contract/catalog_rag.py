"""Contract inputs for rag.* metrics.

Replies without a top-level ``reasoning`` / ``score`` / ``verdict`` key are scripted as JSON
strings, because FakeJudge reads such a mapping as ``metric_id -> reply``.
"""

from __future__ import annotations

import json

from bnbagent_eval.types import Context, EvalCase

from .catalog import ContractCase

CONTEXTS = (
    Context(text="Staking BNB is done on the staking page.", timestamp="2026-10-29T00:00:00Z"),
    Context(text="Validators earn rewards.", timestamp="2026-10-01"),
)


def rag_case(**kw) -> EvalCase:
    return EvalCase(
        id="r",
        input="How do I stake BNB?",
        output="Open the staking page.",
        expected="Stake BNB on the staking page.",
        contexts=CONTEXTS,
        metadata={"query_timestamp": "2026-10-30T00:00:00Z"},
        **kw,
    )


def same(best, worst, **kw) -> dict:
    return {"best": rag_case, "worst": rag_case, "judge_best": best, "judge_worst": worst, **kw}


def faithfulness(overall: str, verdict: str) -> str:
    return json.dumps(
        {
            "atomic_claims_analysis": [{"claim": "Open the staking page.", "verdict": verdict}],
            "omission_analysis": "none",
            "reasoning_summary": "r",
            "overall_score": overall,
        }
    )


def context_quality(overall: float, verdict: str) -> str:
    return json.dumps(
        {
            "context_analysis": [
                {"context_id": i, "verdict": verdict, "reasoning": "r"} for i in (1, 2)
            ],
            "overall_relevance_score": overall,
            "context_precision": overall / 5,
            "retrieval_quality_label": "Excellent" if overall else "Poor",
            "score_reasoning": "r",
        }
    )


def freshness(score: float) -> str:
    return json.dumps(
        {
            "temporal_sensitivity": "HIGH",
            "temporal_sensitivity_reasoning": "r",
            "context_analysis": [],
            "missing_timestamps_count": 0,
            "missing_timestamps_impact": "none",
            "overall_freshness_score": score,
            "score_reasoning": "r",
            "potential_issues": [],
            "recommendations": [],
        }
    )


def verdicts(v: str) -> dict:
    return {"reasoning": "r", "verdicts": [{"context_id": i, "verdict": v} for i in (1, 2)]}


def recall(v: str) -> dict:
    return {"reasoning": "r", "statements": [{"statement": "s", "attributed": v}]}


CONTRACT = {
    "rag.faithfulness": ContractCase(
        **same(faithfulness("Excellent", "Supported"), faithfulness("Poor", "Contradicted"))
    ),
    "rag.groundedness": ContractCase(
        **same({"reasoning": "r", "score": "Excellent"}, {"reasoning": "r", "score": "Poor"})
    ),
    "rag.hallucination": ContractCase(**same(verdicts("yes"), verdicts("no"))),
    "rag.context_relevance": ContractCase(
        **same(context_quality(5, "Highly Relevant"), context_quality(0, "Not Relevant"))
    ),
    "rag.context_precision": ContractCase(
        **same(context_quality(5, "Highly Relevant"), context_quality(0, "Not Relevant"))
    ),
    "rag.context_recall": ContractCase(**same(recall("yes"), recall("no"))),
    "rag.context_freshness": ContractCase(**same(freshness(5), freshness(0))),
}
