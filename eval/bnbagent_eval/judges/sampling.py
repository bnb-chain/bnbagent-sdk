"""Aggregation of k judge samples: lower median, deterministic tie-break."""

from __future__ import annotations

from collections.abc import Sequence


def median_low(values: Sequence[float]) -> float:
    """The lower median (for an even count, the lower of the two middle values)."""
    if not values:
        raise ValueError("no samples")
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def majority(labels: Sequence[str], scores: dict[str, float]) -> str:
    """Most frequent label; ties go to the lower-scoring label."""
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return min(counts, key=lambda lab: (-counts[lab], scores.get(lab, 0.0), lab))
