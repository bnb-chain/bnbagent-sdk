"""Trace and Session evaluation levels see *different* inputs (AgentSight gave both levels
the same input)."""

from __future__ import annotations

from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import (
    AgentStep,
    EvalCase,
    LlmStep,
    MetricConfig,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)


def _trace(tid: str, question: str, tool: str) -> Trace:
    call = ToolCall(name=tool, arguments={"q": tid}, id=f"{tid}-c")
    return Trace(
        id=tid,
        steps=(
            AgentStep(id=f"{tid}-a", agent_id="w", user_prompt=question, response=f"done {tid}"),
            LlmStep(id=f"{tid}-l", response=f"thinking {tid}", tool_calls=(call,)),
            ToolStep(id=f"{tid}-t", call=call, result=ToolResult(content=f"out {tid}")),
        ),
    )


CASE = EvalCase(
    id="c",
    trajectory=Session(
        id="s",
        traces=(
            _trace("t1", "FIRST-QUESTION", "tool_alpha"),
            _trace("t2", "SECOND-Q", "tool_beta"),
        ),
    ),
)


async def _run(metric_id: str, script) -> FakeJudge:
    cls, _ = registry.resolve(metric_id)
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id),
        method=cls.spec.method(),
        params={},
        judge=judge,
    )
    res = await cls().evaluate(CASE, ctx)
    assert res.status == "scored", res.error
    return judge


async def test_trace_level_judges_each_trace_alone() -> None:
    judge = await _run("agent.tool_parameter_accuracy", {"analysis": "a", "judgment": "All"})
    assert len(judge.requests) == 2  # one call per invocation (trace)
    first, second = (r.user for r in judge.requests)
    assert "tool_alpha" in first and "tool_beta" not in first
    assert "tool_beta" in second and "out t1" not in second


async def test_session_level_judges_the_whole_session_once() -> None:
    goals = {"reasoning": "r", "goals": [{"goal_description": "g", "goal_success_score": 1}]}
    judge = await _run("agent.goal_success", goals)
    assert len(judge.requests) == 1
    prompt = judge.requests[0].user
    assert "tool_alpha" in prompt and "tool_beta" in prompt


async def test_levels_produce_different_inputs() -> None:
    trace_level = await _run("agent.tool_parameter_accuracy", {"analysis": "a", "judgment": "All"})
    goals = {"reasoning": "r", "goals": [{"goal_description": "g", "goal_success_score": 1}]}
    session_level = await _run("agent.goal_success", goals)
    assert {r.user for r in trace_level.requests}.isdisjoint({session_level.requests[0].user})
