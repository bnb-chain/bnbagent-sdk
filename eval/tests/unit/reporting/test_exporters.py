from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager
from typing import Any

import pytest

from bnbagent_eval.exceptions import MissingExtraError
from bnbagent_eval.reporting.exporters import EXPORTERS, export

from ._data import SECRET, response

# --------------------------------------------------------------------------------- fakes


class FakeSpan:
    def __init__(self, name: str, parent: FakeSpan | None, attributes: dict, start: Any) -> None:
        self.name, self.parent, self.attributes, self.start = name, parent, attributes, start
        self.end_time: Any = None
        self.status: Any = None
        self.ended = False

    def set_status(self, status: Any) -> None:
        self.status = status

    def end(self, end_time: Any = None) -> None:
        self.ended, self.end_time = True, end_time


class FakeTracer:
    def __init__(self, spans: list[FakeSpan]) -> None:
        self.spans = spans

    def start_span(self, name, context=None, start_time=None, attributes=None):  # noqa: ANN001
        span = FakeSpan(name, context, dict(attributes or {}), start_time)
        self.spans.append(span)
        return span


class FakeProvider:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []
        self.names: list[str] = []

    def get_tracer(self, name: str) -> FakeTracer:
        self.names.append(name)
        return FakeTracer(self.spans)


def _fake_otel(monkeypatch: pytest.MonkeyPatch, global_provider: FakeProvider) -> None:
    trace = types.ModuleType("opentelemetry.trace")
    trace.get_tracer = global_provider.get_tracer  # type: ignore[attr-defined]
    trace.set_span_in_context = lambda span: span  # type: ignore[attr-defined]
    trace.Status = lambda code, desc=None: (code, desc)  # type: ignore[attr-defined]
    trace.StatusCode = types.SimpleNamespace(ERROR="ERROR", OK="OK")  # type: ignore[attr-defined]
    pkg = types.ModuleType("opentelemetry")
    pkg.trace = trace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", pkg)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace)


class _Child:
    def __init__(self) -> None:
        self.value = 0.0

    def set(self, v: float) -> None:
        self.value = v

    def inc(self, v: float = 1.0) -> None:
        self.value += v


class _Metric:
    kind = ""

    def __init__(self, name, doc, labelnames=(), registry=None) -> None:  # noqa: ANN001
        self.name, self.labelnames, self.children = name, tuple(labelnames), {}
        registry.register(self)

    def labels(self, **kw: str) -> _Child:
        assert set(kw) == set(self.labelnames)
        return self.children.setdefault(tuple(sorted(kw.items())), _Child())


class FakeRegistry:
    def __init__(self) -> None:
        self.metrics: dict[str, _Metric] = {}

    def register(self, m: _Metric) -> None:
        if m.name in self.metrics:
            raise ValueError(f"duplicate {m.name}")
        self.metrics[m.name] = m

    def value(self, name: str, **labels: str) -> float:
        return self.metrics[name].children[tuple(sorted(labels.items()))].value


def _fake_prom(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    pushes: list[dict] = []
    mod = types.ModuleType("prometheus_client")
    mod.Gauge = type("Gauge", (_Metric,), {"kind": "gauge"})  # type: ignore[attr-defined]
    mod.Counter = type("Counter", (_Metric,), {"kind": "counter"})  # type: ignore[attr-defined]
    mod.CollectorRegistry = FakeRegistry  # type: ignore[attr-defined]
    mod.push_to_gateway = lambda url, **kw: pushes.append({"url": url, **kw})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "prometheus_client", mod)
    return pushes


