"""Attestations: EIP-712 signatures over an EvalResponse.

Typed data::

    domain  = {name: "bnbagent-eval", version: "1", chainId, verifyingContract}
    message = Attestation{resultHash, subjectHash, suiteHash, status, scoreBps, evaluator,
                          issuedAt, expiresAt}

``status`` / ``scoreBps`` are the verdict and aggregate score in the EvalEvidenceV1 encoding, so
the headline a voter (or an on-chain policy) reads from evidence is signed, not just the hashes.
``verifyingContract`` is the commerce (jobs) or registry (agents) address, else the zero
address; verification requires the domain to match the subject. If a signer cannot sign typed
data, EIP-191 over the 32-byte EIP-712 digest is used
(``scheme="eip191"``). Signing needs a ``SignerPort``; verification needs ``[evm]``.
``verify_evidence`` checks an attestation from EvalEvidenceV1 fields alone (no full response).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping
from typing import Any

from .._optional import require
from ..exceptions import AttestationError
from ..hashing import hash_obj, keccak_hex
from ..ports import SignerPort
from ..types.request import Attestation, EvalResponse
from ..types.subject import AgentSubject, JobSubject

ZERO_ADDRESS = "0x" + "00" * 20
ZERO_HASH = "0x" + "00" * 32
DOMAIN_NAME = "bnbagent-eval"
DOMAIN_VERSION = "1"

TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Attestation": [
        {"name": "resultHash", "type": "bytes32"},
        {"name": "subjectHash", "type": "bytes32"},
        {"name": "suiteHash", "type": "bytes32"},
        {"name": "status", "type": "uint8"},
        {"name": "scoreBps", "type": "uint16"},
        {"name": "evaluator", "type": "address"},
        {"name": "issuedAt", "type": "uint64"},
        {"name": "expiresAt", "type": "uint64"},
    ],
}


STATUS_CODES = {"pass": 1, "fail": 2, "inconclusive": 3, "pending_human": 4}


def status_code(status: str | None) -> int:
    """The EvalEvidenceV1 ``uint8 status`` of a verdict status (0 = unknown)."""
    return STATUS_CODES.get(str(status), 0)


def score_bps(score: float | None) -> int:
    """The aggregate score in basis points (``uint16``; ``None`` -> 0)."""
    if score is None:
        return 0
    return max(0, min(10_000, round(float(score) * 10_000)))


def subject_hash_of(subject: Mapping[str, Any] | None) -> str:
    return ZERO_HASH if subject is None else hash_obj(dict(subject))


def subject_hash(response: EvalResponse) -> str:
    return subject_hash_of(response.subject.to_dict() if response.subject else None)


def default_domain(response: EvalResponse) -> dict[str, Any]:
    subject = response.subject
    if isinstance(subject, JobSubject):
        return {"chainId": subject.chain_id, "verifyingContract": subject.commerce}
    if isinstance(subject, AgentSubject):
        return {"chainId": subject.chain_id, "verifyingContract": subject.registry}
    return {"chainId": 0, "verifyingContract": ZERO_ADDRESS}


def domain_of_subject(subject: Mapping[str, Any] | None) -> dict[str, Any]:
    """The default domain of a subject's dict form (job: commerce, agent: registry)."""
    subject = subject or {}
    if subject.get("type") == "job":
        return {"chainId": int(subject["chain_id"]), "verifyingContract": subject["commerce"]}
    if subject.get("type") == "agent":
        return {"chainId": int(subject["chain_id"]), "verifyingContract": subject["registry"]}
    return {"chainId": 0, "verifyingContract": ZERO_ADDRESS}


