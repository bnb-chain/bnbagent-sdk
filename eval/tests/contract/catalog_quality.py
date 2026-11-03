"""Contract inputs for quality.* metrics."""

from __future__ import annotations

from bnbagent_eval.judges.prompts.quality.conciseness import SUB_LABELS
from bnbagent_eval.types import EvalCase

from .catalog import ContractCase


def qa(**kw) -> EvalCase:
    return EvalCase(id="q", input="How do I stake BNB?", output="Open the staking page.", **kw)


def five_tier(best: str = "Excellent", worst: str = "Poor", **extra) -> dict:
    return {
        "best": qa,
        "worst": qa,
        "judge_best": {"reasoning": "r", "score": best, **extra},
        "judge_worst": {"reasoning": "r", "score": worst, **extra},
    }


def conciseness_reply(level: int) -> dict:
    """level 4 = best label on every dimension, 0 = worst."""
    return {"reasoning": "r", **{k: list(v)[level] for k, v in SUB_LABELS.items()}}


CONTRACT = {
    "quality.relevance": ContractCase(**five_tier()),
    "quality.answer_correctness": ContractCase(
        best=lambda: EvalCase(
            id="a", input="Capital of France?", output="Paris", expected="Paris"
        ),
        worst=lambda: EvalCase(
            id="a", input="Capital of France?", output="Rome", expected="Paris"
        ),
        judge_best={"reasoning": "r", "score": "Excellent"},
        judge_worst={"reasoning": "r", "score": "Poor"},
    ),
    "quality.clarity": ContractCase(**five_tier(clarity_issues=[])),
    "quality.completeness": ContractCase(**five_tier(missing_elements=[])),
    "quality.conciseness": ContractCase(
        best=qa,
        worst=qa,
        judge_best=conciseness_reply(4),
        judge_worst=conciseness_reply(0),
    ),
    "quality.fluency": ContractCase(**five_tier(grammar_issues=[])),
    "quality.helpfulness": ContractCase(**five_tier()),
    "quality.tone": ContractCase(**five_tier(tone_issues=[])),
    "quality.contradiction": ContractCase(
        best=qa,
        worst=qa,
        judge_best={"reasoning": "r", "level": "No Contradictions", "contradictions": []},
        judge_worst={"reasoning": "r", "level": "Major Contradictions", "contradictions": ["x"]},
    ),
    "quality.coherence": ContractCase(**five_tier(coherence_issues=[])),
    "quality.instruction_following": ContractCase(**five_tier(instructions_violated=[])),
    "quality.summarization": ContractCase(**five_tier(unsupported_claims=[])),
}