class FakeMlflow(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("mlflow")
        self.calls: list[tuple[str, Any]] = []
        self._active: Any = None

    def _run(self, run_id: str) -> Any:
        return types.SimpleNamespace(info=types.SimpleNamespace(run_id=run_id))

    def set_tracking_uri(self, uri: str) -> None:
        self.calls.append(("set_tracking_uri", uri))

    def set_experiment(self, name: str) -> None:
        self.calls.append(("set_experiment", name))

    def active_run(self) -> Any:
        return self._active

    @contextmanager
    def start_run(self, run_id=None, run_name=None, nested=False):  # noqa: ANN001
        self.calls.append(("start_run", {"run_id": run_id, "run_name": run_name}))
        yield self._run(run_id or "new-run")

    def log_params(self, p: dict) -> None:
        self.calls.append(("log_params", p))

    def log_metrics(self, m: dict) -> None:
        self.calls.append(("log_metrics", m))

    def log_text(self, text: str, artifact_file: str) -> None:
        self.calls.append(("log_text", (text, artifact_file)))

    def get(self, name: str) -> Any:
        return next(v for k, v in self.calls if k == name)


# --------------------------------------------------------------------------------- dispatch


def test_dispatch_and_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    assert EXPORTERS == ("otel", "prometheus", "mlflow")
    with pytest.raises(ValueError):
        export(response(), "statsd")
    monkeypatch.setitem(sys.modules, "mlflow", None)  # import -> ImportError
    with pytest.raises(MissingExtraError, match="exporters"):
        export(response(), "mlflow")


# --------------------------------------------------------------------------------- otel


def test_otel_span_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    glob = FakeProvider()
    _fake_otel(monkeypatch, glob)
    provider = FakeProvider()
    root = export(response(), "otel", tracer_provider=provider)
    assert not glob.spans and provider.names == ["bnbagent_eval"]
    names = [s.name for s in provider.spans]
    assert names == ["eval.run", "eval.case", "eval.metric", "eval.metric"] + [
        "eval.case",
        "eval.metric",
        "eval.metric",
    ]
    assert root is provider.spans[0] and all(s.ended for s in provider.spans)
    a = root.attributes
    assert a["eval.suite_hash"] == "0xsuite" and a["eval.result_hash"] == "0xresult"
    assert a["eval.verdict"] == "fail" and a["eval.cases"] == 2
    assert a["eval.metric.text.exact.avg_score"] == 1.0
    assert a["eval.metric.text.exact.threshold"] == 0.5
    assert None not in a.values() and root.status == ("ERROR", "verdict: fail")
    assert root.start == 100_000_000_000 and root.end_time == 101_500_000_000
    case, metric = provider.spans[1], provider.spans[2]
    assert case.parent is root and metric.parent is case
    assert case.attributes["eval.case_id"] == "a"
    assert case.attributes["eval.suite_hash"] == "0xsuite"
    assert metric.attributes["eval.metric_id"] == "text.exact"
    assert "eval.metric.reason" not in metric.attributes
    err = provider.spans[5]
    assert err.attributes["eval.metric.error_code"] == "missing_input"
    assert err.status == ("ERROR", "missing_input")
    assert all(SECRET not in str(s.attributes) for s in provider.spans)


def test_otel_global_provider_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    glob = FakeProvider()
    _fake_otel(monkeypatch, glob)
    export(response(), "otel", include_text=True, metric_spans=True)
    assert glob.spans[2].attributes["eval.metric.reason"] == SECRET
    glob.spans.clear()
    export(response(), "otel", metric_spans=False)
    assert [s.name for s in glob.spans] == ["eval.run", "eval.case", "eval.case"]


def test_otel_real_sdk() -> None:
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    export_mod = pytest.importorskip("opentelemetry.sdk.trace.export")
    mem = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    exporter = mem.InMemorySpanExporter()
    provider = sdk_trace.TracerProvider()
    provider.add_span_processor(export_mod.SimpleSpanProcessor(exporter))
    export(response(), "otel", tracer_provider=provider)
    spans = exporter.get_finished_spans()
    assert len(spans) == 7
    root = next(s for s in spans if s.name == "eval.run")
    assert root.attributes["eval.result_hash"] == "0xresult"
    assert all(s.context.trace_id == root.context.trace_id for s in spans)


# --------------------------------------------------------------------------------- prometheus


def test_prometheus_gauges_counters_push(monkeypatch: pytest.MonkeyPatch) -> None:
    pushes = _fake_prom(monkeypatch)
    reg = FakeRegistry()
    out = export(response(), "prometheus", registry=reg)
    assert out is reg and not pushes
    run = {"stage": "dev", "suite_hash": "0xsuite"}
    m = {**run, "metric_id": "text.exact"}
    assert reg.value("bnbagent_eval_score", **run) == 0.25
    assert reg.value("bnbagent_eval_verdict_pass", **run) == 0.0
    assert reg.value("bnbagent_eval_metric_avg_score", **m) == 1.0
    assert reg.value("bnbagent_eval_metric_threshold", **m) == 0.5
    assert reg.value("bnbagent_eval_metric_count", **m) == 2
    assert reg.value("bnbagent_eval_metric_errors", **m) == 1
    lat = {**run, "metric_id": "perf.latency"}
    assert reg.value("bnbagent_eval_metric_value_p95", **lat) == 120.0
    assert reg.value("bnbagent_eval_tokens", **run) == 50
    # Same registry again: collectors are reused, counters accumulate.
    export(response(), "prometheus", registry=reg, pushgateway="http://pg:9091")
    assert reg.value("bnbagent_eval_runs", **run) == 2
    assert reg.value("bnbagent_eval_metric_errors", **m) == 2
    assert pushes == [
        {"url": "http://pg:9091", "job": "bnbagent-eval", "registry": reg, "grouping_key": run}
    ]


def test_prometheus_fresh_registry_and_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_prom(monkeypatch)
    reg = export(response(), "prometheus", prefix="my-app.eval")
    assert isinstance(reg, FakeRegistry)
    assert "my_app_eval_score" in reg.metrics


def test_prometheus_real_registry() -> None:
    prom = pytest.importorskip("prometheus_client")
    reg = prom.CollectorRegistry()
    export(response(), "prometheus", registry=reg)
    run = {"stage": "dev", "suite_hash": "0xsuite"}
    assert reg.get_sample_value("bnbagent_eval_score", run) == 0.25
    assert (
        reg.get_sample_value(
            "bnbagent_eval_metric_errors_total", {**run, "metric_id": "text.exact"}
        )
        == 1
    )
    text = prom.generate_latest(reg).decode()
    assert SECRET not in text and "bnbagent_eval_metric_avg_score" in text
    assert prom.REGISTRY.get_sample_value("bnbagent_eval_score", run) is None


# --------------------------------------------------------------------------------- mlflow


def test_mlflow_new_run_in_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    run_id = export(response(), "mlflow", experiment="exp", run_name="r1", tracking_uri="file:x")
    assert run_id == "new-run"
    assert fake.get("set_tracking_uri") == "file:x" and fake.get("set_experiment") == "exp"
    assert fake.get("start_run") == {"run_id": None, "run_name": "r1"}
    p = fake.get("log_params")
    assert p["suite_hash"] == "0xsuite" and p["result_hash"] == "0xresult"
    assert p["verdict"] == "fail"
    m = fake.get("log_metrics")
    assert m["score"] == 0.25 and m["text.exact_avg_score"] == 1.0
    assert m["text.exact_errors"] == 1.0 and m["perf.latency_value_p50"] == 120.0
    text, name = fake.get("log_text")
    assert name == "bnbagent_eval/report.json"
    doc = json.loads(text)
    assert doc["result_hash"] == "0xresult" and SECRET not in text
    assert doc["results"][0]["metrics"][0]["details"] == {"n": 3}


def test_mlflow_existing_and_active_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    assert export(response(), "mlflow", run_id="abc", include_text=True) == "abc"
    assert fake.get("start_run") == {"run_id": "abc", "run_name": None}
    assert SECRET in fake.get("log_text")[0]
    fake.calls.clear()
    fake._active = fake._run("active-1")
    assert export(response(), "mlflow", artifact=False) == "active-1"
    assert [k for k, _ in fake.calls] == ["log_params", "log_metrics"]
