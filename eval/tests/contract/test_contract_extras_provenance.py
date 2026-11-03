"""Contract checks beyond the per-metric suite in ``test_metric_contract.py``.

Score polarity, canonical-JSON round trips, judge-prompt fencing and per-sample nonces,
``MissingExtraError`` for every optional extra, unknown params rejected at resolution,
aggregation, the untrusted-suite policy and input-mode detection (``docs/design/testing.md``).
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from bnbagent_eval.engine import resolve as resolve_mod
from bnbagent_eval.engine.resolve import resolve_suite
from bnbagent_eval.exceptions import MissingExtraError, PolicyError, SuiteError
from bnbagent_eval.hashing import canonical_json
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import BaseMetric, Kind, MethodSpec, MetricSpec
from bnbagent_eval.metrics.judge_base import JudgeMetric
from bnbagent_eval.types import MetricConfig, MetricResult, Suite
from bnbagent_eval.types.case import Message

from .catalog import CONTRACT
from .test_metric_contract import (
    IDS,
    JUDGE_IDS,
    JUDGE_PAIRS,
    PAIRS,
    _best,
    _ctx,
    _judge,
    _metric,
    _params,
)

# ---------------------------------------------------------------------------- spec defaults


@pytest.mark.parametrize("spec", registry.list_specs(), ids=lambda s: s.id)
def test_default_threshold_and_method_names(spec: MetricSpec) -> None:
    assert spec.default_threshold is None or 0.0 <= spec.default_threshold <= 1.0
    names = spec.method_names
    assert len(names) == len(set(names)), f"duplicate method names in {spec.id}"
    declares_required = bool(spec.params_schema.get("required")) or any(
        alt.get("required") for alt in spec.params_schema.get("anyOf", ())
    )  # "required" directly, or "at least one of" via anyOf
    assert spec.validate_params({}) == [] or declares_required, (
        "empty params must validate unless the schema declares required params"
    )


# --------------------------------------------------------------------------------- polarity


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
def test_polarity_per_method(spec: MetricSpec, method: MethodSpec) -> None:
    metric = _metric(spec)
    lo, hi = method.raw_range
    if method.inverted:  # raw worst (high) -> 0, raw best (low) -> 1
        assert metric.normalize(hi, method) == 0.0
        assert metric.normalize(lo, method) == 1.0
    else:  # raw best (high) -> 1
        assert metric.normalize(hi, method) == 1.0
        assert metric.normalize(lo, method) == 0.0
    # Out-of-range raw values are clipped, never escape [0, 1].
    assert metric.normalize(hi + (hi - lo), method) in (0.0, 1.0)
    assert metric.normalize(lo - (hi - lo), method) in (0.0, 1.0)


# Only non-identity mappings have a raw value to keep (filtered here, not skipped).
_MAPPED = [
    (i, p)
    for i, p in zip(IDS, PAIRS, strict=True)
    if p[1].inverted or p[1].raw_range != (0.0, 1.0)
]


@pytest.mark.parametrize(("spec", "method"), [p for _, p in _MAPPED], ids=[i for i, _ in _MAPPED])
async def test_inverted_methods_keep_raw(spec: MetricSpec, method: MethodSpec) -> None:
    assert _MAPPED, "no inverted / rescaled method left: the filter is wrong"
    cc = CONTRACT[spec.id]
    res = await _metric(spec).evaluate(*_best(cc, spec, method))
    assert res.status == "scored" and "raw" in res.details, res
    # The raw value maps back to the reported score through the method's polarity.
    assert _metric(spec).normalize(float(res.details["raw"]), method) == pytest.approx(res.score)


# ------------------------------------------------------------ provenance and canonical JSON


@pytest.mark.parametrize(("spec", "method"), PAIRS, ids=IDS)
async def test_full_provenance_and_canonical_json(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    res = await _metric(spec).evaluate(*_best(cc, spec, method))
    prov = res.provenance
    assert prov.method == method.name and prov.metric_version == spec.version
    assert res.metric_id == spec.id and res.method == method.name
    if method.kind is Kind.JUDGE and isinstance(_metric(spec), JudgeMetric):
        assert prov.prompt_hash and prov.judge and prov.judge["model"] == "fake"
        assert prov.samples and prov.samples >= 1 and prov.judge_source
    # One from_dict normalizes types (int -> float for float fields, as the engine does on
    # input); after that the canonical JSON is a fixed point of to_dict / from_dict.
    once = MetricResult.from_dict(res.to_dict())
    twice = MetricResult.from_dict(once.to_dict())
    assert once == res
    assert canonical_json(twice.to_dict()) == canonical_json(once.to_dict())


# ---------------------------------------------------------------------------------- fencing

_FENCE = re.compile(r"<untrusted-([0-9a-f]{16}) field=\"[a-z_]+\">\n.*?\n</untrusted-\1>", re.S)


def _nonce(system: str) -> str:
    return system.split("<untrusted-")[1].split(">")[0]


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_no_untrusted_text_outside_fences(spec: MetricSpec, method: MethodSpec) -> None:
    case, ctx = _best(CONTRACT[spec.id], spec, method)
    await _metric(spec).evaluate(case, ctx)
    assert ctx.judge.requests, "a judge method must call the judge"
    for req in ctx.judge.requests:
        nonce = _nonce(req.system)
        assert f"<untrusted-{nonce}" in req.user, "no fenced field in the user prompt"
        outside = _FENCE.sub("", req.user)
        assert f"untrusted-{nonce}" not in outside, "unbalanced fence"
        for value in (case.output, case.input, case.expected):
            text = value if isinstance(value, str) else None
            if text and len(text) >= 8:
                assert text not in outside, f"{text!r} rendered outside the fence"


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_nonce_differs_across_samples(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    if callable(cc.judge_best):
        pytest.skip("custom judge script")
    case, ctx = _best(cc, spec, method)
    ctx.params = {**ctx.params, "samples": 2}
    await _metric(spec).evaluate(case, ctx)
    by_sample: dict[int, set[str]] = {}
    for r in ctx.judge.requests:
        by_sample.setdefault(int(r.metadata.get("sample_idx", 0)), set()).add(_nonce(r.system))
    if len(by_sample) < 2:
        pytest.skip("method does not draw per-sample prompts (single-call method)")
    assert by_sample[0].isdisjoint(by_sample[1])


# -------------------------------------------------------------------------- optional extras

EXTRA_PAIRS = [(s, m) for s, m in PAIRS if m.extra_module]


def _cfg(spec: MetricSpec, method: MethodSpec, **params) -> MetricConfig:
    cc = CONTRACT[spec.id]
    p = {**cc.params, **cc.method_params.get(method.name, {}), **params}
    return MetricConfig(id=spec.id, method=method.name, params=p, **cc.config)


def test_methods_with_extras_exist() -> None:
    assert len(EXTRA_PAIRS) >= 20, "the missing-extra check would be vacuous"


@pytest.mark.parametrize(
    ("spec", "method"), EXTRA_PAIRS, ids=[f"{s.id}:{m.name}" for s, m in EXTRA_PAIRS]
)
def test_missing_extra_raises_with_hint(
    spec: MetricSpec, method: MethodSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolve_mod, "available", lambda _m: False)
    suite = Suite(name="c11", version="1", metrics=(_cfg(spec, method),))
    with pytest.raises(MissingExtraError) as exc:
        resolve_suite(suite)
    assert exc.value.module == method.extra_module
    assert f"pip install 'bnbagent-eval[{method.extra or 'all'}]'" in str(exc.value)


def test_every_extra_is_installable() -> None:
    """Every method's extra is declared in pyproject.toml (one install command per extra)."""
    from pathlib import Path

    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = set(tomllib.loads(pyproject.read_text())["project"]["optional-dependencies"])
    for spec, method in EXTRA_PAIRS:
        if method.extra:
            assert method.extra in declared, (spec.id, method.name, method.extra)