def _check_domain(domain: Mapping[str, Any], subject: Mapping[str, Any] | None) -> None:
    """A job / agent attestation must be signed under that subject's chain and contract."""
    if not subject or subject.get("type") not in ("job", "agent"):
        return
    expected = domain_of_subject(subject)
    try:
        same = (
            int(domain.get("chainId", -1)) == expected["chainId"]
            and str(domain.get("verifyingContract", "")).lower()
            == str(expected["verifyingContract"]).lower()
        )
    except (TypeError, ValueError):
        same = False
    if not same:
        raise AttestationError("attestation domain does not match the subject's chain / contract")


def typed_data_fields(
    *,
    result_hash: str,
    subject_hash: str,
    suite_hash: str,
    status: int,
    score_bps: int,
    evaluator: str,
    issued_at: int,
    expires_at: int,
    domain: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "types": TYPES,
        "primaryType": "Attestation",
        "domain": {
            "name": DOMAIN_NAME,
            "version": DOMAIN_VERSION,
            "chainId": int(domain["chainId"]),
            "verifyingContract": domain["verifyingContract"],
        },
        "message": {
            "resultHash": result_hash,
            "subjectHash": subject_hash,
            "suiteHash": suite_hash,
            "status": int(status),
            "scoreBps": int(score_bps),
            "evaluator": evaluator,
            "issuedAt": issued_at,
            "expiresAt": expires_at,
        },
    }


def typed_data(
    response: EvalResponse,
    *,
    evaluator: str,
    issued_at: int,
    expires_at: int,
    domain: Mapping[str, Any],
) -> dict[str, Any]:
    return typed_data_fields(
        result_hash=response.result_hash,
        subject_hash=subject_hash(response),
        suite_hash=response.suite_hash,
        status=status_code(response.verdict.status),
        score_bps=score_bps(response.aggregate.score),
        evaluator=evaluator,
        issued_at=issued_at,
        expires_at=expires_at,
        domain=domain,
    )


def eip712_digest(data: Mapping[str, Any]) -> str:
    messages = require("eth_account.messages", "evm")
    signable = messages.encode_typed_data(full_message=dict(data))
    return keccak_hex(b"\x19" + signable.version + signable.header + signable.body)


