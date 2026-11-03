"""Shared bases for safety judges: AgentSight severity labels and parameterized prompts."""

from __future__ import annotations

from typing import ClassVar

from ...types.case import EvalCase
from ..base import MetricContext
from ..conversation._common import render_prompt_params
from ..judge_base import JudgeMetric


class SeverityJudge(JudgeMetric):
    """AgentSight safety judges: the reply rates ``severity`` (not ``score``); input not shown."""

    label_field: ClassVar[str] = "severity"
    include_input = False  # AgentSight build_prompt(include_input=False)


class ParamJudge(JudgeMetric):
    """5-tier judge whose prompt also renders metric params (``{domain}``, ``{role}``, ...)."""

    prompt_params: ClassVar[tuple[str, ...]] = ()

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        return render_prompt_params(super().fields(case, ctx, mode), ctx, self.prompt_params)