# ---------------------------------------------------------------------------- params schema

STRICT = [s for s in registry.list_specs() if s.params_schema.get("additionalProperties") is False]


def test_most_specs_are_strict() -> None:
    # Without additionalProperties=False the unknown-param check is a no-op for that metric.
    assert len(STRICT) >= 0.8 * len(registry.list_specs()), [
        s.id for s in registry.list_specs() if s not in STRICT
    ]


@pytest.mark.parametrize("spec", STRICT, ids=lambda s: s.id)
def test_unknown_param_rejected_at_resolution(spec: MetricSpec) -> None:
    method = spec.methods[0]
    suite = Suite(name="c12", version="1", metrics=(_cfg(spec, method, not_a_param=1),))
    with pytest.raises(SuiteError, match="invalid params"):
        resolve_suite(suite)


_WRONG = {"integer": "x", "number": "x", "string": 12345, "boolean": "yes", "array": 1}


def _typed(spec: MetricSpec) -> list[tuple[str, str]]:
    props = spec.params_schema.get("properties", {})
    return [
        (k, v["type"])
        for k, v in props.items()
        if isinstance(v.get("type"), str) and v["type"] in _WRONG
    ]


@pytest.mark.parametrize(
    "spec", [s for s in registry.list_specs() if _typed(s)], ids=lambda s: s.id
)
def test_wrongly_typed_param_rejected(spec: MetricSpec) -> None:
    for name, typ in _typed(spec):
        assert spec.validate_params({name: _WRONG[typ]}), f"{spec.id}.{name} accepts a {typ} typo"


# ---------------------------------------------------------------------------- judge samples


async def _score(spec, method, script, samples):
    cc = CONTRACT[spec.id]
    case, ctx = _best(cc, spec, method)
    ctx.judge = FakeJudge(script)
    ctx.params = {**ctx.params, "samples": samples}
    res = await _metric(spec).evaluate(case, ctx)
    return res, len(ctx.judge.requests)


