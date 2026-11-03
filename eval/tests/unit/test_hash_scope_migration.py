"""``result_hash`` scope, schema migration and suite errors."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.engine.seal import compute_response_hash, compute_result_hash
from bnbagent_eval.exceptions import SchemaError, SuiteError
from bnbagent_eval.types import AppMetrics, JobSubject
from bnbagent_eval.types.request import Attestation, EvalResponse
from bnbagent_eval.types.result import Cost, RunResult

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemas" / "v1_response.json"
SUBJECT = JobSubject(chain_id=97, commerce="0x1", job_id=7, provider="0x2", output_hash="0x3")


def _response() -> EvalResponse:
    suite = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="text.exact_match"), MetricConfig(id="text.token_f1")),
    )
    return EvalClient().evaluate_sync(
        suite=suite,
        subject=SUBJECT,
        cases=[EvalCase(id="1", output="BNB 费用", expected="BNB"), EvalCase(id="2", output="x")],
    )


def _metric(resp: EvalResponse, **changes) -> EvalResponse:
    case = resp.results[0]
    m = dataclasses.replace(case.metrics[0], **changes)
    case = dataclasses.replace(case, metrics=(m, *case.metrics[1:]))
    return dataclasses.replace(resp, results=(case, *resp.results[1:]))


def _prov(resp: EvalResponse, **changes) -> EvalResponse:
    m = resp.results[0].metrics[0]
    return _metric(resp, provenance=dataclasses.replace(m.provenance, **changes))


# ----------------------------------------------- excluded from result_hash, in response_hash


def _with_runs(resp: EvalResponse, app: AppMetrics) -> EvalResponse:
    case = dataclasses.replace(resp.results[0], runs=(RunResult(run_index=0, app_metrics=app),))
    return dataclasses.replace(resp, results=(case, *resp.results[1:]))


VOLATILE = {
    "timestamps": lambda r: dataclasses.replace(
        r,
        provenance=dataclasses.replace(
            r.provenance, started_at=r.provenance.started_at + 5, finished_at=1e10
        ),
    ),
    "run_latency": lambda r: _with_runs(r, AppMetrics(latency_ms=999.0, ttft_ms=12.0)),
    "run_cost": lambda r: _with_runs(r, AppMetrics(cost_usd=1.25)),
    "response_cost": lambda r: dataclasses.replace(r, cost=Cost(judge_calls=9, usd_estimate=3.0)),
    "judge_source": lambda r: _prov(r, judge_source="cache:sqlite"),
}


@pytest.mark.parametrize("change", sorted(VOLATILE))
def test_result_hash_excludes_volatile_fields(change: str) -> None:
    resp = _response()
    base = _with_runs(resp, AppMetrics(latency_ms=1.0)) if change.startswith("run") else resp
    changed = VOLATILE[change](base)
    assert compute_result_hash(changed) == compute_result_hash(base)
    assert compute_response_hash(changed) != compute_response_hash(base)


def test_attestation_excluded_from_both_hashes() -> None:
    resp = _response()
    att = Attestation(
        scheme="eip191", evaluator="0x" + "11" * 20, digest="0x00", signature="0x01", issued_at=1
    )
    signed = dataclasses.replace(resp, attestation=att)
    assert compute_result_hash(signed) == compute_result_hash(resp)
    assert compute_response_hash(signed) == compute_response_hash(resp)


# ------------------------------------------------------------- included in result_hash

RELEVANT = {
    "score": lambda r: _metric(r, score=0.5),
    "reason": lambda r: _metric(r, reason="different reason"),
    "threshold": lambda r: _metric(r, threshold=0.9),
    "metric_version": lambda r: _prov(r, metric_version="99"),
    "prompt_hash": lambda r: _prov(r, prompt_hash="0xabc"),
    "metric_judge_config": lambda r: _prov(r, judge={"provider": "x", "model": "m2"}),
    "response_judge_config": lambda r: dataclasses.replace(
        r, provenance=dataclasses.replace(r.provenance, judge={"model": "other"})
    ),
    "subject_job_id": lambda r: dataclasses.replace(
        r, subject=dataclasses.replace(SUBJECT, job_id=8)
    ),
    "subject_output_hash": lambda r: dataclasses.replace(
        r, subject=dataclasses.replace(SUBJECT, output_hash="0x4")
    ),
    "suite_hash": lambda r: dataclasses.replace(r, suite_hash="0x" + "00" * 32),
}


@pytest.mark.parametrize("change", sorted(RELEVANT))
def test_result_hash_covers_every_scored_field(change: str) -> None:
    resp = _response()
    assert resp.subject == SUBJECT
    assert compute_result_hash(RELEVANT[change](resp)) != compute_result_hash(resp)


# ------------------------------------------------------------------------------ migration


def test_v1_fixture_loads_and_round_trips() -> None:
    """A response serialized by schema v1 keeps loading (regression guard for migrations)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["schema_version"] == "bnbagent-eval/v1"
    resp = EvalResponse.from_dict(data)
    assert resp.result_hash == compute_result_hash(resp)  # hash still verifies after loading
    assert resp.to_dict() == data


def test_unknown_fields_survive_response_round_trip() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["future_field"] = {"x": [1, 2]}
    resp = EvalResponse.from_dict(data)
    assert resp.to_dict()["future_field"] == {"x": [1, 2]}


def test_future_major_version_raises_on_response() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["schema_version"] = "bnbagent-eval/v2"
    with pytest.raises(SchemaError, match="newer"):
        EvalResponse.from_dict(data)


# --------------------------------------------------------------------- suite field paths


@pytest.mark.parametrize(
    ("bad", "path"),
    [
        ({"metrics": []}, r"\$:"),
        ({"metrics": [{"id": "custom.python", "threshold": 2}]}, r"\$\.metrics\[0\]"),
        ({"metrics": [{"id": "custom.python", "bogus": 1}]}, r"\$\.metrics\[0\].*bogus"),
        ({"metrics": [{"id": "custom.python", "weight": "heavy"}]}, r"\$\.metrics\[0\]\.weight"),
    ],
)
def test_invalid_suite_errors_name_the_field_path(bad: dict, path: str) -> None:
    with pytest.raises(SuiteError, match=path):
        Suite.load({"name": "s", "version": "1", **bad})


def write_fixture() -> None:  # pragma: no cover - run by hand to (re)create the v1 fixture
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(_response().to_dict(), indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":  # pragma: no cover
    write_fixture()
