"""UntrustedSuitePolicy as the runner hook, and the egress guard (no FastAPI needed)."""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("fastapi")  # server tests run with the [server] extra only

from bnbagent_eval.exceptions import PolicyError  # noqa: E402
from bnbagent_eval.metrics import registry  # noqa: E402
from bnbagent_eval.metrics.base import Kind, MethodSpec, MetricSpec  # noqa: E402
from bnbagent_eval.server.policy import GuardedTransport, UntrustedSuitePolicy  # noqa: E402
from bnbagent_eval.types import MetricConfig  # noqa: E402

from ._fakes import fake_resolver  # noqa: E402


def _spec(metric_id: str, method: MethodSpec) -> MetricSpec:
    return MetricSpec(
        id=metric_id, category=metric_id.split(".")[0], description="", methods=(method,)
    )


@pytest.mark.parametrize(
    ("metric_id", "method"),
    [
        ("code.exec", MethodSpec("rule", Kind.RULE)),
        ("custom.python", MethodSpec("rule", Kind.RULE)),
        ("custom.human_review", MethodSpec("rule", Kind.RULE)),
        ("agent.task_completion", MethodSpec("agentic", Kind.JUDGE)),
        ("text.exact_match", MethodSpec("rule", Kind.RULE, service_allowed=False)),
        ("plugin.metric", MethodSpec("rule", Kind.RULE)),  # not shipped with the package
    ],
)
def test_runner_hook_denies(metric_id: str, method: MethodSpec) -> None:
    policy = UntrustedSuitePolicy()
    with pytest.raises(PolicyError):
        policy(_spec(metric_id, method), method, MetricConfig(id=metric_id))


def test_runner_hook_allows_builtin() -> None:
    spec = registry.get_spec("text.exact_match")
    UntrustedSuitePolicy()(spec, spec.method(), MetricConfig(id=spec.id))


def test_plugin_class_denied(monkeypatch) -> None:
    cls, _ = registry.resolve("text.exact_match")
    monkeypatch.setattr(cls, "__module__", "thirdparty.metrics")
    spec = cls.spec
    with pytest.raises(PolicyError):
        UntrustedSuitePolicy()(spec, spec.method(), MetricConfig(id=spec.id))


async def test_check_url() -> None:
    policy = UntrustedSuitePolicy(resolver=fake_resolver)
    await policy.check_url("https://agent.example.com/x")
    for url in (
        "http://rebind.example.com/",  # one private address is enough to deny
        "http://unknown.example.com/",
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
        "gopher://agent.example.com/",
        "",
    ):
        with pytest.raises(PolicyError):
            await policy.check_url(url)


async def test_guarded_transport_blocks_each_hop() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.host == "agent.example.com":
            return httpx.Response(301, headers={"location": "http://evil.example.com/"})
        return httpx.Response(200)

    transport = GuardedTransport(httpx.MockTransport(handler), resolver=fake_resolver)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as http:
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await http.get("http://agent.example.com/")
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await http.get("http://192.168.1.1/")
    assert hits == ["http://agent.example.com/"]


def test_vendor_and_model_methods_need_operator_opt_in() -> None:
    from bnbagent_eval.metrics import registry
    from bnbagent_eval.server.policy import UntrustedSuitePolicy

    spec = registry.get_spec("safety.toxicity")
    model = spec.method("model")
    default = UntrustedSuitePolicy()
    assert not default.method_allowed(spec, model)
    assert all(m.kind.value in ("rule", "judge") for s in default.catalogue() for m in s.methods)
    opted = UntrustedSuitePolicy(allowed_kinds={"rule", "judge", "model"})
    assert opted.method_allowed(spec, model)