@pytest.mark.parametrize(("spec", "method"), JUDGE_PAIRS, ids=JUDGE_IDS)
async def test_exact_calls_and_lower_median(spec: MetricSpec, method: MethodSpec) -> None:
    cc = CONTRACT[spec.id]
    if callable(cc.judge_best):
        pytest.skip("custom judge script")
    one, per_sample = await _score(spec, method, [cc.judge_best], 1)
    assert per_sample >= 1
    for k in (2, 3):
        res, calls = await _score(spec, method, [cc.judge_best] * 8, k)
        assert calls == k * per_sample and res.provenance.samples == k
    if per_sample != 1:
        return  # multi-call methods consume the script per stage; call counts checked above
    worst, _ = await _score(spec, method, [cc.judge_worst], 1)
    hi, lo = one.score, worst.score
    assert hi is not None and lo is not None and hi > lo
    best_majority, _ = await _score(
        spec, method, [cc.judge_best, cc.judge_worst, cc.judge_best], 3
    )
    assert best_majority.score == pytest.approx(hi)
    tie, _ = await _score(spec, method, [cc.judge_best, cc.judge_worst], 2)
    if spec.id == "agent.execution_chain":
        # Documented source formula (AgentSight _aggregate_samples): per-link rounded median.
        assert lo <= tie.score <= hi
    else:
        assert tie.score == pytest.approx(lo), "ties must go to the lower score"


# --------------------------------------------------------------------------- service policy

DENIED = [(s, m) for s, m in PAIRS if not m.service_allowed or m.name.startswith("agentic")]


def test_denied_methods_exist() -> None:
    assert len(DENIED) >= 5


@pytest.mark.parametrize(("spec", "method"), DENIED, ids=[f"{s.id}:{m.name}" for s, m in DENIED])
def test_policy_rejects_service_disallowed(spec: MetricSpec, method: MethodSpec) -> None:
    pytest.importorskip("fastapi")  # server extra is part of the CI full-extras leg
    from bnbagent_eval.server.policy import UntrustedSuitePolicy

    policy = UntrustedSuitePolicy(allowed_kinds={"rule", "judge", "model", "vendor"})
    cfg = MetricConfig(id=spec.id, method=method.name)
    assert not policy.method_allowed(spec, method)
    with pytest.raises(PolicyError, match="not allowed on the eval service"):
        policy(spec, method, cfg)
    assert all(
        m.name != method.name for c in policy.catalogue() if c.id == spec.id for m in c.methods
    )


# ------------------------------------------------------------------------------ input modes


def _modes(spec: MetricSpec) -> tuple[str, ...]:
    cls = registry.resolve(spec.id)[0]
    if not issubclass(cls, JudgeMetric):
        return ()
    cc = CONTRACT[spec.id]
    params = {**cc.params, **cc.method_params.get("native", {})}
    try:
        return cls().template_for(params).modes
    except SuiteError:
        return ()


MODE_PAIRS = [
    (s, m, mode)
    for s, m in JUDGE_PAIRS
    if m.name == "native"
    for mode in ("with_expected", "conversation")
    if mode in _modes(s)
]


def test_has_multi_mode_metrics() -> None:
    assert len(MODE_PAIRS) >= 10


@pytest.mark.parametrize(
    ("spec", "method", "mode"),
    MODE_PAIRS,
    ids=[f"{s.id}:{mode}" for s, _, mode in MODE_PAIRS],
)
async def test_auto_mode_detection(spec: MetricSpec, method: MethodSpec, mode: str) -> None:
    cc = CONTRACT[spec.id]
    case = cc.best()
    if mode == "conversation" and spec.id.startswith("agent.mcp_"):
        pytest.skip(
            "MCP judges: a trajectory wins; see test_agent_new_judge::test_mcp_conversation_mode"
        )
    if mode == "conversation":
        conv = (Message("user", "What is BNB Chain?"), Message("assistant", "An EVM chain."))
        case = dataclasses.replace(case, conversation=conv)
    else:
        case = dataclasses.replace(case, conversation=(), expected=case.expected or "reference")
    params = {**cc.params, **cc.method_params.get(method.name, {})}
    _params(spec, method)  # same skips as the main suite
    ctx = _ctx(spec, method, params, judge=_judge(cc, "best", method))
    await _metric(spec).evaluate(case, ctx)
    assert ctx.judge.requests, "no judge call"
    selected = ctx.judge.requests[0].metadata["mode"]
    token = "expected" if mode == "with_expected" else "conversation"
    # Input variants (with_input_and_expected, conversation_with_tools, ...) count as the mode.
    assert selected == mode or token in selected, (selected, mode)
    template = registry.resolve(spec.id)[0]().template_for(params)
    # The rendered body is the selected mode's template.
    assert ctx.judge.requests[0].user.startswith(template.parts[selected].split("{")[0])


def test_basemetric_is_contract_base() -> None:
    assert all(issubclass(registry.resolve(s.id)[0], BaseMetric) for s in registry.list_specs())
