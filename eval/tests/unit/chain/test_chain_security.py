"""Chain security: negotiated suites cannot escalate privileges, the evidence headline is
signed, and attestations bind to the job subject (threat model: docs/design/security.md)."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from bnbagent_eval import Suite
from bnbagent_eval.chain import attestation, evidence
from bnbagent_eval.chain.erc8183 import (
    check_provider_evidence,
    suite_criterion,
    suite_from_job,
)
from bnbagent_eval.engine import resolve_suite
from bnbagent_eval.exceptions import AttestationError, ChainIntegrationError, SuiteError
from bnbagent_eval.ports import JobView
from bnbagent_eval.testing.fakes import MemoryStorage

pytest.importorskip("eth_account", reason="needs the [evm] extra")

BASE = {"name": "neg", "version": "1", "metrics": [{"id": "text.contains"}]}


async def _job_with_suite(doc: dict) -> tuple[JobView, MemoryStorage]:
    storage = MemoryStorage()
    uri = await storage.upload(doc, "suite.json")
    suite_hash = "0x" + "00" * 32
    try:
        suite_hash = resolve_suite(Suite.load(doc, refs=False)).suite_hash
    except SuiteError:
        pass
    desc = json.dumps(
        {"task": "t", "terms": {"success_criteria": [suite_criterion(uri, suite_hash)]}}
    )
    job = JobView(
        job_id=1,
        client="0x" + "c1" * 20,
        provider="0x" + "d2" * 20,
        status="funded",
        description=desc,
    )
    return job, storage


# ---------------------------------------------------------- negotiated suites
async def test_negotiated_suite_cannot_redirect_the_judge_key(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_JUDGE_BASE_URL", raising=False)
    doc = {
        **BASE,
        "judge": {"provider": "openai", "model": "m", "base_url": "https://evil.example"},
    }
    job, storage = await _job_with_suite(doc)
    with pytest.raises(ChainIntegrationError) as exc:
        await suite_from_job(job, storage)
    assert exc.value.code == "suite_denied"
    # The locally configured endpoint (or an explicit allow-list) is fine.
    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "https://evil.example/")
    assert (await suite_from_job(job, storage)).judge.base_url == "https://evil.example"
    monkeypatch.delenv("EVAL_JUDGE_BASE_URL")
    allowed = await suite_from_job(job, storage, allowed_judge_base_urls=["https://evil.example"])
    assert allowed.name == "neg"


async def test_negotiated_suite_cannot_run_python() -> None:
    doc = {**BASE, "metrics": [{"id": "custom.python", "params": {"fn": "os:getcwd"}}]}
    job, storage = await _job_with_suite(doc)
    with pytest.raises(ChainIntegrationError, match="custom.python"):
        await suite_from_job(job, storage)


async def test_negotiated_suite_cannot_read_local_files(tmp_path) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text('{"id": "text.contains"}')
    doc = {**BASE, "metrics": [{"$ref": str(secret)}]}
    job, storage = await _job_with_suite(doc)
    with pytest.raises(ChainIntegrationError, match=r"\$ref"):
        await suite_from_job(job, storage)
    with pytest.raises(SuiteError):
        Suite.load(doc, refs=False)
    assert Suite.load(doc).metrics[0].id == "text.contains"  # local files keep $ref


# ---------------------------------------------------------- signed headline
def _evidence(response, signer):
    return evidence.build(attestation.sign(response, signer, now=1))


@pytest.mark.parametrize("field", ["status", "score"])
def test_evidence_verdict_and_score_are_signed(response, signer, field) -> None:
    ev = _evidence(response, signer)
    assert attestation.verify_evidence(ev, now=2) == signer.address
    forged = copy.deepcopy(ev)
    if field == "status":
        forged["verdict"]["status"] = "fail" if ev["verdict"]["status"] == "pass" else "pass"
    else:
        forged["aggregate"]["score"] = 0.01 if ev["aggregate"]["score"] != 0.01 else 0.99
    with pytest.raises(AttestationError):
        attestation.verify_evidence(forged, now=2)


def test_abi_score_and_status_are_the_signed_values(response, signer) -> None:
    ev = _evidence(response, signer)
    decoded = evidence.from_abi(evidence.to_abi(ev))
    assert attestation.status_code(decoded["status"]) == attestation.status_code(
        ev["verdict"]["status"]
    )
    assert round(decoded["score"] * 10_000) == attestation.score_bps(ev["aggregate"]["score"])


def test_attestation_domain_must_match_the_subject(response, signer) -> None:
    domain = {"chainId": 1, "verifyingContract": "0x" + "99" * 20}
    signed = attestation.sign(response, signer, now=1, domain=domain)
    with pytest.raises(AttestationError, match="domain"):
        attestation.verify(signed, now=2)
    with pytest.raises(AttestationError, match="domain"):
        attestation.verify_evidence(evidence.build(signed), now=2)


def test_provider_evidence_flags_summary_and_self_attestation(
    response, signer, job_subject
) -> None:
    ev = _evidence(response, signer)
    check = check_provider_evidence(
        ev, negotiated_suite_hash=ev["suite_hash"], subject=job_subject, now=2
    )
    assert check["attestation_ok"] and check["summary_consistent"] and not check["self_attested"]
    edited = copy.deepcopy(ev)
    edited["voter_summary"]["status"] = "pass" if ev["verdict"]["status"] != "pass" else "fail"
    check = check_provider_evidence(
        edited, negotiated_suite_hash=ev["suite_hash"], subject=job_subject, now=2
    )
    assert check["attestation_ok"] and not check["summary_consistent"]
    own = dataclasses.replace(job_subject, provider=signer.address)
    check = check_provider_evidence(ev, negotiated_suite_hash=ev["suite_hash"], subject=own, now=2)
    assert check["self_attested"] is True and check["binding_ok"] is False
