"""EvalEvidenceV1: evaluation evidence attached to an ERC-8183 deliverable.

JSON form (``DeliverableManifest.metadata["eval"]``, kept under a byte budget) carries a
plain-language ``voter_summary`` over the technical results, the subject binding, suite hash,
hashes, report pointer and attestation. The ABI byte form is for a future eval-aware policy::

    (uint256 chainId, address commerce, uint256 jobId, address provider, bytes32 outputHash,
     bytes32 suiteHash, bytes32 resultHash, uint16 scoreBps, uint8 status, address evaluator,
     uint64 expiresAt, bytes signature)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .._optional import require
from ..types.request import EvalResponse
from ..types.subject import JobSubject
from .attestation import STATUS_CODES, score_bps

SCHEMA = "bnbagent-eval/evidence/v1"
DEFAULT_BUDGET_BYTES = 64 * 1024
ABI_TYPES = [
    "uint256",
    "address",
    "uint256",
    "address",
    "bytes32",
    "bytes32",
    "bytes32",
    "uint16",
    "uint8",
    "address",
    "uint64",
    "bytes",
]
ZERO_ADDRESS = "0x" + "00" * 20


def _grade(score: float | None) -> str:
    if score is None:
        return "N/A"
    for cut, grade in ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D")):
        if score >= cut:
            return grade
    return "F"


def voter_summary(response: EvalResponse) -> dict[str, Any]:
    v, a = response.verdict, response.aggregate
    failed_required = [
        r.name for c in response.results for r in c.metrics if r.required and r.passed is False
    ]
    per_metric = [
        {
            "name": m.name,
            "mean": m.mean,
            "pass_rate": m.pass_rate,
            "scored": m.scored,
            "count": m.count,
        }
        for m in a.metrics
    ]
    score_text = "n/a" if a.score is None else f"{a.score:.2f}"
    return {
        "headline": f"Verdict {v.status.upper()} (score {score_text}, grade {_grade(a.score)}) "
        f"over {a.cases} case(s)",
        "grade": _grade(a.score),
        "status": v.status,
        "action": v.action,
        "reasons": list(v.reasons),
        "material_failure": bool(failed_required),
        "failed_required_metrics": sorted(set(failed_required)),
        "per_metric": per_metric,
    }


def build(
    response: EvalResponse,
    *,
    report_uri: str | None = None,
    report_hash: str | None = None,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
) -> dict[str, Any]:
    """The JSON evidence, trimmed to ``budget_bytes`` (per-case detail is dropped first)."""
    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "subject": response.subject.to_dict() if response.subject else None,
        "stage": response.stage,
        "suite_hash": response.suite_hash,
        "result_hash": response.result_hash,
        "response_hash": response.response_hash,
        "aggregate": {
            "score": response.aggregate.score,
            "pass_rate": response.aggregate.pass_rate,
            "error_rate": response.aggregate.error_rate,
            "cases": response.aggregate.cases,
        },
        "verdict": response.verdict.to_dict(),
        "voter_summary": voter_summary(response),
        "report": {"uri": report_uri, "hash": report_hash} if report_uri else None,
        "attestation": response.attestation.to_dict() if response.attestation else None,
        "cases": [
            {
                "case_id": c.case_id,
                "score": c.score,
                "passed": c.passed,
                "metrics": [
                    {
                        "name": m.name,
                        "score": m.score,
                        "passed": m.passed,
                        "status": m.status,
                        "reason": m.reason,
                    }
                    for m in c.metrics
                ],
            }
            for c in response.results
        ],
    }
    for trim in (_drop_reasons, _drop_case_metrics, _drop_cases, _drop_per_metric):
        if _size(evidence) <= budget_bytes:
            break
        trim(evidence)
    if _size(evidence) > budget_bytes:
        raise ValueError(f"evidence exceeds {budget_bytes} bytes even after trimming")
    return evidence


def _size(obj: Any) -> int:
    """Bytes as the SDK's ``submit_result`` metadata cap counts them (``\\uXXXX`` escapes)."""
    return len(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def _drop_reasons(e: dict) -> None:
    for c in e["cases"]:
        for m in c["metrics"]:
            m.pop("reason", None)
    e["trimmed"] = sorted(set(e.get("trimmed", [])) | {"reasons"})


def _drop_case_metrics(e: dict) -> None:
    for c in e["cases"]:
        c.pop("metrics", None)
    e["trimmed"] = sorted(set(e.get("trimmed", [])) | {"case_metrics"})


def _drop_cases(e: dict) -> None:
    e["cases"] = []
    e["trimmed"] = sorted(set(e.get("trimmed", [])) | {"cases"})


def _drop_per_metric(e: dict) -> None:
    e["voter_summary"]["per_metric"] = []
    e["trimmed"] = sorted(set(e.get("trimmed", [])) | {"per_metric"})


def to_abi(evidence: Mapping[str, Any]) -> bytes:
    """ABI-encode the binding fields of JSON evidence (needs ``[evm]``)."""
    eth_abi = require("eth_abi", "evm")
    subject = evidence.get("subject") or {}
    if subject.get("type") != "job":
        raise ValueError("ABI evidence needs a job subject")
    att = evidence.get("attestation") or {}
    score = evidence["aggregate"]["score"]
    values = [
        int(subject["chain_id"]),
        subject["commerce"],
        int(subject["job_id"]),
        subject["provider"],
        bytes.fromhex(subject["output_hash"][2:]),
        bytes.fromhex(evidence["suite_hash"][2:]),
        bytes.fromhex(evidence["result_hash"][2:]),
        score_bps(score),
        STATUS_CODES[evidence["verdict"]["status"]],
        att.get("evaluator") or ZERO_ADDRESS,
        int(att.get("expires_at") or 0),
        bytes.fromhex((att.get("signature") or "0x")[2:]),
    ]
    return eth_abi.encode(ABI_TYPES, values)


def from_abi(data: bytes) -> dict[str, Any]:
    eth_abi = require("eth_abi", "evm")
    (
        chain_id,
        commerce,
        job_id,
        provider,
        output_hash,
        suite_hash,
        result_hash,
        score_bps,
        status,
        evaluator,
        expires_at,
        signature,
    ) = eth_abi.decode(ABI_TYPES, data)
    checksum = require("eth_utils", "evm").to_checksum_address
    commerce, provider, evaluator = checksum(commerce), checksum(provider), checksum(evaluator)
    names = {v: k for k, v in STATUS_CODES.items()}
    return {
        "subject": JobSubject(
            chain_id=chain_id,
            commerce=commerce,
            job_id=job_id,
            provider=provider,
            output_hash="0x" + output_hash.hex(),
        ).to_dict(),
        "suite_hash": "0x" + suite_hash.hex(),
        "result_hash": "0x" + result_hash.hex(),
        "score": score_bps / 10_000,
        "status": names.get(status, "unknown"),
        "evaluator": evaluator,
        "expires_at": expires_at,
        "signature": "0x" + signature.hex(),
    }
