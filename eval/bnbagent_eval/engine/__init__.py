"""Engine (L3): resolution, running, aggregation, verdicts, sealing."""

from __future__ import annotations

from .compare import CombinedEvaluation, Comparison, aggregate_evaluations, compare
from .resolve import ResolvedSuite, build_lock, compute_suite_hash, resolve_suite
from .runner import Runner
from .seal import compute_response_hash, compute_result_hash, verify_hashes

__all__ = [
    "CombinedEvaluation",
    "Comparison",
    "aggregate_evaluations",
    "compare",
    "ResolvedSuite",
    "Runner",
    "build_lock",
    "compute_response_hash",
    "compute_result_hash",
    "compute_suite_hash",
    "resolve_suite",
    "verify_hashes",
]
