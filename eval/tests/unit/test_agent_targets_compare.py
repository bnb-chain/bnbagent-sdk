"""A2A / MCP targets, evaluator comparison, trajectory rendering."""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, Suite, TargetRef
from bnbagent_eval.adapters.traces import render_trajectory, session_messages
from bnbagent_eval.engine import Runner, aggregate_evaluations, compare
from bnbagent_eval.engine.agent_targets import A2ATarget, McpTarget
from bnbagent_eval.engine.targets import from_ref
from bnbagent_eval.exceptions import RequestError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import (
    AgentStep,
    DecodedCall,
    DelegationStep,
    EvalCase,
    LlmStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    TxStep,
)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def rpc_result(request: httpx.Request, result) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


async def test_a2a_direct_message_and_data_parts() -> None:
    def handler(request):
        body = json.loads(request.content)
        assert body["method"] == "message/send"
        assert body["params"]["message"]["parts"] == [{"kind": "text", "text": "quote 1 BNB"}]
        return rpc_result(
            request,
            {"kind": "message", "role": "agent", "parts": [{"kind": "text", "text": "600 USDT"}]},
        )

    run = await A2ATarget("http://agent/a2a", client=client(handler)).invoke(
        EvalCase(id="1", input="quote 1 BNB")
    )
    assert run.output == "600 USDT" and run.app_metrics.error is None

    def data_handler(request):
        assert json.loads(request.content)["params"]["message"]["parts"][0]["kind"] == "data"
        return rpc_result(
            request, {"kind": "message", "parts": [{"kind": "data", "data": {"price": 600}}]}
        )

    run = await A2ATarget("http://agent/a2a", client=client(data_handler)).invoke(
        EvalCase(id="1", input={"pair": "BNB/USDT"})
    )
    assert run.output == {"price": 600}


async def test_a2a_task_polling_and_failures() -> None:
    states = iter(["working", "completed"])

    def handler(request):
        method = json.loads(request.content)["method"]
        if method == "message/send":
            return rpc_result(
                request, {"kind": "task", "id": "t1", "status": {"state": "submitted"}}
            )
        state = next(states)
        result = {"kind": "task", "id": "t1", "status": {"state": state}}
        if state == "completed":
            result["artifacts"] = [{"parts": [{"kind": "text", "text": "done"}]}]
        return rpc_result(request, result)

    target = A2ATarget("http://agent/a2a", poll_interval_s=0, client=client(handler))
    assert (await target.invoke(EvalCase(id="1", input="go"))).output == "done"

    failed = A2ATarget(
        "http://agent/a2a",
        client=client(
            lambda r: rpc_result(r, {"kind": "task", "id": "t", "status": {"state": "failed"}})
        ),
    )
    run = await failed.invoke(EvalCase(id="1", input="go"))
    assert run.output is None and "failed" in run.app_metrics.error

    def error_handler(request):
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -32601, "message": "nope"}}
        )

    run = await A2ATarget("http://agent/a2a", client=client(error_handler)).invoke(
        EvalCase(id="1", input="go")
    )
    assert "-32601" in run.app_metrics.error


async def test_a2a_agent_card_resolution() -> None:
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"name": "agent", "url": "http://agent/rpc"})
        assert str(request.url) == "http://agent/rpc"
        return rpc_result(request, {"kind": "message", "parts": [{"kind": "text", "text": "ok"}]})

    target = A2ATarget("http://agent/.well-known/agent-card.json", client=client(handler))
    assert (await target.invoke(EvalCase(id="1", input="hi"))).output == "ok"


