from __future__ import annotations

import asyncio

import pytest

from bnbagent_eval import EvalClient, EvalRequest, MetricConfig, Suite, register_python_metric
from bnbagent_eval.engine import Runner, verify_hashes
from bnbagent_eval.exceptions import RequestError, SuiteError
from bnbagent_eval.types import EvalCase, JudgeConfig, Privacy, RunOptions

register_python_metric("score_from_meta", lambda case: case.metadata["score"])
register_python_metric("explode", lambda case: 1 / 0)
register_python_metric(
    "with_content",
    lambda case: {
        "score": 1.0,
        "reason": "token=sk-abcdefghijklmnopqrst",  # fake; secret-scan: allow
        "details": {"output_text": case.output, "n": 1},
    },
)

_active = 0
_peak = 0


async def _slow(case):
    global _active, _peak
    _active += 1
    _peak = max(_peak, _active)
    await asyncio.sleep(0.01 * (int(case.id) % 3))
    _active -= 1
    return 1.0


register_python_metric("slow", _slow)


def suite(*fns: str, **kw) -> Suite:
    return Suite(
        name="t",
        version="1",
        metrics=tuple(
            MetricConfig(id="custom.python", name=fn, params={"fn": fn}, threshold=0.5)
            for fn in fns
        ),
        **kw,
    )


async def test_end_to_end_and_hashes() -> None:
    cases = [EvalCase(id=str(i), metadata={"score": s}) for i, s in enumerate([1.0, 0.8, 0.2])]
    resp = await EvalClient().evaluate(suite=suite("score_from_meta"), cases=cases)
    assert [c.case_id for c in resp.results] == ["0", "1", "2"]
    assert resp.aggregate.score == pytest.approx(2.0 / 3.0)
    assert resp.verdict.status == "fail"  # default min_pass_rate = 1.0
    assert verify_hashes(resp)


async def test_result_hash_deterministic_response_hash_not() -> None:
    clock = iter(range(100)).__next__
    cases = [EvalCase(id="1", metadata={"score": 1.0})]
    a = await Runner(clock=lambda: float(clock())).run(
        EvalRequest(suite=suite("score_from_meta"), cases=tuple(cases))
    )
    b = await Runner(clock=lambda: float(clock())).run(
        EvalRequest(suite=suite("score_from_meta"), cases=tuple(cases))
    )
    assert a.result_hash == b.result_hash
    assert a.response_hash != b.response_hash


async def test_concurrency_limit_and_order() -> None:
    cases = tuple(EvalCase(id=str(i)) for i in range(12))
    req = EvalRequest(suite=suite("slow"), cases=cases, options=RunOptions(max_concurrency=3))
    resp = await Runner().run(req)
    assert _peak == 3  # the limit is reached (concurrency is real) and never exceeded
    assert [c.case_id for c in resp.results] == [str(i) for i in range(12)]


async def test_metric_failure_is_isolated() -> None:
    req = EvalRequest(
        suite=suite("explode", "score_from_meta"),
        cases=(EvalCase(id="1", metadata={"score": 1.0}),),
    )
    resp = await Runner().run(req)
    by_name = {m.name: m for m in resp.results[0].metrics}
    assert by_name["explode"].status == "error" and by_name["explode"].error.code == "metric_error"
    assert by_name["score_from_meta"].score == 1.0
    assert resp.verdict.status == "inconclusive"  # error rate 0.5 > max_error_rate 0.0


async def test_privacy_hash_only_and_redaction() -> None:
    req = EvalRequest(
        suite=suite("with_content"), cases=(EvalCase(id="1", output="secret output"),)
    )
    resp = await Runner().run(req)
    m = resp.results[0].metrics[0]
    assert "output_text" not in m.details and m.details["n"] == 1
    req_full = EvalRequest(
        suite=suite("with_content"),
        cases=req.cases,
        options=RunOptions(privacy=Privacy(report_contents="full", redact=(r"sk-\w+",))),
    )
    m_full = (await Runner().run(req_full)).results[0].metrics[0]
    assert m_full.details["output_text"] == "secret output"
    assert "sk-" not in m_full.reason


@pytest.mark.parametrize(
    ("request_kwargs", "error"),
    [
        ({}, RequestError),
        ({"suite": "s"}, RequestError),
        ({"suite": "s", "cases": (EvalCase(id="1"), EvalCase(id="1"))}, RequestError),
    ],
)
async def test_request_errors(request_kwargs, error) -> None:
    if request_kwargs.get("suite") == "s":
        request_kwargs = {**request_kwargs, "suite": suite("score_from_meta")}
    with pytest.raises(error):
        await Runner().run(EvalRequest(**request_kwargs))


async def test_judge_pin_conflicts_with_override() -> None:
    pinned = suite("score_from_meta", judge=JudgeConfig(provider="fake", model="a"))
    req = EvalRequest(
        suite=pinned,
        cases=(EvalCase(id="1", metadata={"score": 1.0}),),
        options=RunOptions(judge_override=JudgeConfig(provider="fake", model="b")),
    )
    with pytest.raises(SuiteError, match="pins a judge"):
        await Runner().run(req)


async def test_client_rejects_unknown_fields_and_accepts_dicts() -> None:
    client = EvalClient()
    with pytest.raises(TypeError, match="unknown"):
        await client.evaluate(suite=suite("score_from_meta"), casez=[])
    resp = await client.evaluate(
        suite={
            "name": "d",
            "version": "1",
            "metrics": [{"id": "custom.python", "params": {"fn": "score_from_meta"}}],
        },
        cases=[{"id": "1", "metadata": {"score": 1.0}}],
        options={"max_concurrency": 2},
    )
    assert resp.aggregate.score == 1.0


def test_evaluate_sync_inside_and_outside_loop() -> None:
    kwargs = {
        "suite": suite("score_from_meta"),
        "cases": [EvalCase(id="1", metadata={"score": 1.0})],
    }
    assert EvalClient().evaluate_sync(**kwargs).aggregate.score == 1.0

    async def inner():
        return EvalClient().evaluate_sync(**kwargs)

    assert asyncio.run(inner()).aggregate.score == 1.0


def test_remote_max_payment_is_atomic_units() -> None:
    class Payer:
        def request(self, url, *, max_payment, method="GET", body=None):
            raise AssertionError("not called")

    client = EvalClient.remote("http://eval.test", payer=Payer(), max_payment=100_000)
    assert client.backend.max_payment == 100_000
    for bad in ("0.10", 0.1, True):
        with pytest.raises(TypeError, match="atomic units"):
            EvalClient.remote("http://eval.test", payer=Payer(), max_payment=bad)


def test_client_accepts_a_judge_config() -> None:
    """``EvalClient(judge=JudgeConfig(...))`` builds a judge from the config."""
    cfg = JudgeConfig(provider="openai", model="m", base_url="http://127.0.0.1:9/v1")
    client = EvalClient(judge=cfg)
    assert client.backend.runner.judge.config == cfg


def test_remote_expected_evaluator_requires_an_attestation() -> None:
    """With ``expected_evaluator`` set, an unattested response is rejected."""
    from bnbagent_eval.exceptions import AttestationError

    request = EvalRequest(
        suite=suite("score_from_meta"), cases=(EvalCase(id="1", metadata={"score": 1.0}),)
    )
    response = EvalClient().evaluate_sync(request)
    backend = EvalClient.remote("http://eval.test", expected_evaluator="0x" + "11" * 20).backend
    with pytest.raises(AttestationError, match="not attested"):
        backend.verify(request, response)
    EvalClient.remote("http://eval.test").backend.verify(request, response)  # no expectation
