"""Contract inputs for ``quality.correctness`` (native judge; agentcore in unit tests)."""

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


def _session(response: str) -> Session:
    steps = (
        AgentStep(id="a", user_prompt="What is 2 + 2?", response=response),
        LlmStep(
            id="l",
            parent_id="a",
            messages=({"role": "user", "content": "What is 2 + 2?"},),
            tool_calls=(ToolCall(name="calc", arguments={"expr": "2+2"}, id="c1"),),
        ),
        ToolStep(
            id="t",
            parent_id="a",
            call=ToolCall(name="calc", arguments={"expr": "2+2"}, id="c1"),
            result=ToolResult(content="4", call_id="c1"),
        ),
    )
    return Session(id="s", traces=(Trace(id="t1", steps=steps),))


CONTRACT = {
    "quality.correctness": ContractCase(
        # output + input for the native judge, the trajectory for the agentcore method
        best=lambda: EvalCase(
            id="c", input="What is 2 + 2?", output="The answer is four.", trajectory=_session("4")
        ),
        worst=lambda: EvalCase(
            id="c", input="What is 2 + 2?", output="The answer is five.", trajectory=_session("5")
        ),
        judge_best={"reasoning": "correct", "score": "Excellent", "errors": []},
        judge_worst={"reasoning": "wrong sum", "score": "Poor", "errors": ["2 + 2 is 4"]},
    ),
}
