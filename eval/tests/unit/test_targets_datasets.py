"""Targets, runs, datasets and the native trace format."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, RunOptions, Suite, TargetRef
from bnbagent_eval.adapters.traces import dump_session, load_session
from bnbagent_eval.engine import Runner
from bnbagent_eval.engine.dataset import load_dataset_ref
from bnbagent_eval.engine.targets import CallableTarget, HttpTarget, from_ref, register_target
from bnbagent_eval.exceptions import AdapterError, RequestError, SuiteError
from bnbagent_eval.hashing import keccak_hex
from bnbagent_eval.types import EvalCase

_calls: dict[str, int] = {}


def flaky(text):
    _calls[text] = _calls.get(text, 0) + 1
    if _calls[text] % 4 == 0:  # every 4th call for the same input fails
        raise RuntimeError("upstream 503")
    return {"output": f"echo: {text}", "app_metrics": {"input_tokens": 10, "output_tokens": 5}}


async def slow(text):
    await asyncio.sleep(1)
    return text


register_target("echo", lambda text: f"echo: {text}")
register_target("flaky", flaky)
register_target("slow", slow)


async def test_callable_target_variants() -> None:
    run = await CallableTarget(lambda t: t.upper()).invoke(EvalCase(id="1", input="hi"))
    assert run.output == "HI" and run.app_metrics.latency_ms >= 0
    full = await CallableTarget(
        lambda c: {"output": c.id, "trajectory": {"id": "s"}}, pass_case=True
    ).invoke(EvalCase(id="7"))
    assert full.output == "7" and full.trajectory.id == "s"
    timeout = await CallableTarget(slow, timeout_s=0.01).invoke(EvalCase(id="1", input="x"))
    assert timeout.output is None and timeout.app_metrics.error == "timeout"


def _transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_http_target_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": {"answer": body["prompt"][::-1]}, "app_metrics": {"cost_usd": 0.001}},
        )

    target = HttpTarget(
        "http://agent.local/run",
        input_field="prompt",
        output_path="data.answer",
        client=_transport(handler),
    )
    run = await target.invoke(EvalCase(id="1", input="abc"))
    assert run.output == "cba" and run.app_metrics.cost_usd == 0.001
    assert run.app_metrics.latency_ms is not None


async def test_http_target_errors_are_recorded() -> None:
    target = HttpTarget(
        "http://agent.local/run", client=_transport(lambda r: httpx.Response(500, text="boom"))
    )
    run = await target.invoke(EvalCase(id="1", input="abc"))
    assert run.output is None and "500" in run.app_metrics.error


async def test_http_target_streaming_text_and_sse() -> None:
    text_target = HttpTarget(
        "http://agent.local/stream",
        stream=True,
        client=_transport(lambda r: httpx.Response(200, content=b"hello world")),
    )
    run = await text_target.invoke(EvalCase(id="1", input="x"))
    assert run.output == "hello world" and run.app_metrics.ttft_ms is not None

    sse = b'data: {"delta": "Hel"}\n\ndata: {"delta": "lo"}\n\ndata: [DONE]\n\n'
    sse_target = HttpTarget(
        "http://agent.local/sse",
        stream=True,
        stream_format="sse",
        output_path="delta",
        client=_transport(lambda r: httpx.Response(200, content=sse)),
    )
    run = await sse_target.invoke(EvalCase(id="1", input="x"))
    assert run.output == "Hello" and run.app_metrics.ttft_ms <= run.app_metrics.latency_ms


def test_from_ref_validation() -> None:
    assert isinstance(from_ref(TargetRef(kind="callable", uri="echo")), CallableTarget)
    with pytest.raises(RequestError):
        from_ref(TargetRef(kind="http", uri="ftp://x"))
    with pytest.raises(RequestError, match="unknown http target config"):
        from_ref(TargetRef(kind="http", uri="http://x", config={"verify": False}))
    with pytest.raises(RequestError, match="not supported yet"):
        from_ref(TargetRef(kind="erc8004", uri="97:0xregistry:1"))


def _suite() -> Suite:
    return Suite(
        name="t",
        version="1",
        metrics=(
            MetricConfig(id="text.contains", params={}, threshold=1.0),
            MetricConfig(id="ops.consistency", method="exact"),
            MetricConfig(id="ops.reliability"),
            MetricConfig(id="ops.latency", value_max=10_000),
        ),
    )


async def test_runner_with_target_and_runs() -> None:
    _calls.clear()
    req = EvalRequest(
        suite=_suite(),
        target=TargetRef(kind="callable", uri="flaky"),
        cases=(
            EvalCase(id="1", input="a", expected="echo"),
            EvalCase(id="2", input="b", expected="echo"),
        ),
        options=RunOptions(runs=4, max_concurrency=1),
    )
    resp = await Runner().run(req)
    assert [len(c.runs) for c in resp.results] == [4, 4]
    by = {m.name: m for m in resp.results[0].metrics}
    assert by["ops.reliability"].score == 0.75  # every 4th call fails
    assert by["ops.consistency"].details["runs"] == 3  # failed runs have no output
    assert by["ops.consistency"].score == 1.0
    assert resp.results[0].runs[3].error.startswith("RuntimeError")
    assert all(r.output_hash for r in resp.results[0].runs[:3])
    assert by["ops.latency"].passed is True


async def test_runs_without_target_rejected() -> None:
    with pytest.raises(RequestError, match="requires a target"):
        await Runner().run(
            EvalRequest(suite=_suite(), cases=(EvalCase(id="1"),), options=RunOptions(runs=2))
        )


def test_dataset_ref_with_hash(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"id": "1", "output": "a"}\n{"id": "2", "output": "b"}\n')
    digest = keccak_hex(path.read_bytes())
    assert [c.id for c in load_dataset_ref(f"{path}#{digest}")] == ["1", "2"]
    with pytest.raises(RequestError, match="hash mismatch"):
        load_dataset_ref(f"{path}#0x{'0' * 64}")
    with pytest.raises(RequestError, match="only fetched by the eval service"):
        load_dataset_ref("https://example.com/cases.json")
    resp = EvalClient().evaluate_sync(
        suite=Suite(
            name="d",
            version="1",
            metrics=(MetricConfig(id="text.length", params={"max_words": 100}),),
        ),
        dataset_ref=f"file://{path}",
    )
    assert resp.aggregate.cases == 2


async def test_suite_ref_through_the_storage_port() -> None:
    from bnbagent_eval.engine import resolve_suite
    from bnbagent_eval.ports import ChainPorts
    from bnbagent_eval.testing.fakes import MemoryStorage

    suite = Suite(
        name="r", version="1", metrics=(MetricConfig(id="text.length", params={"max_words": 100}),)
    )
    storage = MemoryStorage()
    uri = await storage.upload(suite.to_dict(), "suite.json")
    suite_hash = resolve_suite(suite).suite_hash
    client = EvalClient(ports=ChainPorts(storage=storage))
    cases = (EvalCase(id="1", output="a"),)
    for ref in (uri, f"{uri}#{suite_hash}"):
        resp = await client.evaluate(suite_ref=ref, cases=cases)
        assert resp.suite_hash == suite_hash
    with pytest.raises(SuiteError, match="suite_ref pins"):
        await client.evaluate(suite_ref=f"{uri}#0x{'0' * 64}", cases=cases)
    with pytest.raises(RequestError, match="cannot download"):
        await client.evaluate(suite_ref="mem://missing", cases=cases)
    with pytest.raises(RequestError, match="either suite or suite_ref"):
        await client.evaluate(suite=suite, suite_ref=uri, cases=cases)
    with pytest.raises(RequestError, match="needs a storage port"):
        await EvalClient().evaluate(suite_ref=uri, cases=cases)
    with pytest.raises(RequestError, match="benchmark_ref is not supported"):
        await client.evaluate(suite=suite, benchmark_ref="ipfs://b#0x00")


def test_native_trace_roundtrip(tmp_path) -> None:
    data = {
        "id": "s1",
        "traces": [
            {
                "id": "t1",
                "steps": [
                    {
                        "kind": "tool",
                        "id": "1",
                        "call": {"name": "search", "arguments": {"q": "bnb"}},
                    },
                    {"kind": "tx", "id": "2", "chain_id": 97, "to": "0xabc", "value": 1},
                ],
            }
        ],
    }
    session = load_session(data)
    assert load_session(dump_session(session)) == session
    file = tmp_path / "trace.json"
    file.write_text(json.dumps(data))
    assert load_session(file) == session
    with pytest.raises(AdapterError):
        load_session({"id": "s", "traces": [{"id": "t", "steps": [{"kind": "nope", "id": "1"}]}]})
