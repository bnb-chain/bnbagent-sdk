"""Manifest hashing, attestations and evidence."""

from __future__ import annotations

import dataclasses
import json

import pytest

from bnbagent_eval.chain import attestation, evidence
from bnbagent_eval.chain.manifest import (
    build_manifest,
    canonical_manifest_json,
    content_hash,
    manifest_hash,
    parse_manifest,
    same_hash,
)
from bnbagent_eval.exceptions import AttestationError
from bnbagent_eval.hashing import keccak_hex
from bnbagent_eval.testing.fakes import FakeSigner

pytest.importorskip("eth_account", reason="needs the [evm] extra")


def test_manifest_matches_sdk_canonical_rules() -> None:
    m = build_manifest(
        job_id=1,
        chain_id=97,
        contracts={"commerce": "0xc"},
        content="héllo",
        metadata={"eval": {"score": 0.1 + 0.2}},
    )
    text = canonical_manifest_json(m)
    assert text == json.dumps(m, sort_keys=True, separators=(",", ":"))  # SDK rule, ensure_ascii
    assert "\\u00e9" in text and "0.30000000000000004" in text  # no float normalization
    assert manifest_hash(m) == keccak_hex(text)
    assert parse_manifest(json.loads(text)) == m
    with pytest.raises(ValueError):
        parse_manifest({**m, "version": 2})
    with pytest.raises(ValueError):
        parse_manifest({**m, "response": {"text": "x"}})
    assert same_hash(manifest_hash(m), bytes.fromhex(manifest_hash(m)[2:]))
    assert same_hash("0xAB", "ab") and not same_hash(None, None)
    assert content_hash("x") == keccak_hex("x")


def test_sign_and_verify_roundtrip(response, signer) -> None:
    signed = attestation.sign(response, signer, now=1_700_000_000)
    att = signed.attestation
    assert att.scheme == "eip712" and att.evaluator == signer.address
    assert att.domain == {"chainId": 97, "verifyingContract": response.subject.commerce}
    assert attestation.verify(signed, now=1_700_000_100) == signer.address
    assert attestation.verify(signed, now=1_700_000_100, expected_evaluator=signer.address.lower())


@pytest.mark.parametrize("tamper", ["result", "subject", "suite", "signer", "domain", "expiry"])
def test_verify_rejects_tampering(response, signer, other_signer, tamper) -> None:
    signed = attestation.sign(response, signer, now=1_000, expires_in=100)
    att = signed.attestation
    if tamper == "result":
        signed = dataclasses.replace(signed, result_hash="0x" + "00" * 32)
    elif tamper == "subject":
        signed = dataclasses.replace(signed, subject=dataclasses.replace(signed.subject, job_id=8))
    elif tamper == "suite":
        signed = dataclasses.replace(signed, suite_hash="0x" + "11" * 32)
    elif tamper == "signer":
        forged = attestation.sign(response, other_signer, now=1_000).attestation
        signed = dataclasses.replace(
            signed, attestation=dataclasses.replace(att, signature=forged.signature)
        )
    elif tamper == "domain":
        signed = dataclasses.replace(
            signed,
            attestation=dataclasses.replace(
                att, domain={"chainId": 56, "verifyingContract": att.domain["verifyingContract"]}
            ),
        )
    with pytest.raises(AttestationError):
        attestation.verify(signed, now=2_000 if tamper == "expiry" else 1_050)


def test_eip191_fallback_and_unsupported_signers(response, signer) -> None:
    class MessageOnly:
        address = signer.address

        def supports(self, cap):
            return cap == "sign.message"

        def sign_typed_data(self, data):
            raise AssertionError("must not be called")

        def sign_message(self, message):
            return signer.sign_message(message)

    signed = attestation.sign(response, MessageOnly(), now=1)
    assert signed.attestation.scheme == "eip191"
    assert attestation.verify(signed, now=2) == signer.address
    with pytest.raises(AttestationError, match="neither"):
        attestation.sign(response, FakeSigner(capabilities=()), now=1)
    with pytest.raises(AttestationError, match="no attestation"):
        attestation.verify(response)
    assert "LocalKeySigner(address=" in repr(signer) and "ac09" not in repr(signer)


def test_evidence_json_budget_and_summary(response, signer) -> None:
    signed = attestation.sign(response, signer, now=1)
    ev = evidence.build(signed, report_uri="ipfs://report", report_hash="0x" + "cd" * 32)
    assert ev["schema"] == evidence.SCHEMA and ev["subject"]["job_id"] == 7
    assert ev["voter_summary"]["status"] == "pass" and ev["voter_summary"]["grade"] == "A"
    assert ev["attestation"]["evaluator"] == signer.address and "trimmed" not in ev
    big = dataclasses.replace(signed, results=signed.results * 2000)
    trimmed = evidence.build(big, budget_bytes=8 * 1024)
    assert len(json.dumps(trimmed).encode()) <= 8 * 1024
    assert {"cases"} <= set(trimmed["trimmed"])
    assert trimmed["result_hash"] == signed.result_hash and trimmed["subject"]  # bindings kept
    with pytest.raises(ValueError):
        evidence.build(big, budget_bytes=200)


def test_evidence_budget_counts_bytes_as_the_sdk_metadata_cap(response) -> None:
    # The SDK measures metadata with json.dumps defaults: non-ASCII text costs \uXXXX escapes.
    case = response.results[0]
    wordy = dataclasses.replace(
        case,
        metrics=tuple(dataclasses.replace(m, reason="费用很低" * 400) for m in case.metrics),
    )
    big = dataclasses.replace(response, results=(wordy,) * 4)
    full = evidence.build(big, budget_bytes=10**9)
    utf8 = len(json.dumps(full, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    escaped = len(json.dumps(full, separators=(",", ":")).encode("utf-8"))
    budget = (utf8 + escaped) // 2  # fits as UTF-8, not as the SDK counts it
    ev = evidence.build(big, budget_bytes=budget)
    assert len(json.dumps(ev, separators=(",", ":")).encode("utf-8")) <= budget
    assert "reasons" in ev["trimmed"]


def test_evidence_abi_roundtrip(response, signer) -> None:
    signed = attestation.sign(response, signer, now=1, expires_in=100)
    ev = evidence.build(signed)
    decoded = evidence.from_abi(evidence.to_abi(ev))
    assert decoded["subject"] == signed.subject.to_dict()
    assert decoded["result_hash"] == signed.result_hash and decoded["status"] == "pass"
    assert decoded["score"] == 1.0 and decoded["evaluator"] == signer.address
    assert decoded["signature"] == signed.attestation.signature.lower()
    with pytest.raises(ValueError):
        evidence.to_abi({**ev, "subject": None})


async def test_remote_client_checks_expected_evaluator(signer, other_signer) -> None:
    import httpx

    from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
    from bnbagent_eval.types import EvalRequest

    suite = Suite(
        name="r", version="1", metrics=(MetricConfig(id="text.length", params={"max_words": 100}),)
    )
    request = EvalRequest(stage="review", suite=suite, cases=(EvalCase(id="1", output="a"),))
    signed = await EvalClient(signer=signer).evaluate(request)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=signed.to_dict()))
    async with httpx.AsyncClient(transport=transport) as http:
        ok = EvalClient.remote("http://eval.test", client=http, expected_evaluator=signer.address)
        assert ok.backend.expected_evaluator == signer.address
        assert (await ok.evaluate(request)).attestation.evaluator == signer.address
        other = EvalClient.remote(
            "http://eval.test", client=http, expected_evaluator=other_signer.address
        )
        with pytest.raises(AttestationError, match="unexpected evaluator"):
            await other.evaluate(request)
