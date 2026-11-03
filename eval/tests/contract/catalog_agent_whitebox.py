"""Contract inputs for the AgentSight whitebox agent.* judge metrics."""

from __future__ import annotations

from typing import Any

from bnbagent_eval.judges.prompts.agent.reasoning import DEFAULT_RUBRICS
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

ANSWER = "Your testnet balance is 1.5 tBNB."
RUBRIC = "The agent calls get_balance with the user's address."
CALL = ToolCall(name="get_balance", arguments={"address": "0xabc"}, id="c1")


def agent_case() -> EvalCase:
    steps = (
        AgentStep(
            id="a",
            agent_id="wallet",
            system_prompt="You answer wallet questions.",
            user_prompt="What is the balance of 0xabc?",
            response=ANSWER,
            available_tools=({"name": "get_balance", "description": "Balance of an address"},),
        ),
        LlmStep(id="l", parent_id="a", response="I will look up the balance.", tool_calls=(CALL,)),
        ToolStep(id="t", parent_id="a", call=CALL, result=ToolResult(content="1.5", call_id="c1")),
    )
    session = Session(id="s", traces=(Trace(id="t1", steps=steps),))
    return EvalCase(id="w", output=ANSWER, trajectory=session)


def rubric_reply(verdict: str, *texts: str) -> dict[str, Any]:
    return {"evaluations": [{"property": t, "rationale": "r", "verdict": verdict} for t in texts]}


def chain_reply(score: int) -> dict[str, Any]:
    return {
        "chain_scores": {
            k: {"score": score, "evidence": "e", "cascading_impact": False}
            for k in (
                "intent_understanding",
                "plan_quality",
                "tool_selection",
                "tool_execution",
                "result_interpretation",
                "output_quality",
            )
        },
        "overall_reasoning": "r",
        "critical_failures": [],
    }


def cc(best: dict[str, Any], worst: dict[str, Any], **kw: Any) -> ContractCase:
    return ContractCase(
        best=agent_case, worst=agent_case, judge_best=best, judge_worst=worst, **kw
    )


REASONING_TEXTS = [t for _, t in DEFAULT_RUBRICS]
RUBRICS = {"rubrics": [{"id": "balance", "text": RUBRIC}]}

CONTRACT = {
    "agent.goal_success": cc(
        {"reasoning": "r", "goals": [{"goal_description": "g", "goal_success_score": 1.0}]},
        {"reasoning": "r", "goals": [{"goal_description": "g", "goal_success_score": 0.0}]},
    ),
    "agent.tool_use_quality": cc(
        rubric_reply("yes", RUBRIC), rubric_reply("no", RUBRIC), params=RUBRICS
    ),
    "agent.multi_agent_trajectory": cc(
        rubric_reply("yes", RUBRIC), rubric_reply("no", RUBRIC), params=RUBRICS
    ),
    "agent.reasoning": cc(
        rubric_reply("yes", *REASONING_TEXTS), rubric_reply("no", *REASONING_TEXTS)
    ),
    "agent.tool_parameter_accuracy": cc(
        {"analysis": "a", "judgment": "All"}, {"analysis": "a", "judgment": "None"}
    ),
    "agent.adaptivity": cc(
        {
            "analysis": "a",
            "adaptive_steps": [0],
            "not_adaptive_steps": [],
            "not_relevant_steps": [],
        },
        {
            "analysis": "a",
            "adaptive_steps": [],
            "not_adaptive_steps": [0],
            "not_relevant_steps": [],
        },
    ),
    "agent.reasoning_hallucination": cc(
        {"analysis": "a", "hallucination_steps": [], "valid_steps": [0]},
        {"analysis": "a", "hallucination_steps": [0], "valid_steps": []},
    ),
    "agent.trajectory_efficiency": cc(
        {"analysis": "a", "inefficient_steps": [], "efficient_steps": [0]},
        {"analysis": "a", "inefficient_steps": [0], "efficient_steps": []},
    ),
    "agent.execution_chain": cc(chain_reply(5), chain_reply(1)),
}
