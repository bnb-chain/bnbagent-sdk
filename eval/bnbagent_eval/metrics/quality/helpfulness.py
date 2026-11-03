"""``quality.helpfulness``: does the response move the user towards their goal (AgentSight
HelpfulnessEvaluator)."""

from __future__ import annotations

import dataclasses

from ...judges.prompts.quality import helpfulness as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import judge_params, native_method
from ..registry import register
from ._common import QualityJudge


@register
class Helpfulness(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.helpfulness",
        category="quality",
        description="The response helps the user progress towards their goal (5-tier judge).",
        methods=(native_method("output", optional=("input", "expected", "conversation")),),
        params_schema=judge_params(
            {
                "include_inputs": {"type": "boolean"},
                "agent_id": {"type": "string"},  # multi-agent traces; recorded in details only
            }
        ),
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:HelpfulnessEvaluator", "agentcore:Builtin.Helpfulness"),
    )

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = super().fields(case, ctx, mode)
        if not ctx.params.get("include_inputs", True):
            out["input"] = ""
        return out

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out = await super()._evaluate_native(case, ctx)
        if "agent_id" not in ctx.params:
            return out
        return dataclasses.replace(
            out, details={**out.details, "agent_id": ctx.params["agent_id"]}
        )
