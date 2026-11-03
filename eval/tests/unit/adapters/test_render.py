"""Trajectory rendering for judges: each tool call appears once."""

from __future__ import annotations

from bnbagent_eval.adapters.traces.render import executed_llm_calls, render_trajectory
from bnbagent_eval.types import LlmStep, Session, ToolCall, ToolResult, ToolStep, Trace


def _session(llm_id: str | None, tool_id: str | None) -> Session:
    balance = ToolCall(name="balance", arguments={"t": "BNB"}, id=llm_id)
    executed = ToolCall(name="balance", arguments={"t": "BNB"}, id=tool_id)
    steps = (
        LlmStep(id="l", response="checking", tool_calls=(balance,)),
        ToolStep(id="t", call=executed, result=ToolResult(content="1.5")),
        LlmStep(id="l2", response="", tool_calls=(ToolCall(name="transfer", arguments={}),)),
    )
    return Session(id="s", traces=(Trace(id="t1", steps=steps),))


def test_executed_calls_render_once() -> None:
    for llm_id, tool_id in (("c1", "c1"), (None, None), ("c1", None), (None, "c1")):
        text = render_trajectory(_session(llm_id, tool_id))
        assert text.count("balance(") == 1, (llm_id, tool_id, text)
        assert text.count("transfer(") == 1  # planned, never executed: shown once


def test_executed_llm_calls_matching() -> None:
    trace = _session(None, None).traces[0]
    assert executed_llm_calls(trace) == {(0, 0)}
    different = _session("a", "b").traces[0]  # ids on both sides that differ: not the same call
    assert executed_llm_calls(different) == set()
