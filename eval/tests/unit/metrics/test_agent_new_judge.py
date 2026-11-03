"""Golden tests for the new agent.* judges: task, tool selection, planning, MCP (FakeJudge)."""

from __future__ import annotations

from typing import Any

import pytest

from bnbagent_eval.judges.prompts import FIVE_TIER
from bnbagent_eval.judges.prompts.agent._section_modes import section_mode, section_parts
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.agent._mcp import (
    conversation_mcp_calls,
    mcp_target,
    mcp_tools,
    session_mcp_calls,
)
from bnbagent_eval.metrics.agent._section_judge import expected_calls_text, plan_text
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import (
    AgentStep,
    EvalCase,
    LlmStep,
    Message,
    MetricConfig,
    MetricResult,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

TASK = "Swap 1 BNB for USDT on the test DEX."
PLAN = "Plan: 1. quote the swap 2. execute the swap 3. report the result."
TOOLS = (
    {"name": "dex_quote", "description": "Quote a token swap"},
    {"name": "dex_swap", "description": "Execute a token swap"},
)

METRICS = [
    "agent.task_completion",
    "agent.tool_selection",
    "agent.plan_quality",
    "agent.plan_adherence",
    "agent.mcp_use",
    "agent.mcp_task_completion",
]


def session(*steps: Any) -> Session:
    return Session(id="s", traces=(Trace(id="t", steps=tuple(steps)),))


def traj(
    names: tuple[str, ...] = ("dex_quote", "dex_swap"),
    *,
    tools: tuple = TOOLS,
    plan: str | None = PLAN,
    args: dict | None = None,
) -> Session:
    steps: list[Any] = [
        AgentStep(id="a", user_prompt=TASK, response="Swapped.", available_tools=tools)
    ]
    if plan:
        steps.append(LlmStep(id="p", parent_id="a", response=plan))
    for i, n in enumerate(names):
        steps.append(
            ToolStep(
                id=f"t{i}",
                parent_id="a",
                call=ToolCall(name=n, arguments=args or {"amount": 1}, id=f"c{i}"),
                result=ToolResult(content=f"ok-{i}", call_id=f"c{i}"),
            )
        )
    return session(*steps)


def reply(score: str = "Good", **extra: Any) -> dict[str, Any]:
    return {"reasoning": f"because {score}", "score": score, **extra}


async def run(
    metric_id: str, case: EvalCase, script: Any = None, params: dict[str, Any] | None = None
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script if script is not None else reply())
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=cls.spec.method(None),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(case, ctx), judge


def mcp_session() -> Session:
    return traj(
        ("mcp__dex__quote", "dex.swap"),
        tools=({"name": "mcp__dex__quote", "description": "Quote"}, {"name": "local_calc"}),
    )


def full_case(**kw: Any) -> EvalCase:
    kw.setdefault("trajectory", mcp_session())
    return EvalCase(id="c", input=TASK, output="Swapped 1 BNB.", **kw)


# ------------------------------------------------------------------- label mapping


@pytest.mark.parametrize("metric_id", METRICS)
@pytest.mark.parametrize(("label", "score"), list(FIVE_TIER.items()))
async def test_five_tier_label_mapping(metric_id: str, label: str, score: float) -> None:
    res, judge = await run(metric_id, full_case(), reply(label))
    assert res.status == "scored" and res.score == score
    assert res.details["label"] == label and res.reason == f"because {label}"
    assert res.threshold == 0.5 and res.passed is (score >= 0.5)
    assert len(judge.requests) == 1


@pytest.mark.parametrize("metric_id", METRICS)
def test_spec_and_sources(metric_id: str) -> None:
    cls, _ = registry.resolve(metric_id)
    assert cls.spec.default_threshold == 0.5
    assert cls.templates["v1"].source.startswith("bnbagent-eval:new (cf. ")
    assert cls.spec.validate_params({"agent_id": "a"}) == []  # renders the trajectory


async def test_extra_fields_kept_in_details() -> None:
    res, _ = await run(
        "agent.plan_quality", full_case(), reply("Average", plan_found=True, plan_issues=["x"])
    )
    assert res.details["plan_found"] is True and res.details["plan_issues"] == ["x"]


# ------------------------------------------------------------ prompt content


async def test_task_completion_prompt_and_modes() -> None:
    c = EvalCase(id="c", input=TASK, trajectory=traj())
    res, judge = await run("agent.task_completion", c)
    user = judge.requests[0].user
    assert res.details["mode"] == "simple"
    assert "# User Input (Task):" in user and TASK in user
    assert "Action: dex_swap(" in user and "Tool result: ok-1" in user
    assert "# Final Output:" not in user
    res, judge = await run("agent.task_completion", full_case(trajectory=traj()))
    assert res.details["mode"] == "with_output" and "# Final Output:" in judge.requests[0].user


async def test_task_completion_requires_input_and_trajectory() -> None:
    res, judge = await run("agent.task_completion", EvalCase(id="c", trajectory=traj()))
    assert res.status == "skipped" and res.error.code == "missing_input" and not judge.requests
    res, _ = await run("agent.task_completion", EvalCase(id="c", input=TASK))
    assert res.status == "skipped"


@pytest.mark.parametrize(
    ("kw", "mode"),
    [
        ({"trajectory": traj(tools=())}, "simple"),
        ({"trajectory": traj()}, "with_tools"),
        ({"trajectory": traj(), "input": TASK}, "with_input_and_tools"),
        ({"trajectory": traj(tools=()), "expected": ["dex_swap"]}, "with_expected"),
        (
            {"trajectory": traj(), "input": TASK, "expected": [{"name": "dex_swap"}]},
            "with_input_and_tools_and_expected",
        ),
    ],
)
async def test_tool_selection_mode_selection(kw: dict, mode: str) -> None:
    res, judge = await run("agent.tool_selection", EvalCase(id="c", **kw))
    assert res.details["mode"] == mode and judge.requests[0].metadata["mode"] == mode


async def test_tool_selection_renders_tools_once_and_reference() -> None:
    c = EvalCase(
        id="c",
        trajectory=traj(),
        expected=[{"name": "dex_quote", "arguments": {"amount": 1}}, {"name": "dex_swap"}],
    )
    _, judge = await run("agent.tool_selection", c)
    user = judge.requests[0].user
    assert "# Available Tools:" in user and "- dex_quote: Quote a token swap" in user
    assert user.count("Execute a token swap") == 1  # not repeated inside the trajectory
    assert "# Available tools" not in user
    assert "# Agent Trajectory:" in user and "Action: dex_quote(" in user
    assert '- dex_quote({"amount": 1})\n- dex_swap({})' in user


def test_expected_calls_text_falls_back_to_raw() -> None:
    assert expected_calls_text({"note": "free text"}) == '{"note": "free text"}'


async def test_explicit_input_mode() -> None:
    c = EvalCase(id="c", trajectory=traj(), input=TASK, expected=["dex_swap"])
    res, judge = await run("agent.tool_selection", c, params={"input_mode": "simple"})
    assert res.details["mode"] == "simple" and "# Available Tools:" not in judge.requests[0].user
    res, _ = await run("agent.plan_quality", c, params={"input_mode": "conversation"})
    assert res.status == "error"  # no conversation mode in this template


@pytest.mark.parametrize("metric_id", ["agent.plan_quality", "agent.plan_adherence"])
async def test_plan_section(metric_id: str) -> None:
    res, judge = await run(metric_id, EvalCase(id="c", trajectory=traj()))
    user = judge.requests[0].user
    assert res.details["mode"] == "with_plan"
    assert "# Agent Reasoning (LLM steps, may contain the plan):" in user
    assert f"[step p] {PLAN}" in user and "# Agent Trajectory:" in user
    res, judge = await run(metric_id, EvalCase(id="c", input=TASK, trajectory=traj(plan=None)))
    assert (
        res.details["mode"] == "with_input" and "# Agent Reasoning" not in judge.requests[0].user
    )


def test_plan_text_filters_agent() -> None:
    s = session(
        LlmStep(id="1", agent_id="planner", response="Plan A"),
        LlmStep(id="2", agent_id="worker", response="Doing B"),
        LlmStep(id="3", agent_id="worker"),
    )
    assert plan_text(s) == "[step 1] Plan A\n\n[step 2] Doing B"
    assert plan_text(s, "worker") == "[step 2] Doing B"
    assert plan_text(None) == ""


# ------------------------------------------------------------------ MCP detection


@pytest.mark.parametrize(
    ("name", "args", "target"),
    [
        ("mcp__dex__quote", {}, ("dex", "quote")),
        ("mcp__dex__pool__info", {}, ("dex", "pool__info")),
        ("dex.swap", {}, ("dex", "swap")),
        ("quote", {"mcp_server": "dex"}, ("dex", "quote")),
        ("local_calc", {}, None),
        ("mcp__dex", {}, None),
        (".swap", {}, None),
    ],
)
def test_mcp_target(name: str, args: dict, target: tuple | None) -> None:
    assert mcp_target(name, args) == target


def test_session_and_conversation_mcp_calls() -> None:
    s = session(
        AgentStep(id="a", available_tools=({"name": "price", "mcp_server": "oracle"},)),
        LlmStep(
            id="l",
            tool_calls=(
                ToolCall(name="mcp__dex__quote", id="x"),
                ToolCall(name="oracle.price", id="y"),
            ),
        ),
        ToolStep(
            id="t",
            call=ToolCall(name="mcp__dex__quote", id="x"),
            result=ToolResult(error="timeout"),
        ),
        ToolStep(id="u", call=ToolCall(name="local_calc")),
    )
    calls = session_mcp_calls(s)
    assert [(c.server, c.tool, c.error) for c in calls] == [
        ("oracle", "price", None),  # LLM call without a tool step
        ("dex", "quote", "timeout"),
    ]
    assert [t["name"] for t in mcp_tools(s)] == ["price"]
    conv = (
        Message(role="user", content=TASK),
        Message(role="assistant", tool_calls=(ToolCall(name="dex.quote", id="q"),)),
        Message(role="tool", content="1 BNB = 600 USDT", tool_call_id="q"),
    )
    (call,) = conversation_mcp_calls(conv)
    assert (call.server, call.tool, call.result) == ("dex", "quote", "1 BNB = 600 USDT")


@pytest.mark.parametrize("metric_id", ["agent.mcp_use", "agent.mcp_task_completion"])
async def test_mcp_trajectory_prompt(metric_id: str) -> None:
    res, judge = await run(metric_id, full_case())
    user = judge.requests[0].user
    assert res.details["mode"] == "with_input"
    assert res.details["mcp_calls"] == 2 and res.details["mcp_servers"] == ["dex"]
    tools_section = user.split("# Available MCP Tools:")[1].split("# MCP Tool Calls")[0]
    assert "- mcp__dex__quote: Quote" in tools_section and "local_calc" not in tools_section
    assert '1. server=dex tool=quote arguments={"amount": 1}\n   result: ok-0' in user
    assert "2. server=dex tool=swap" in user and "# Agent Trajectory:" in user


@pytest.mark.parametrize("metric_id", ["agent.mcp_use", "agent.mcp_task_completion"])
async def test_mcp_conversation_mode(metric_id: str) -> None:
    conv = (
        Message(role="user", content=TASK),
        Message(
            role="assistant",
            content="Quoting.",
            tool_calls=(ToolCall(name="quote", arguments={"mcp_server": "dex"}, id="q"),),
        ),
        Message(role="tool", content="600 USDT", tool_call_id="q"),
        Message(role="assistant", content="You get 600 USDT."),
    )
    res, judge = await run(metric_id, EvalCase(id="c", conversation=conv))
    user = judge.requests[0].user
    assert res.status == "scored" and res.details["mode"] == "conversation"
    assert "# Conversation:" in user and 'Action: quote({"mcp_server": "dex"})' in user
    assert "Tool result: 600 USDT" in user and "(no MCP tools declared)" in user
    assert "1. server=dex tool=quote" in user
    # A trajectory without MCP calls falls back to the conversation's MCP calls.
    c = EvalCase(id="c", input=TASK, conversation=conv, trajectory=traj())
    res, _ = await run(metric_id, c)
    assert res.details["mode"] == "conversation_with_input"


@pytest.mark.parametrize("metric_id", ["agent.mcp_use", "agent.mcp_task_completion"])
async def test_mcp_skipped_without_mcp(metric_id: str) -> None:
    res, judge = await run(metric_id, EvalCase(id="c", trajectory=traj()))
    assert res.status == "skipped" and res.error.code == "missing_input" and not judge.requests
    conv = (Message(role="user", content=TASK), Message(role="assistant", content="Done."))
    res, _ = await run(metric_id, EvalCase(id="c", conversation=conv))
    assert res.status == "skipped" and "trajectory" in res.error.message


async def test_mcp_tools_without_calls_still_judged() -> None:
    s = traj(("local_calc",), tools=({"name": "mcp__dex__swap", "description": "Swap"},))
    res, judge = await run("agent.mcp_use", EvalCase(id="c", trajectory=s), reply("Poor"))
    assert res.score == 0.0 and res.details["mcp_calls"] == 0
    assert "(no MCP tool calls detected)" in judge.requests[0].user


# ------------------------------------------------------------- section helpers


def test_section_parts_cover_every_subset() -> None:
    parts = section_parts("simple", [("a", "A"), (None, "X"), ("b", "B")], "T")
    assert parts == {
        "simple": "X",
        "task_simple": "T",
        "with_a": "A\n\nX",
        "task_with_a": "T",
        "with_b": "X\n\nB",
        "task_with_b": "T",
        "with_a_and_b": "A\n\nX\n\nB",
        "task_with_a_and_b": "T",
    }
    assert section_mode("conversation", ["a"]) == "conversation_with_a"
    assert section_mode("conversation", []) == "conversation"
