"""Shared behaviour of the AgentSight-style quality judges."""

from __future__ import annotations

from typing import ClassVar

from ...judges.prompts import PromptTemplate
from ...types.case import EvalCase
from ..base import MetricContext
from ..judge_base import JudgeMetric

# AgentSight ``build_prompt``: when the case has an input, the simple / with_expected modes use
# their ``with_input`` variants (where the template defines them).
INPUT_VARIANTS = {"simple": "with_input", "with_expected": "with_input_and_expected"}


class QualityJudge(JudgeMetric):
    """JudgeMetric with AgentSight input variants and a configurable label field."""

    label_field: ClassVar[str] = "score"  # judge reply field holding the categorical rating

    def select_mode(self, case: EvalCase, ctx: MetricContext, template: PromptTemplate) -> str:
        mode = super().select_mode(case, ctx, template)
        variant = INPUT_VARIANTS.get(mode)
        if self.include_input and variant in template.parts and case.has("input"):
            return variant
        return mode


def merge_provenance(*provs: dict) -> dict:
    """Combine the provenance of several judge calls of one metric (first call's prompt hash)."""
    first = dict(provs[0])
    sources = {p["judge_source"] for p in provs}
    first["judge_source"] = sources.pop() if len(sources) == 1 else "mixed"
    first["truncated"] = any(p["truncated"] for p in provs)
    return first
