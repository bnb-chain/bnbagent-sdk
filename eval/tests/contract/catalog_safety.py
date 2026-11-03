"""Contract inputs for safety.* metrics."""

from __future__ import annotations

from bnbagent_eval.types import EvalCase

from .catalog import ContractCase


def qa(**kw) -> EvalCase:
    return EvalCase(id="q", input="How do I stake BNB?", output="Open the staking page.", **kw)


def judged(best: dict, worst: dict, **kw) -> dict:
    return {
        "best": qa,
        "worst": qa,
        "judge_best": {"reasoning": "r", **best},
        "judge_worst": {"reasoning": "r", **worst},
        **kw,
    }


def severity(best: str, worst: str) -> dict:
    return judged({"severity": best}, {"severity": worst})


def five_tier(**kw) -> dict:
    return judged({"score": "Excellent"}, {"score": "Poor"}, **kw)


CONTRACT = {
    "safety.harmfulness": ContractCase(**severity("Safe", "High Risk")),
    "safety.bias": ContractCase(**severity("Unbiased", "High Bias")),
    "safety.pii_leakage": ContractCase(**severity("No PII", "Critical Risk")),
    "safety.toxicity": ContractCase(**judged({"score": 0.0}, {"score": 1.0})),
    "safety.stereotyping": ContractCase(**five_tier()),
    "safety.refusal": ContractCase(
        **judged(
            {"is_refusal": True, "category": "Safety Concerns"},
            {"is_refusal": False, "category": "No Refusal"},
            params={"expect_refusal": True},
        )
    ),
    "safety.misuse": ContractCase(**five_tier(params={"domain": "BNB staking support"})),
    "safety.non_advice": ContractCase(**five_tier(params={"advice_types": ["financial"]})),
    "safety.role_violation": ContractCase(**five_tier(params={"role": "staking support agent"})),
}
