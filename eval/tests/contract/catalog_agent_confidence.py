"""Contract inputs for agent.confidence.

With the default weights the confidence cannot reach 0.0: errors <= 0.8 (an explicit error
excludes an inferred one), loops < 1, hedging <= 0.7 (apology density is always 0) and
deflection <= 0.8, so confidence stays above 0.11. The contract therefore puts all the weight on
depth and relevance (both of which can reach 1.0) to exercise the full [0, 1] range.
"""

from __future__ import annotations

from bnbagent_eval.types import (
    AgentStep,
    EvalCase,
    LlmStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

from .catalog import ContractCase

WEIGHTS = {"errors": 0, "loops": 0, "depth": 0.5, "relevance": 0.5, "hedging": 0, "deflection": 0}

ANSWER = (
    "Here are the staking pools you asked me to show:\n"
    "- pool v1.2 at https://pools.example/a\n"
    "- pool v1.3 at https://pools.example/b"
)


def _session(*steps) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=tuple(steps)),))


def best() -> EvalCase:
    """On-topic list answer with concrete identifiers, one clean tool call, depth 2."""
    return EvalCase(
        id="clean",
        trajectory=_session(
            AgentStep(id="a", agent_id="main", user_prompt="show staking pools", response=ANSWER),
            ToolStep(
                id="t1",
                parent_id="a",
                call=ToolCall(name="staking_pools", arguments={"query": "staking pools"}),
                result=ToolResult(content="staking pools: alpha, beta"),
            ),
        ),
    )


def worst() -> EvalCase:
    """Off-topic three-word answer to an "explain" query, nested six steps deep."""
    return EvalCase(
        id="worst",
        trajectory=_session(
            AgentStep(id="0", user_prompt="explain staking rewards", response="It is so."),
            *(LlmStep(id=str(i), parent_id=str(i - 1)) for i in range(1, 6)),
        ),
    )


CONTRACT = {
    "agent.confidence": ContractCase(best=best, worst=worst, params={"weight_overrides": WEIGHTS}),
}
