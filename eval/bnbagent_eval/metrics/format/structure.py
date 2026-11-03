"""``format.structure``: the output follows a structure described in natural language.

Native method = AgentSight OutputStructureComplianceEvaluator LLM mode
(``prioritize_efficiency=False``, validation type ``description``) with the 5-tier labels.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...exceptions import SuiteError
from ...judges.prompts import PromptTemplate
from ...judges.prompts.format import structure as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import JudgeMetric, judge_params, native_method
from ..registry import register

NATIVE_THRESHOLD = 0.5


class StructureJudge(JudgeMetric):
    """AgentSight OutputStructureCompliance LLM mode (``prioritize_efficiency=False``).

    Subclasses set ``validation_type`` and return the structure specification from params.
    """

    templates = prompts.VERSIONS
    validation_type: ClassVar[str] = "description"

    def structure_spec(self, params: Mapping[str, Any]) -> str:
        raise NotImplementedError

    def template_for(self, params: Mapping[str, Any]) -> PromptTemplate:
        return prompts.with_structure(
            super().template_for(params), self.structure_spec(params), self.validation_type
        )

    def fields(self, case: EvalCase, ctx: MetricContext, mode: str) -> dict[str, str]:
        out = super().fields(case, ctx, mode)
        if mode == "conversation":  # AgentSight: all assistant turns, space-joined
            out["actual_output"] = " ".join(
                m.content for m in case.conversation if m.role == "assistant"
            )
        return out

    async def _evaluate_native(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        outcome = await super()._evaluate_native(case, ctx)
        return Outcome(
            score=outcome.score,
            reason=outcome.reason,
            details={**outcome.details, "validation_type": self.validation_type},
            provenance=outcome.provenance,
            # AgentSight OutputStructureComplianceEvaluator.DEFAULT_THRESHOLD; the rule methods of
            # format.json_schema / regex / xml keep their 1.0 (valid or not)
            threshold=NATIVE_THRESHOLD,
        )


@register
class Structure(StructureJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    spec = MetricSpec(
        id="format.structure",
        category="format",
        description="The output complies with a natural-language structure description.",
        methods=(native_method("output", optional=("conversation",)),),
        params_schema={
            **judge_params({"expected_structure": {"type": "string", "minLength": 1}}),
            "required": ["expected_structure"],
        },
        default_threshold=0.5,
        version="2",  # v2: a lone conversation message without output is judged as a conversation
        sources=("agentsight:OutputStructureComplianceEvaluator(llm)",),
    )

    def structure_spec(self, params: Mapping[str, Any]) -> str:
        structure = params.get("expected_structure")
        if not structure:
            raise SuiteError("format.structure requires the expected_structure param")
        return structure
