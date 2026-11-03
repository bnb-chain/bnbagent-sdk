"""UntrustedSuitePolicy request limits and allow-list edges, for the coverage gate."""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("fastapi")  # server tests run with the [server] extra only

from bnbagent_eval.exceptions import PolicyError  # noqa: E402
from bnbagent_eval.server.policy import (  # noqa: E402
    GuardedTransport,
    LimitExceeded,
    PolicyLimits,
    UntrustedSuitePolicy,
)
from bnbagent_eval.types import (  # noqa: E402
    EvalCase,
    EvalRequest,
    JudgeConfig,
    MetricConfig,
    RunOptions,
    Suite,
    TargetRef,
)

from ._fakes import fake_resolver  # noqa: E402


def _request(*metrics: str, judge: JudgeConfig | None = None, **kw) -> EvalRequest:
    suite = Suite(
        name="s",
        version="1",
        metrics=tuple(MetricConfig(id=m) for m in metrics or ("text.exact_match",)),
        judge=judge,
    )
    return EvalRequest(suite=suite, cases=(EvalCase(id="1", output="a"),), **kw)


def test_allowed_ids_default_to_the_service_catalogue() -> None:
    ids = UntrustedSuitePolicy().allowed_ids
    assert "text.exact_match" in ids and "custom.python" not in ids
    assert UntrustedSuitePolicy(allowed_ids=["text.bleu"]).allowed_ids == {"text.bleu"}


def test_body_size_and_shape() -> None:
    policy = UntrustedSuitePolicy(limits=PolicyLimits(max_request_bytes=10, max_json_depth=3))
    with pytest.raises(LimitExceeded):
        policy.check_body({}, size=11)
    with pytest.raises(PolicyError, match="JSON object"):
        policy.check_body([1, 2])
    with pytest.raises(PolicyError, match="nesting"):
        policy.check_body({"a": {"b": {"c": {"d": 1}}}})
    policy.check_body({"a": [1, {"b": 2}]}, size=5)


async def test_allow_list_with_unknown_metric_id() -> None:
    policy = UntrustedSuitePolicy(allowed_ids=["text.exact_match"])
    await policy.check_request(_request("text.exact_match"))
    with pytest.raises(PolicyError, match="allow-list"):
        await policy.check_request(_request("plugin.unknown"))


async def test_judge_samples_cases_and_simulated_users() -> None:
    policy = UntrustedSuitePolicy(
        resolver=fake_resolver, limits=PolicyLimits(max_judge_samples=2, max_cases=1)
    )
    judge = JudgeConfig(provider="openai", model="m", samples=3)
    with pytest.raises(LimitExceeded, match="samples"):
        await policy.check_request(_request(judge=judge))
    with pytest.raises(LimitExceeded, match="samples"):
        await policy.check_request(_request(options=RunOptions(judge_override=judge)))
    target = TargetRef(
        kind="http", uri="https://agent.example.com/x", simulated_user={"persona": "p"}
    )
    with pytest.raises(PolicyError, match="simulated-user"):
        await policy.check_request(_request(target=target))
    policy.check_cases(1)
    with pytest.raises(LimitExceeded):
        policy.check_cases(2)


def test_per_metric_samples_are_capped_and_counted() -> None:
    from bnbagent_eval.engine.resolve import resolve_suite

    policy = UntrustedSuitePolicy(limits=PolicyLimits(max_judge_samples=5), judge_samples=2)
    over = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="quality.fluency", params={"samples": 15}),),
    )
    with pytest.raises(LimitExceeded, match=r"params.samples 15 > 5"):
        resolve_suite(over, policy=policy)
    as_float = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="quality.fluency", params={"samples": 10.0}),),
    )
    with pytest.raises(LimitExceeded, match=r"params.samples 10.0 > 5"):
        resolve_suite(as_float, policy=policy)
    suite = Suite(
        name="s",
        version="1",
        metrics=(
            MetricConfig(id="quality.fluency", params={"samples": 4}),
            MetricConfig(id="quality.coherence"),  # the server judge's samples (2)
            MetricConfig(id="text.exact_match"),  # not a judge metric
        ),
    )
    resolved = resolve_suite(suite, policy=policy)
    request = EvalRequest(suite=suite, cases=(EvalCase(id="1", output="a"),))
    assert policy.estimate_judge_calls(request, resolved.metrics, 3) == 3 * 4 + 3 * 2
    # bare method specs count with the judge's samples
    assert policy.estimate_judge_calls(request, [m.method for m in resolved.metrics], 3) == 12


def test_per_metric_samples_reach_the_quote(make_app) -> None:
    import json

    from ._fakes import request_body

    def quote(metrics):
        cases = [{"id": str(i), "output": "a"} for i in range(2)]
        body = json.dumps(request_body(metrics=metrics, cases=cases))
        return make_app().post("/v1/quote", content=body)

    q = quote([{"id": "quality.fluency", "params": {"samples": 3}}])
    assert q.status_code == 200, q.text
    assert q.json()["judge_calls"] == 6
    r = quote([{"id": "quality.fluency", "params": {"samples": 15}}])
    assert r.status_code == 413 and "samples" in r.json()["message"]


async def test_guarded_transport_blocks_non_http_schemes() -> None:
    transport = GuardedTransport(httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(httpx.ConnectError, match="scheme"):
        await transport.handle_async_request(httpx.Request("GET", "ftp://agent.example.com/"))
    await transport.aclose()
