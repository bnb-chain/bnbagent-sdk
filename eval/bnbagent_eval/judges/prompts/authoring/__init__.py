"""Prompt templates of the eval maker (``authoring.eval_maker``).

One module per stage (assess, strategy, match, generate, reflect). Every template renders the
``simple`` mode only. Untrusted text (the description, sample cases and anything the judge
derived from them) arrives fenced; the metric catalogue, stage guidance and lint findings are
trusted and arrive unfenced.
"""

from __future__ import annotations

# Stage guidance (bnbagent-eval:new); negotiated stages vs development.
NEGOTIATED_GUIDANCE = (
    "This suite will be used at the {stage!r} stage of an on-chain job: the parties negotiate "
    "it and an arbiter may replay it in a dispute. Prefer deterministic (rule) metrics wherever "
    "they measure a quality, because they replay exactly. Use judge metrics only for qualities "
    "no rule can measure, with specific, checkable criteria. Mark the metrics that must pass as "
    "required and give each a threshold."
)
PRE_SUBMIT_GUIDANCE = (
    " At pre_submit the provider checks its own deliverable before submitting it, so cheap, "
    "deterministic checks of the deliverable's format and content come first."
)
DEV_GUIDANCE = (
    "This suite is for development: favour coverage of the stated qualities; judge metrics are "
    "fine where they fit best."
)


def stage_guidance(stage: str | None) -> str:
    """The stage paragraph that the strategy, match, generate and reflect prompts include."""
    if stage in (None, "dev"):
        return DEV_GUIDANCE
    text = NEGOTIATED_GUIDANCE.format(stage=stage)
    return text + PRE_SUBMIT_GUIDANCE if stage == "pre_submit" else text
