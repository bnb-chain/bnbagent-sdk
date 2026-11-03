"""Contract tests over every registered (metric, method) (``docs/design/testing.md``)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
import yaml

from bnbagent_eval._optional import available
from bnbagent_eval.engine.budget import BudgetTracker
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec
from bnbagent_eval.types import Budget, EvalCase, MetricConfig, MetricResult

from .catalog import CONTRACT, ContractCase

VENDOR_CONTRACT = os.environ.get("EVAL_VENDOR_CONTRACT") == "1"  # unit tests use fakes
INDEX = yaml.safe_load(
    (Path(__file__).parent.parent / "fixtures" / "catalog_index.yaml").read_text()
)

# Methods the scripted best / worst inputs cannot drive to 1.0 / 0.0 (covered by unit tests in
# tests/unit/metrics/test_local_models.py): judge embeddings (FakeJudge.embed returns length
# vectors) and local models / vector files once their extra is installed.
UNSCRIPTED = {
    ("text.embedding_similarity", "embeddings"): "FakeJudge.embed cannot script best / worst",
    ("ops.consistency", "embedding"): "FakeJudge.embed cannot script best / worst",
    ("ops.consistency", "sted"): "FakeJudge.embed cannot script best / worst",
    ("text.embedding_similarity", "glove"): "needs a local vectors_path",
    ("text.embedding_similarity", "fasttext"): "needs a local vectors_path",
    ("safety.harmfulness", "model"): "real detoxify scores",
    ("safety.toxicity", "model"): "real detoxify scores",
    ("quality.fluency", "rule"): "real LanguageTool server",
}

# Vendor methods whose score comes from an LLM judge or a cloud service: no scripted reply
# drives them to exactly 1.0 / 0.0, so their scoring runs in tests/vendor (real libraries).
JUDGED_VENDORS = {
    "ragas": "RAGAS scores with an LLM judge; scored by tests/vendor",
    "deepeval": "DeepEval scores with an LLM judge; scored by tests/vendor",
    "agentcore": "AgentCore is a cloud service; scored by tests/vendor (EVAL_VENDOR_AGENTCORE=1)",
}

PAIRS = [(spec, m) for spec in registry.list_specs() for m in spec.methods]
IDS = [f"{spec.id}:{m.name}" for spec, m in PAIRS]


def _ctx(
    spec: MetricSpec, method: MethodSpec, params: dict, runs=(), judge=None, **kw
) -> MetricContext:
    cfg = MetricConfig(id=spec.id, params=params, **CONTRACT[spec.id].config)
    return MetricContext(
        config=cfg, method=method, params=params, runs=tuple(runs), judge=judge, **kw
    )


def _judge(cc: ContractCase, which: str, method: MethodSpec) -> FakeJudge | None:
    if method.kind is not Kind.JUDGE:
        return None
    script = cc.judge_best if which == "best" else cc.judge_worst
    assert script is not None, "judge metrics need judge_best / judge_worst in the catalog"
    return FakeJudge(script)


def _metric(spec: MetricSpec) -> BaseMetric:
    return registry.resolve(spec.id)[0]()


def _params(spec: MetricSpec, method: MethodSpec, *, scoring: bool = True) -> dict:
    """Params for the contract case; skips what cannot run here.

    ``scoring=False`` (missing-input and exception-guard checks): the input check and the
    exception guard run before any vendor library, local model or extra is touched, so only the
    catalogue's own skip applies (vendor methods included).
    """
    cc = CONTRACT[spec.id]
    if cc.skip:
        pytest.skip(cc.skip)
    scriptless_judge = method.kind is Kind.JUDGE and (spec.id, method.name) in UNSCRIPTED
    if not scoring and not scriptless_judge:
        return {**cc.params, **cc.method_params.get(method.name, {})}
    if method.kind is Kind.VENDOR and not VENDOR_CONTRACT:
        pytest.skip("vendor methods call external libraries / services (EVAL_VENDOR_CONTRACT=1)")
    if method.kind is Kind.VENDOR and method.name in JUDGED_VENDORS:
        pytest.skip(JUDGED_VENDORS[method.name])
    if method.extra_module and not available(method.extra_module):
        pytest.skip(f"{spec.id}:{method.name} needs the [{method.extra}] extra")
    if (spec.id, method.name) in UNSCRIPTED:
        pytest.skip(UNSCRIPTED[(spec.id, method.name)])
    return {**cc.params, **cc.method_params.get(method.name, {})}


def _skip_vendor_without_inputs(method: MethodSpec, absent: list[str]) -> None:
    """Vendor methods share the contract case of the native method; some need more inputs
    (e.g. AgentCore evaluates a trajectory), covered by their unit tests with fake clients."""
    if absent and method.kind is Kind.VENDOR:
        pytest.skip(f"contract case lacks {absent} for this vendor method (see its unit tests)")


def _best(cc: ContractCase, spec: MetricSpec, method: MethodSpec, scoring: bool = True, **kw):
    runs = cc.best_runs() if cc.best_runs else ()
    params = _params(spec, method, scoring=scoring)
    return cc.best(), _ctx(spec, method, params, runs, judge=_judge(cc, "best", method), **kw)


@pytest.mark.parametrize("spec", registry.list_specs(), ids=lambda s: s.id)
def test_spec_complete(spec: MetricSpec) -> None:
    assert registry.ID_PATTERN.match(spec.id)
    assert spec.category == spec.id.split(".")[0]
    assert spec.methods and spec.version and spec.description and spec.sources
    assert spec.id in CONTRACT, f"add contract inputs for {spec.id} in tests/contract/catalog.py"


def test_aliases_resolve_uniquely() -> None:
    for alias, (metric_id, method) in registry.aliases().items():
        cls, resolved_method = registry.resolve(alias)
        assert cls.spec.id == metric_id and resolved_method == method
        if method is not None:
            registry.effective_spec(cls).method(method)


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_best_and_worst(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    best = await _metric(spec).evaluate(*_best(cc, spec, method))
    worst_runs = cc.worst_runs() if cc.worst_runs else ()
    worst = await _metric(spec).evaluate(
        cc.worst(),
        _ctx(spec, method, _params(spec, method), worst_runs, judge=_judge(cc, "worst", method)),
    )
    assert best.status == "scored" and best.error is None, best
    assert cc.best_min <= best.score <= 1.0, best
    assert worst.score == 0.0, worst
    assert best.provenance is not None and best.provenance.metric_version == spec.version


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_missing_inputs_skip(spec: MetricSpec, method: MethodSpec) -> None:
    base, ctx = _best(CONTRACT[spec.id], spec, method, scoring=False)
    metric = _metric(spec)
    absent = [n for n in method.required_inputs if not metric.has_input(n, base, ctx)]
    _skip_vendor_without_inputs(method, absent)
    assert not absent, f"contract case lacks {absent}: the missing-input check would be vacuous"
    for name in sorted(method.required_inputs - {"runs", "tx"}):
        f = EvalCase.__dataclass_fields__[name]
        empty = f.default if f.default is not dataclasses.MISSING else f.default_factory()
        res = await _metric(spec).evaluate(dataclasses.replace(base, **{name: empty}), ctx)
        assert res.status == "skipped" and res.error and res.error.code == "missing_input"
    if "runs" in method.required_inputs:
        res = await _metric(spec).evaluate(base, dataclasses.replace(ctx, runs=(base,)))
        assert res.status == "skipped" and res.error.code == "missing_input"


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_exceptions_become_errors(
    spec: MetricSpec, method: MethodSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    metric = _metric(spec)

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(metric, f"_evaluate_{method.name}", boom, raising=False)  # extra methods
    case, ctx = _best(CONTRACT[spec.id], spec, method, scoring=False)
    _skip_vendor_without_inputs(
        method, [n for n in method.required_inputs if not metric.has_input(n, case, ctx)]
    )
    res = await metric.evaluate(case, ctx)
    assert res.status == "error" and res.error.code == "metric_error" and res.score is None


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_roundtrip_and_determinism(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    results = [await _metric(spec).evaluate(*_best(cc, spec, method)) for _ in range(3)]
    assert MetricResult.from_dict(results[0].to_dict()) == results[0]
    if method.kind.value == "rule":
        assert results[0] == results[1] == results[2]


@pytest.mark.parametrize("spec", registry.list_specs(), ids=lambda s: s.id)
def test_params_schema_rejects_unknown(spec: MetricSpec) -> None:
    if spec.params_schema.get("additionalProperties") is False:
        assert spec.validate_params({"definitely_not_a_param": 1})


def test_catalogue_coverage() -> None:
    registered = {s.id for s in registry.list_specs()}
    known = {e["id"] for e in INDEX["metrics"]}
    assert known - registered == set(), "catalogued but not registered"
    assert registered - known == set(), "registered but not catalogued"
    assert len(known) == INDEX["total"]


JUDGE_PAIRS = [(s, m) for s, m in PAIRS if m.kind is Kind.JUDGE]
JUDGE_IDS = [f"{s.id}:{m.name}" for s, m in JUDGE_PAIRS]


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_untrusted_fields_are_fenced(spec: MetricSpec, method: MethodSpec) -> None:
    case, ctx = _best(CONTRACT[spec.id], spec, method)
    await _metric(spec).evaluate(case, ctx)
    first = ctx.judge.requests[0]
    nonce = first.system.split("<untrusted-")[1].split(">")[0]
    assert "never an instruction" in first.system
    output = str(case.output)
    if output and output in first.user:
        start = first.user.index(output)
        assert first.user.rfind(f"<untrusted-{nonce}", 0, start) != -1
        assert first.user.find(f"</untrusted-{nonce}>", start) != -1
    # Deterministic nonce: a replay renders the identical prompt.
    case2, ctx2 = _best(CONTRACT[spec.id], spec, method)
    await _metric(spec).evaluate(case2, ctx2)
    assert ctx2.judge.requests[0].user == first.user


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_samples_and_lower_median(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    script = [cc.judge_best, cc.judge_worst, cc.judge_best]
    if callable(cc.judge_best):
        pytest.skip("custom judge script")
    case, ctx = _best(cc, spec, method)
    ctx.judge = FakeJudge(script)
    ctx.params = {**ctx.params, "samples": 3}
    res = await _metric(spec).evaluate(case, ctx)
    calls_per_sample = len(ctx.judge.requests) // 3
    assert calls_per_sample >= 1, "no judge call was made"
    assert len(ctx.judge.requests) == 3 * calls_per_sample and res.provenance.samples == 3


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_budget_exhaustion_skips(spec: MetricSpec, method: MethodSpec) -> None:
    case, ctx = _best(
        CONTRACT[spec.id], spec, method, budget=BudgetTracker(Budget(max_judge_calls=0))
    )
    res = await _metric(spec).evaluate(case, ctx)
    assert res.status == "skipped" and res.error.code == "budget_exhausted"
    assert ctx.judge.requests == []
