"""Golden tables for rule metrics: ReDoS safety, local ``$ref``, METEOR and ``custom.python``."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bnbagent_eval import register_python_metric
from bnbagent_eval._jsonschema import validate as mini_validate
from bnbagent_eval._optional import available
from bnbagent_eval.metrics.text.overlap import meteor
from bnbagent_eval.types import MetricResult

from ._util import case, run_metric

PAIRS = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "parity" / "text.json").read_text(encoding="utf-8")
)["pairs"]


# ------------------------------------------------------------------- format.regex (ReDoS)


@pytest.mark.skipif(not available("regex"), reason="needs the regex module ([format]/[server])")
async def test_catastrophic_regex_times_out_for_real() -> None:
    """No mock. The classic ``(a+)+$`` is optimized by ``regex`` (no hang, a plain 0);
    ``(a|aa)+$`` still backtracks exponentially there, so the timeout must stop it."""
    start = time.monotonic()
    nested = await run_metric(
        "format.regex", case("a" * 40 + "!"), {"pattern": r"(a+)+$", "timeout_ms": 100}
    )
    assert nested.status in ("scored", "error") and nested.score in (0.0, None)
    r = await run_metric(
        "format.regex", case("a" * 60 + "!"), {"pattern": r"(a|aa)+$", "timeout_ms": 100}
    )
    assert time.monotonic() - start < 5.0, "the regex timeout did not fire"
    assert r.status == "error" and r.error.code == "metric_error" and "timed out" in r.reason


# --------------------------------------------------------- format.json_schema (local $ref)

REF_SCHEMA = {
    "$defs": {"price": {"type": "number", "minimum": 0}},
    "type": "object",
    "required": ["price"],
    "properties": {"price": {"$ref": "#/$defs/price"}},
}


@pytest.mark.parametrize("backend", ["jsonschema", "builtin"])
async def test_json_schema_local_ref(backend: str, monkeypatch) -> None:
    if backend == "jsonschema" and not available("jsonschema"):
        pytest.skip("needs jsonschema ([format])")
    if backend == "builtin":
        import bnbagent_eval.metrics.format.structured as structured

        monkeypatch.setattr(structured, "available", lambda module: False)
    ok = await run_metric("format.json_schema", case('{"price": 3}'), {"schema": REF_SCHEMA})
    bad = await run_metric("format.json_schema", case('{"price": -1}'), {"schema": REF_SCHEMA})
    assert ok.details["backend"] == backend and bad.details["backend"] == backend
    assert ok.score == 1.0 and bad.score == 0.0, (ok, bad)


def test_builtin_validator_rejects_remote_ref_without_fetching() -> None:
    errors = mini_validate({}, {"$ref": "https://example.com/s.json"})
    assert errors and "only local $ref" in errors[0]
    assert mini_validate(1, {"$ref": "#/nope"}) == ["$: unresolvable $ref '#/nope'"]
    loop = {"$ref": "#"}
    assert "nesting deeper" in mini_validate(1, loop)[0]  # self-reference terminates


CORPUS = [
    ({"type": "integer"}, 3),
    ({"type": "integer"}, 3.5),
    ({"type": "integer"}, True),
    ({"type": "string", "minLength": 2}, "a"),
    ({"type": "string", "pattern": "^0x[0-9a-f]+$"}, "0xabc"),
    ({"type": "string", "pattern": "^0x[0-9a-f]+$"}, "abc"),
    ({"enum": ["a", "b"]}, "c"),
    ({"const": 1}, 1),
    ({"type": "array", "items": {"type": "number"}, "maxItems": 2}, [1, 2, 3]),
    ({"type": "array", "items": {"type": "number"}}, [1, "x"]),
    ({"type": "object", "additionalProperties": False, "properties": {"a": {}}}, {"b": 1}),
    ({"type": "object", "additionalProperties": {"type": "string"}}, {"b": 1}),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, None),
    ({"oneOf": [{"minimum": 0}, {"maximum": 10}]}, 5),
    ({"exclusiveMinimum": 0}, 0),
    ({"type": ["string", "null"]}, 3),
    (REF_SCHEMA, {"price": 2}),
    (REF_SCHEMA, {"price": "2"}),
    (REF_SCHEMA, {}),
]


@pytest.mark.skipif(not available("jsonschema"), reason="needs jsonschema ([format])")
@pytest.mark.parametrize(("schema", "value"), CORPUS, ids=[str(i) for i in range(len(CORPUS))])
def test_builtin_validator_agrees_with_jsonschema(schema, value) -> None:
    import jsonschema

    lib_ok = not list(jsonschema.validators.validator_for(schema)(schema).iter_errors(value))
    assert (not mini_validate(value, schema)) == lib_ok


# ---------------------------------------------------------------- text.meteor vs NLTK


@pytest.mark.skipif(not available("nltk"), reason="needs nltk ([text])")
@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["candidate"][:20])
def test_meteor_matches_nltk_without_wordnet(pair) -> None:
    """Our METEOR = NLTK's ``meteor_score`` with the WordNet synonym stage disabled."""
    from nltk.stem.porter import PorterStemmer
    from nltk.translate.meteor_score import meteor_score

    class NoWordnet:
        @staticmethod
        def synsets(*_a, **_k):
            return []

    ref, hyp = pair["reference"].lower().split(), pair["candidate"].lower().split()
    expected = meteor_score([ref], hyp, stemmer=PorterStemmer(), wordnet=NoWordnet())
    assert meteor(ref, hyp) == pytest.approx(expected, abs=1e-6)


# ------------------------------------------------------------------------- custom.python


def _sync(case, args):
    return float(case.output == args.get("want", "good"))


async def _async(case):
    return {"score": 0.25, "value": 7, "unit": "items", "reason": "async", "details": {"k": 1}}


def _result(case):
    return MetricResult(metric_id="x", name="x", method="rule", score=0.5, reason="mr")


def _raises(case):
    raise RuntimeError("user bug")


def _bad_type(case):
    return "not a score"


for _name, _fn in {
    "golden_sync": _sync,
    "golden_async": _async,
    "golden_result": _result,
    "golden_raises": _raises,
    "golden_bad_type": _bad_type,
}.items():
    register_python_metric(_name, _fn)


async def test_custom_python_sync_with_args() -> None:
    p = {"fn": "golden_sync", "args": {"want": "yes"}}
    assert (await run_metric("custom.python", case("yes"), p)).score == 1.0
    assert (await run_metric("custom.python", case("no"), p)).score == 0.0


async def test_custom_python_async_dict() -> None:
    r = await run_metric("custom.python", case("x"), {"fn": "golden_async"})
    assert (r.score, r.value, r.unit, r.reason, r.details) == (0.25, 7, "items", "async", {"k": 1})


async def test_custom_python_metric_result_return() -> None:
    r = await run_metric("custom.python", case("x"), {"fn": "golden_result"})
    assert r.status == "scored" and r.score == 0.5 and r.reason == "mr"
    assert r.metric_id == "custom.python"  # the engine's identity wins over the callable's


async def test_custom_python_import_path() -> None:
    r = await run_metric("custom.python", case("good"), {"fn": f"{__name__}:_sync"})
    assert r.score == 1.0


@pytest.mark.parametrize(
    ("fn", "needle"),
    [
        ("golden_raises", "user bug"),
        ("golden_bad_type", "unsupported type"),
        ("nope", "no python"),
    ],
)
async def test_custom_python_failures_are_errors(fn: str, needle: str) -> None:
    r = await run_metric("custom.python", case("x"), {"fn": fn})
    assert r.status == "error" and r.error.code == "metric_error" and needle in r.error.message