def _mcp_handler(reply, *, sse: bool = False):
    seen = {"session": []}

    def handler(request):
        body = json.loads(request.content)
        seen["session"].append(request.headers.get("mcp-session-id"))
        if body.get("method") == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"Mcp-Session-Id": "sess-1"},
            )
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        assert body["params"] == {
            "name": "get_price",
            "arguments": {"symbol": "BNB", "query": "BNB price"},
        }
        payload = {"jsonrpc": "2.0", "id": 2, "result": reply}
        if sse:
            return httpx.Response(
                200,
                text=f"event: message\ndata: {json.dumps(payload)}\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=payload)

    return handler, seen


@pytest.mark.parametrize("sse", [False, True])
async def test_mcp_tool_call(sse) -> None:
    handler, seen = _mcp_handler({"content": [{"type": "text", "text": "612.5"}]}, sse=sse)
    target = McpTarget(
        "http://mcp/",
        tool="get_price",
        input_argument="query",
        arguments={"symbol": "BNB"},
        client=client(handler),
    )
    run = await target.invoke(EvalCase(id="1", input="BNB price"))
    assert run.output == "612.5"
    assert seen["session"] == [None, "sess-1", "sess-1"]
    step = run.trajectory.tool_steps()[0]
    assert step.call.name == "get_price" and step.result.content == "612.5"


async def test_mcp_is_error_is_recorded() -> None:
    handler, _ = _mcp_handler({"content": [{"type": "text", "text": "boom"}], "isError": True})
    target = McpTarget(
        "http://mcp/",
        tool="get_price",
        input_argument="query",
        arguments={"symbol": "BNB"},
        client=client(handler),
    )
    run = await target.invoke(EvalCase(id="1", input="BNB price"))
    assert run.output is None and "isError" in run.app_metrics.error
    assert run.trajectory.tool_steps()[0].result.error


def test_from_ref_a2a_mcp_validation() -> None:
    assert isinstance(from_ref(TargetRef(kind="a2a", uri="http://x/a2a")), A2ATarget)
    assert isinstance(
        from_ref(TargetRef(kind="mcp", uri="http://x/mcp", config={"tool": "t"})), McpTarget
    )
    with pytest.raises(RequestError):
        from_ref(TargetRef(kind="mcp", uri="http://x/mcp"))
    with pytest.raises(RequestError):
        from_ref(TargetRef(kind="a2a", uri="http://x", config={"bogus": 1}))


GOOD = {"reasoning": "r", "score": "Good"}
SUITE = Suite(
    name="c",
    version="1",
    metrics=(MetricConfig(id="text.exact_match"), MetricConfig(id="quality.relevance")),
)
CASES = (EvalCase(id="1", input="q", output="a", expected="a"),)


async def _run(judge_reply) -> object:
    return await Runner(judge=FakeJudge(judge_reply)).run(EvalRequest(suite=SUITE, cases=CASES))


async def test_compare_tolerances_and_comparability() -> None:
    a = await _run(GOOD)
    b = await _run({"reasoning": "r", "score": "Excellent"})  # judge 0.75 vs 1.0
    c = compare(a, b)
    assert c.comparable and c.verdict_agree
    by = {m.name: m for m in c.metrics}
    assert by["text.exact_match"].agree and by["text.exact_match"].tolerance == 0.0
    assert not by["quality.relevance"].agree  # |0.25| > 0.15
    assert compare(a, b, {"quality.relevance": 0.3}).agree
    assert EvalClient(judge=FakeJudge(GOOD)).compare(a, b, **{"quality.relevance": 0.3}).agree
    other_suite = dataclasses.replace(b, suite_hash="0xother")
    assert not compare(a, other_suite).comparable


async def test_aggregate_evaluations_median_and_quorum() -> None:
    evals = [await _run({"reasoning": "r", "score": s}) for s in ("Poor", "Good", "Excellent")]
    med = aggregate_evaluations(evals)
    assert med.score == pytest.approx(0.875)  # case scores 0.5 / 0.875 / 1.0
    assert med.votes == {"fail": 1, "pass": 2}
    assert aggregate_evaluations(evals, "quorum:2").status == "pass"
    assert aggregate_evaluations(evals, "quorum:3").status == "inconclusive"
    with pytest.raises(ValueError):
        aggregate_evaluations(evals, "quorum:9")
    with pytest.raises(ValueError):
        aggregate_evaluations([])


def test_render_trajectory_details() -> None:
    session = Session(
        id="s",
        traces=(
            Trace(
                id="t",
                steps=(
                    AgentStep(
                        id="a",
                        agent_id="planner",
                        user_prompt="Swap 1 BNB",
                        response="Swapped.",
                        available_tools=(
                            {"name": "quote", "description": "DEX quote"},
                            {"name": "quote", "description": "dup"},
                        ),
                    ),
                    LlmStep(
                        id="l",
                        parent_id="a",
                        agent_id="planner",
                        response="Quoting.",
                        tool_calls=(
                            ToolCall(name="quote", arguments={"a": 1}, id="c1"),
                            ToolCall(name="unrun", arguments={}, id="c9"),
                        ),
                    ),
                    ToolStep(
                        id="t1",
                        parent_id="a",
                        agent_id="planner",
                        call=ToolCall(name="quote", arguments={"a": 1}, id="c1"),
                        result=ToolResult(error="rate limited"),
                    ),
                    DelegationStep(id="d", from_agent="planner", to_agent="executor"),
                    TxStep(
                        id="x",
                        agent_id="executor",
                        chain_id=56,
                        to="0xR",
                        value=1,
                        status=0,
                        decoded=DecodedCall(function="swap", args={"amountIn": 1}),
                    ),
                ),
            ),
        ),
    )
    text = render_trajectory(session)
    assert text.count("- quote:") == 1 and "Action [planner]: unrun({})" in text
    assert text.count("Action [planner]: quote") == 1  # not duplicated by the LLM tool call
    assert "Tool error: rate limited" in text and "[Delegated: planner -> executor]" in text
    assert "call=swap" in text and "status=failed" in text
    assert text.rstrip().endswith("Assistant [planner]: Swapped.")
    only_exec = render_trajectory(session, agent_id="executor")
    assert "Swap 1 BNB" not in only_exec and "Transaction" in only_exec
    assert [m.role for m in session_messages(session)] == ["user", "assistant"]


async def test_erc8004_target_resolution() -> None:
    from bnbagent_eval.engine.agent_targets import A2ATarget as _A2A
    from bnbagent_eval.engine.targets import HttpTarget, resolve_agent_target
    from bnbagent_eval.ports import ChainPorts
    from bnbagent_eval.testing.fakes import FakeRegistry

    registry = FakeRegistry()
    registry.register(
        5,
        {
            "name": "a",
            "services": [
                {"name": "web", "endpoint": "https://agent.example.com/run"},
                {"name": "A2A", "endpoint": "https://agent.example.com/a2a"},
            ],
        },
    )
    uri = f"{registry.chain_id}:{registry.registry_address}:5"
    assert isinstance(
        await resolve_agent_target(TargetRef(kind="erc8004", uri=uri), registry), _A2A
    )
    web = await resolve_agent_target(
        TargetRef(kind="erc8004", uri="5", config={"service": "web"}), registry
    )
    assert isinstance(web, HttpTarget) and web.url.endswith("/run")
    for bad in (
        TargetRef(kind="erc8004", uri="56:0xother:5"),
        TargetRef(kind="erc8004", uri="404"),
        TargetRef(kind="erc8004", uri="5", config={"service": "nope"}),
    ):
        with pytest.raises(RequestError):
            await resolve_agent_target(bad, registry)
    with pytest.raises(RequestError, match="RegistryPort"):
        await resolve_agent_target(TargetRef(kind="erc8004", uri="5"), None)

    def handler(request):
        return rpc_result(request, {"kind": "message", "parts": [{"kind": "text", "text": "ok"}]})

    # End to end through EvalClient(ports=...): resolution happens inside the runner.
    import bnbagent_eval.engine.agent_targets as at

    original = at.A2ATarget.__init__

    def patched(self, url, **kw):
        original(self, url, **{**kw, "client": client(handler)})

    at.A2ATarget.__init__ = patched
    try:
        resp = await EvalClient(ports=ChainPorts(registry=registry)).evaluate(
            suite=Suite(
                name="t",
                version="1",
                metrics=(MetricConfig(id="text.length", params={"max_words": 100}),),
            ),
            target=TargetRef(kind="erc8004", uri=uri),
            cases=[EvalCase(id="1", input="hi")],
        )
    finally:
        at.A2ATarget.__init__ = original
    assert resp.results[0].metrics[0].score == 1.0 and resp.results[0].runs[0].output_hash


@pytest.mark.parametrize("name", ["web", "A2A", "MCP"])
async def test_erc8004_endpoints_go_through_the_egress_guard(name) -> None:
    # The endpoint comes from the agent's registration file, a URL the caller did not choose.
    from bnbagent_eval.ports import ChainPorts
    from bnbagent_eval.testing.fakes import FakeRegistry

    registry = FakeRegistry()
    registry.register(
        5, {"name": "a", "services": [{"name": name, "endpoint": "http://127.0.0.1:9/run"}]}
    )
    resp = await EvalClient(ports=ChainPorts(registry=registry)).evaluate(
        suite=Suite(
            name="t",
            version="1",
            metrics=(MetricConfig(id="text.length", params={"max_words": 100}),),
        ),
        target=TargetRef(kind="erc8004", uri="5", config={"tool": "run"} if name == "MCP" else {}),
        cases=[EvalCase(id="1", input="hi")],
    )
    run = resp.results[0].runs[0]
    assert run.output_hash is None and "blocked" in run.error
