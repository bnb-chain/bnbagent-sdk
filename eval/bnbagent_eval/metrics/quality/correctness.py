"""``quality.correctness``: is the response itself correct (facts, reasoning, numbers)?

Reference-free: ``expected``, when present, is one correct answer the judge may use, but the
response is not required to match it (that is ``quality.answer_correctness``). Methods:

- ``native`` (default): our 5-tier judge prompt (``judges/prompts/quality/correctness.py``);
- ``agentcore`` (third-party, ``[agentcore]``): AWS AgentCore ``Builtin.Correctness`` over the
  trajectory's session spans; params ``region``, ``agentcore_evaluator_id``, ``level`` (see
  :mod:`bnbagent_eval.metrics.vendors.agentcore`).
"""

from __future__ import annotations

from ...judges.prompts.quality import correctness as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import judge_params, native_method
from ..registry import register
from ..vendors._agentcore_spans import AGENTCORE_PARAMS, agentcore_method, evaluate
from ._common import QualityJudge


@register
class Correctness(QualityJudge):
    output_or_conversation = True
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.correctness",
        category="quality",
        description="The response is correct: true facts, sound reasoning, right numbers (judge).",
        methods=(
            native_method("output", optional=("input", "expected", "conversation")),
            agentcore_method(),
        ),
        params_schema=judge_params(dict(AGENTCORE_PARAMS)),
        default_threshold=0.5,
        version="3",  # v3: a lone conversation message without output is judged as a conversation
        sources=("bnbagent-eval:new", "agentcore:Builtin.Correctness"),
    )

    async def _evaluate_agentcore(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        return await evaluate(case, ctx, "Builtin.Correctness")
