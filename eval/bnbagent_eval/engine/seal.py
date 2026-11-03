"""result_hash (deterministic) and response_hash (envelope).

See docs/design/architecture.md for what each hash covers."""

from __future__ import annotations

from typing import Any

from ..hashing import hash_obj
from ..types.request import EvalResponse

# Fields excluded from result_hash because they legitimately differ between replays.
_VOLATILE_PROVENANCE = ("judge_source",)


def _strip_volatile(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for case in results:
        case = dict(case)
        case["runs"] = [
            {k: v for k, v in run.items() if k != "app_metrics"} for run in case["runs"]
        ]
        metrics = []
        for m in case["metrics"]:
            m = dict(m)
            if m.get("provenance"):
                m["provenance"] = {
                    k: v for k, v in m["provenance"].items() if k not in _VOLATILE_PROVENANCE
                }
            metrics.append(m)
        case["metrics"] = metrics
        out.append(case)
    return out


def result_payload(resp: EvalResponse) -> dict[str, Any]:
    return _payload(resp.to_dict())


def _payload(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": d["schema_version"],
        "stage": d["stage"],
        "subject": d["subject"],
        "suite_hash": d["suite_hash"],
        "results": _strip_volatile(d["results"]),
        "aggregate": d["aggregate"],
        "verdict": d["verdict"],
        "metric_versions": d["provenance"]["metric_versions"],
        "judge": d["provenance"]["judge"],
    }


def compute_result_hash(resp: EvalResponse) -> str:
    return hash_obj(result_payload(resp))


def compute_response_hash(resp: EvalResponse) -> str:
    """Hash of the whole response except ``response_hash`` and ``attestation``."""
    return _envelope_hash(resp.to_dict())


def _envelope_hash(d: dict[str, Any]) -> str:
    d = {k: v for k, v in d.items() if k not in ("response_hash", "attestation")}
    return hash_obj(d)


def seal_hashes(resp: EvalResponse) -> tuple[str, str]:
    """``(result_hash, response_hash)`` for ``resp`` with one serialization (same values as
    :func:`compute_result_hash` followed by :func:`compute_response_hash`)."""
    d = resp.to_dict()
    result_hash = hash_obj(_payload(d))
    d["result_hash"] = result_hash
    return result_hash, _envelope_hash(d)


def verify_hashes(resp: EvalResponse) -> bool:
    return resp.result_hash == compute_result_hash(
        resp
    ) and resp.response_hash == compute_response_hash(resp)
