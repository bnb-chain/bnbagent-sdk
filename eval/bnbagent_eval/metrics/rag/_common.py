"""Helpers shared by judge metrics whose replies use source field names (rag.*, custom.*)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...judges.sampling import median_low
from ...types.case import EvalCase
from ..base import Outcome
from ..judge_base import Sample


def agentsight_contexts(case: EvalCase) -> str:
    """AgentSight ContextQuality format: ``**Context {id}:**\\n{text}\\n`` blocks."""
    return "\n".join(f"**Context {i}:**\n{c.text}\n" for i, c in enumerate(case.contexts, 1))


def combine(
    samples: Sequence[Sample],
    scored: Sequence[tuple[float, Mapping[str, Any]]],
    provenance: Mapping[str, Any],
    *,
    reason_key: str = "reasoning",
    label_key: str | None = None,
    higher_is_worse: bool = False,
    **common: Any,
) -> Outcome:
    """Lower-median sample wins; its raw reply fields and per-sample details go to ``details``.

    ``scored`` holds ``(score, details)`` per sample, computed from that sample's reply.
    """
    scores = [s for s, _ in scored]
    # Always aggregate towards the worse result: the lower median of a higher-is-better value,
    # the upper median of a raw value that the method inverts (higher is worse).
    score = sorted(scores)[len(scores) // 2] if higher_is_worse else median_low(scores)
    i = scores.index(score)
    chosen = samples[i].parsed
    skip = {reason_key, label_key}
    details: dict[str, Any] = {k: v for k, v in chosen.items() if k not in skip}
    details.update(scored[i][1])
    details.update(common)
    if label_key is not None and label_key in chosen:
        details["label"] = chosen[label_key]
    if len(samples) > 1:
        details["sample_scores"] = scores
    return Outcome(
        score=score, reason=chosen.get(reason_key), details=details, provenance=provenance
    )


def share(flags: Sequence[bool]) -> float:
    return sum(1 for f in flags if f) / len(flags) if flags else 0.0
