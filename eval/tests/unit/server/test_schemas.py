"""Checked-in JSON Schema / OpenAPI files are current; fixtures validate against them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
jsonschema = pytest.importorskip("jsonschema")

from bnbagent_eval import EvalCase, JobSubject, MetricConfig, Suite  # noqa: E402
from bnbagent_eval.chain import evidence  # noqa: E402
from bnbagent_eval.chain.attestation import LocalKeySigner, sign  # noqa: E402
from bnbagent_eval.chain.erc8004 import MetricsPublisher  # noqa: E402
from bnbagent_eval.cli import _default_schema_dir, main  # noqa: E402
from bnbagent_eval.engine import Runner  # noqa: E402
from bnbagent_eval.types import EvalRequest  # noqa: E402

TEST_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
)
SCHEMA_DIR = Path(_default_schema_dir())


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _validate(name: str, instance) -> None:
    schema = _schema(name)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(instance)


def test_schemas_check_passes() -> None:
    assert main(["schemas", "--check"]) == 0


def test_schemas_check_detects_stale(tmp_path, capsys) -> None:
    assert main(["schemas", "--dir", str(tmp_path)]) == 0
    (tmp_path / "Suite.schema.json").write_text("{}", encoding="utf-8")
    assert main(["schemas", "--dir", str(tmp_path), "--check"]) == 1
    assert "Suite.schema.json" in capsys.readouterr().err


async def test_fixtures_validate() -> None:
    subject = JobSubject(
        chain_id=97,
        commerce="0x" + "81" * 20,
        job_id=7,
        provider="0x" + "22" * 20,
        output_hash="0x" + "ab" * 32,
    )
    suite = Suite(
        name="s",
        version="1",
        metrics=(
            MetricConfig(id="text.exact_match", required=True),
            MetricConfig(id="ops.latency"),
        ),
    )
    request = EvalRequest(
        stage="pre_submit",
        suite=suite,
        subject=subject,
        cases=(EvalCase(id="1", output="a", expected="a"),),
    )
    response = sign(await Runner().run(request), LocalKeySigner(TEST_KEY))
    _validate("EvalRequest", request.to_dict())
    _validate("Suite", suite.to_dict())
    _validate("EvalResponse", response.to_dict())
    _validate("Attestation", response.attestation.to_dict())
    _validate("EvalEvidenceV1", evidence.build(response))
    _validate("RegistrationMetricsV1", MetricsPublisher(None).build_entry(response))
    with pytest.raises(jsonschema.ValidationError):
        _validate("EvalRequest", {**request.to_dict(), "cases": [{"id": 1}]})
    with pytest.raises(jsonschema.ValidationError):
        _validate("EvalResponse", {**response.to_dict(), "stage": "nope"})


def test_openapi_lists_endpoints_and_components() -> None:
    doc = json.loads((SCHEMA_DIR / "openapi.json").read_text(encoding="utf-8"))
    assert doc["openapi"].startswith("3.1")
    paths = set(doc["paths"])
    assert {
        "/health",
        "/v1/metrics",
        "/v1/metrics/{metric_id}",
        "/v1/quote",
        "/v1/evaluate",
        "/v1/evaluations",
        "/v1/evaluations/{job_id}",
        "/v1/credits/redeem",
        "/.well-known/agent-card.json",
    } <= paths
    components = doc["components"]["schemas"]
    assert {"EvalRequest", "EvalResponse", "Suite", "Attestation", "EvalEvidenceV1"} <= set(
        components
    )
    body = doc["paths"]["/v1/evaluate"]["post"]["requestBody"]["content"]["application/json"]
    assert body["schema"] == {"$ref": "#/components/schemas/EvalRequest"}
    assert "402" in doc["paths"]["/v1/evaluate"]["post"]["responses"]