def sign(
    response: EvalResponse,
    signer: SignerPort,
    *,
    expires_in: int | None = 30 * 86400,
    domain: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> EvalResponse:
    """Return ``response`` with an attestation by ``signer`` (the evaluator)."""
    issued = int(now if now is not None else time.time())
    expires = issued + expires_in if expires_in else 0
    dom = dict(domain or default_domain(response))
    data = typed_data(
        response, evaluator=signer.address, issued_at=issued, expires_at=expires, domain=dom
    )
    digest = eip712_digest(data)
    if signer.supports("sign.typed_data"):
        scheme, signature = "eip712", signer.sign_typed_data(data)
    elif signer.supports("sign.message"):
        scheme, signature = "eip191", signer.sign_message(bytes.fromhex(digest[2:]))
    else:
        raise AttestationError("the signer supports neither sign.typed_data nor sign.message")
    attestation = Attestation(
        scheme=scheme,
        evaluator=signer.address,
        digest=digest,
        signature=signature,
        issued_at=issued,
        expires_at=expires or None,
        domain=dom,
    )
    return dataclasses.replace(response, attestation=attestation)


def _recover(data: Mapping[str, Any], scheme: str, signature: str) -> str:
    account = require("eth_account", "evm").Account
    messages = require("eth_account.messages", "evm")
    if scheme == "eip712":
        signable = messages.encode_typed_data(full_message=dict(data))
    else:
        signable = messages.encode_defunct(primitive=bytes.fromhex(eip712_digest(data)[2:]))
    try:
        return account.recover_message(signable, signature=signature)
    except Exception as exc:  # noqa: BLE001 - malformed signatures
        raise AttestationError(f"invalid signature: {exc}") from exc


def _check(
    data: Mapping[str, Any],
    *,
    scheme: str,
    signature: str,
    evaluator: str,
    digest: str,
    expires_at: int | None,
    now: float | None,
    expected_evaluator: str | None,
) -> str:
    if eip712_digest(data).lower() != str(digest).lower():
        raise AttestationError("attestation digest does not match the attested fields")
    recovered = _recover(data, scheme, signature)
    if recovered.lower() != evaluator.lower():
        raise AttestationError("signature was not made by the stated evaluator")
    if expected_evaluator and recovered.lower() != expected_evaluator.lower():
        raise AttestationError("attestation is from an unexpected evaluator")
    current = int(now if now is not None else time.time())
    if expires_at and current > expires_at:
        raise AttestationError("attestation has expired")
    return recovered


def verify(
    response: EvalResponse, *, now: float | None = None, expected_evaluator: str | None = None
) -> str:
    """Verify the attestation and the response hashes; return the evaluator address."""
    from ..engine.seal import verify_hashes

    att = response.attestation
    if att is None:
        raise AttestationError("response has no attestation")
    if not verify_hashes(response):
        raise AttestationError("result_hash / response_hash do not match the response")
    _check_domain(att.domain, response.subject.to_dict() if response.subject else None)
    data = typed_data(
        response,
        evaluator=att.evaluator,
        issued_at=att.issued_at,
        expires_at=att.expires_at or 0,
        domain=att.domain,
    )
    return _check(
        data,
        scheme=att.scheme,
        signature=att.signature,
        evaluator=att.evaluator,
        digest=att.digest,
        expires_at=att.expires_at,
        now=now,
        expected_evaluator=expected_evaluator,
    )


def verify_evidence(
    evidence: Mapping[str, Any], *, now: float | None = None, expected_evaluator: str | None = None
) -> str:
    """Verify the attestation inside EvalEvidenceV1 JSON against its bound fields.

    Bound (signed): subject, suite_hash, result_hash, ``verdict.status`` and
    ``aggregate.score``. Other fields (``voter_summary`` text, per-case rows, the report
    pointer) are not; recompute them from the full report when they matter.
    """
    att = evidence.get("attestation")
    if not att:
        raise AttestationError("evidence has no attestation")
    _check_domain(att.get("domain") or {}, evidence.get("subject"))
    data = typed_data_fields(
        result_hash=evidence["result_hash"],
        subject_hash=subject_hash_of(evidence.get("subject")),
        suite_hash=evidence["suite_hash"],
        status=status_code((evidence.get("verdict") or {}).get("status")),
        score_bps=score_bps((evidence.get("aggregate") or {}).get("score")),
        evaluator=att["evaluator"],
        issued_at=int(att["issued_at"]),
        expires_at=int(att.get("expires_at") or 0),
        domain=att.get("domain") or {},
    )
    return _check(
        data,
        scheme=att.get("scheme", "eip712"),
        signature=att["signature"],
        evaluator=att["evaluator"],
        digest=att.get("digest", ""),
        expires_at=att.get("expires_at"),
        now=now,
        expected_evaluator=expected_evaluator,
    )


class LocalKeySigner:
    """A ``SignerPort`` backed by a raw private key (``[evm]``). For tests and services."""

    def __init__(self, private_key: str | bytes):
        account_cls = require("eth_account", "evm").Account
        self._account = account_cls.from_key(private_key)
        self.address = self._account.address

    def supports(self, capability: str) -> bool:
        return capability in ("sign.typed_data", "sign.message")

    def sign_typed_data(self, typed_data: Mapping[str, Any]) -> str:
        messages = require("eth_account.messages", "evm")
        signed = self._account.sign_message(
            messages.encode_typed_data(full_message=dict(typed_data))
        )
        return "0x" + signed.signature.hex().removeprefix("0x")

    def sign_message(self, message: bytes) -> str:
        messages = require("eth_account.messages", "evm")
        signed = self._account.sign_message(messages.encode_defunct(primitive=message))
        return "0x" + signed.signature.hex().removeprefix("0x")

    def __repr__(self) -> str:  # never print key material
        return f"LocalKeySigner(address={self.address})"
