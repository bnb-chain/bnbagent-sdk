"""Contract inputs for the new agent.* judges (task, tool selection, planning, MCP)."""

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

TASK = "Find the current BNB price and report it in USD."


def session(tool: str) -> Session:
    steps = (
        AgentStep(
            id="a",
            user_prompt=TASK,
            response="BNB trades at 600 USD.",
            available_tools=({"name": tool, "description": "Look up a token price"},),
        ),
        LlmStep(id="l", parent_id="a", response="Plan: 1. look up the price 2. report it."),
        ToolStep(
            id="t",
            parent_id="a",
            call=ToolCall(name=tool, arguments={"symbol": "BNB"}, id="c1"),
            result=ToolResult(content={"usd": 600}, call_id="c1"),
        ),
    )
    return Session(id="s", traces=(Trace(id="tr", steps=steps),))


def traj_case(tool: str = "price_lookup") -> EvalCase:
    return EvalCase(id="a", input=TASK, output="BNB trades at 600 USD.", trajectory=session(tool))


def five_tier(case=traj_case, **extra) -> dict:
    return {
        "best": case,
        "worst": case,
        "judge_best": {"reasoning": "r", "score": "Excellent", **extra},
        "judge_worst": {"reasoning": "r", "score": "Poor", **extra},
    }


def mcp_case() -> EvalCase:
    return traj_case("mcp__prices__lookup")


CONTRACT = {
    "agent.task_completion": ContractCase(**five_tier(unmet_requirements=[])),
    "agent.tool_selection": ContractCase(**five_tier(missing_tools=[])),
    "agent.plan_quality": ContractCase(**five_tier(plan_found=True)),
    "agent.plan_adherence": ContractCase(**five_tier(deviations=[])),
    "agent.mcp_use": ContractCase(**five_tier(mcp_case, mcp_issues=[])),
    "agent.mcp_task_completion": ContractCase(**five_tier(mcp_case, task="price")),
}
