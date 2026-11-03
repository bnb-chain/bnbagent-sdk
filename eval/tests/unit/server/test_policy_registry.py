"""The untrusted-suite policy over the *real* registry
(not synthetic specs), and the service rejecting ``benchmark_ref``."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from bnbagent_eval.exceptions import PolicyError  # noqa: E402
from bnbagent_eval.metrics import registry  # noqa: E402
from bnbagent_eval.metrics.base import Kind  # noqa: E402
from bnbagent_eval.server.policy import UntrustedSuitePolicy  # noqa: E402
from bnbagent_eval.types import MetricConfig  # noqa: E402

from ._fakes import request_body  # noqa: E402

registry.load_builtins()
DENIED = sorted(
    (spec.id, m.name)
    for spec in registry.list_specs()
    for m in spec.methods
    if spec.id.startswith(("code.", "custom.python", "custom.human_review")) or m.name == "agentic"
)


def test_denied_set_is_not_empty() -> None:
    ids = {i for i, _ in DENIED}
    assert {"code.syntax_valid", "code.tests_pass", "custom.python"} <= ids
    assert any(m == "agentic" for _, m in DENIED)


@pytest.mark.parametrize(("metric_id", "method"), DENIED)
def test_policy_hook_denies_real_methods(metric_id: str, method: str) -> None:
    spec = registry.get_spec(metric_id)
    with pytest.raises(PolicyError):
        UntrustedSuitePolicy()(spec, spec.method(method), MetricConfig(id=metric_id))


@pytest.mark.parametrize(("metric_id", "method"), DENIED)
def test_service_denies_real_methods_over_http(make_app, metric_id: str, method: str) -> None:
    params = (
        {"rubrics": [{"id": "r1", "text": "The answer is short."}]}
        if metric_id == "custom.rubric"
        else {}
    )
    body = request_body(metrics=[{"id": metric_id, "method": method, "params": params}])
    r = make_app().post("/v1/evaluate", content=json.dumps(body))
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "policy_denied"


def test_every_service_allowed_method_passes_the_hook() -> None:
    policy = UntrustedSuitePolicy()
    for spec in registry.list_specs():
        if not spec.id.startswith(("text.", "format.", "quality.")):
            continue
        for m in spec.methods:
            if (
                m.service_allowed
                and m.kind in (Kind.RULE, Kind.JUDGE)
                and spec.id != "format.regex"
            ):
                policy(spec, m, MetricConfig(id=spec.id, method=m.name))


def test_service_rejects_benchmark_ref(make_app) -> None:
    body = {**request_body(), "benchmark_ref": "ipfs://bench#0x00"}
    r = make_app().post("/v1/evaluate", content=json.dumps(body))
    assert r.status_code == 422 and r.json()["error"] == "policy_denied"
    assert "bench" not in r.json().get("message", "").replace("benchmark_ref", "")
