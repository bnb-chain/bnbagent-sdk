"""Contract inputs for conversation.* metrics."""

from __future__ import annotations

from bnbagent_eval.types import Context, EvalCase, Message

from .catalog import ContractCase

CONVERSATION = (
    Message(role="user", content="How do I stake BNB?"),
    Message(role="assistant", content="Open the staking page and choose a validator."),
    Message(role="user", content="Is there a minimum amount?"),
    Message(role="assistant", content="Yes, the minimum delegation is 1 BNB."),
)
CONTEXTS = (Context(text="The minimum delegation is 1 BNB.", source="docs/staking.md"),)


def conv(**kw) -> EvalCase:
    return EvalCase(id="conv", conversation=CONVERSATION, **kw)


def rag_conv() -> EvalCase:
    return conv(contexts=CONTEXTS, expected="Explain staking and state the 1 BNB minimum.")


def judged(field: str, best: str, worst: str, case=conv, **kw) -> dict:
    return {
        "best": case,
        "worst": case,
        "judge_best": {"reasoning": "r", field: best},
        "judge_worst": {"reasoning": "r", field: worst},
        **kw,
    }


def five_tier(case=conv, **kw) -> dict:
    return judged("score", "Excellent", "Poor", case=case, **kw)


CONTRACT = {
    "conversation.coherence": ContractCase(**judged("level", "Highly Coherent", "Incoherent")),
    "conversation.completeness": ContractCase(**judged("level", "Fully Complete", "Incomplete")),
    "conversation.knowledge_retention": ContractCase(
        **judged("level", "Perfect Retention", "No Retention")
    ),
    "conversation.entity_consistency": ContractCase(**five_tier()),
    "conversation.turn_relevancy": ContractCase(**five_tier()),
    "conversation.role_adherence": ContractCase(
        **five_tier(params={"role": "staking support agent"})
    ),
    "conversation.topic_adherence": ContractCase(
        **five_tier(params={"topics": ["staking", "validators"]})
    ),
    "conversation.turn_faithfulness": ContractCase(**five_tier(case=rag_conv)),
    "conversation.turn_context_precision": ContractCase(**five_tier(case=rag_conv)),
    "conversation.turn_context_recall": ContractCase(**five_tier(case=rag_conv)),
    "conversation.turn_context_relevancy": ContractCase(**five_tier(case=rag_conv)),
}
