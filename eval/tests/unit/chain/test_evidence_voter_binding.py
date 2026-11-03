"""Chain workflows on fakes: evidence size at scale, every VoterAssistant binding field,
expired attestations, the negotiated suite in the DisputeAdvisor, the SDK job-description
round trip and the privacy warning for ``file://`` evidence URIs."""

from __future__ import annotations

import dataclasses
import json
import logging

import pytest

from bnbagent_eval import EvalCase, MetricConfig, Suite
from bnbagent_eval.chain import attestation, evidence
from bnbagent_eval.chain.erc8183 import (
    DisputeAdvisor,
    VoterAssistant,
    parse_suite_criterion,
    suite_criterion,
)
from bnbagent_eval.chain.manifest import manifest_hash
from bnbagent_eval.engine import Runner
from bnbagent_eval.testing.fakes import FakePolicy, MemoryStorage
from bnbagent_eval.types import EvalRequest

from .test_workflows import _gate, _submitted, _world

pytest.importorskip("eth_account", reason="needs the [evm] extra")


# ------------------------------------------------------------------ EvidenceBuilder
@pytest.mark.parametrize("n_cases", [1, 100, 10_000])
async def test_evidence_fits_64kb_for_1_100_10000_cases(n_cases, job_subject, signer) -> None:
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="text.exact_match", required=True),)
    )
    cases = tuple(EvalCase(id=str(i), output="a", expected="a") for i in range(n_cases))
    resp = await Runner().run(
        EvalRequest(stage="pre_submit", suite=suite, subject=job_subject, cases=cases)
    )
    signed = attestation.sign(resp, signer, now=1)
    ev = evidence.build(signed, report_uri="ipfs://report", report_hash="0x" + "cd" * 32)
    assert len(json.dumps(ev).encode()) <= evidence.DEFAULT_BUDGET_BYTES == 64 * 1024
    # Bindings and hashes are always kept, whatever was trimmed.
    assert ev["result_hash"] == signed.result_hash and ev["suite_hash"] == signed.suite_hash
    assert ev["subject"] == job_subject.to_dict()
    assert ev["report"]["uri"] == "ipfs://report"
    assert attestation.verify_evidence(ev, now=2) == signer.address
    if n_cases == 1:
        assert "trimmed" not in ev
    if n_cases == 10_000:
        assert "cases" in ev["trimmed"]  # per-case detail is dropped first
    decoded = evidence.from_abi(evidence.to_abi(ev))
    assert decoded["result_hash"] == signed.result_hash


# ------------------------------------------------------------------ VoterAssistant
def _rebind(storage, commerce, edit) -> None:
    """Edit the provider's evidence and re-anchor the manifest (a consistent forgery)."""
    url = commerce.deliverable_urls[1]
    edit(storage.docs[url]["metadata"]["eval"])
    commerce.jobs[1] = dataclasses.replace(
        commerce.jobs[1], deliverable=manifest_hash(storage.docs[url])
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chain_id", 56),
        ("commerce", "0x" + "99" * 20),
        ("job_id", 2),
        ("provider", "0x" + "98" * 20),
        ("output_hash", "0x" + "97" * 32),
    ],
)
async def test_voter_flags_every_bad_binding_field(field, value, signer) -> None:
    storage, commerce, _, clock, client = await _submitted("BNB fees are low", signer=signer)
    _rebind(storage, commerce, lambda ev: ev["subject"].__setitem__(field, value))
    policy = FakePolicy(voters=[signer.address], caller=signer.address)
    await policy.dispute(1)
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    advice = await voter.review(1)
    check = advice.provider_evidence
    assert check["binding_ok"] is False and check["binding_mismatch"] == [field]
    assert any("different job or output" in r for r in advice.reasons)
    assert check["attestation_ok"] is False  # the signature covered the original subject


async def test_voter_flags_expired_attestation(signer) -> None:
    storage, commerce, _, clock, client = await _submitted(
        "BNB fees are low", signer=signer, clock_value=100
    )
    policy = FakePolicy(voters=[signer.address], caller=signer.address, vote_window=None)
    await policy.dispute(1)
    voter = VoterAssistant(commerce, policy, storage, client, signer, clock=lambda: clock["now"])
    fresh = await voter.review(1)
    assert fresh.provider_evidence["attestation_ok"] is True
    clock["now"] = 100 + 31 * 86400  # default attestation lifetime is 30 days
    late = await voter.review(1)
    assert late.provider_evidence["attestation_ok"] is False
    assert "expired" in late.provider_evidence["attestation_error"]
    assert "provider attestation is invalid" in late.reasons


# ------------------------------------------------------------------ DisputeAdvisor
async def test_dispute_advisor_uses_the_negotiated_suite(signer) -> None:
    storage, commerce, suite_hash, clock, client = await _submitted(
        "BNB fees are low", signer=signer
    )
    _rebind(storage, commerce, lambda ev: ev.__setitem__("suite_hash", "0x" + "55" * 32))
    advisor = DisputeAdvisor(commerce, FakePolicy(), storage, client, clock=lambda: clock["now"])
    advice = await advisor.advise(1)
    assert advice.response.suite_hash == suite_hash  # not the one named in the evidence
    assert advice.provider_evidence["suite_match"] is False


# ------------------------------------------------------------------ suite criterion (SDK)
def test_suite_criterion_survives_sdk_job_description_roundtrip() -> None:
    negotiation = pytest.importorskip("bnbagent.erc8183.negotiation")
    suite_uri = "ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi/suite.json"
    suite_hash = "0x" + "ab" * 32
    result = {
        "request": {"task_description": "Summarize BNB fees"},
        "response": {
            "accepted": True,
            "terms": {
                "price": "1000",
                "currency": "0x" + "11" * 20,
                "deliverables": "a summary",
                "quality_standards": "short and correct",
                "success_criteria": ["short", suite_criterion(suite_uri, suite_hash)],
            },
        },
        "chain_id": 97,
    }
    description = negotiation.build_job_description(result)
    assert len(description.encode()) <= negotiation.MAX_DESCRIPTION_BYTES == 4096
    assert parse_suite_criterion(description) == (suite_uri, suite_hash)
    parsed = negotiation.parse_job_description(description)
    assert parse_suite_criterion({"terms": parsed.terms}) == (suite_uri, suite_hash)


# ---------------------------------------------------------- privacy: file:// URIs
class _FileStorage(MemoryStorage):
    async def upload(self, data, name):
        uri = "file:///tmp/" + name
        self.docs[uri] = json.loads(json.dumps(dict(data)))
        return uri


async def test_gate_warns_when_the_report_uri_is_file(caplog) -> None:
    for reports, warned in ((_FileStorage(), True), (MemoryStorage(), False)):
        caplog.clear()
        storage, commerce, _, _, content = await _world()
        gate = _gate(commerce, storage, content, report_storage=reports)
        with caplog.at_level(logging.WARNING, logger="bnbagent_eval.chain.erc8183"):
            await gate(1)
            await gate.wait()
        assert len(commerce.submissions) == 1
        assert ("file:// URI" in caplog.text) is warned
