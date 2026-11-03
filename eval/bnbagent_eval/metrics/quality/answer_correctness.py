"""``quality.answer_correctness``: does the answer match the expected answer
(AgentSight AnswerCorrectnessEvaluator).

- ``native``: the source ``conceptual_match`` strategy (5-tier LLM judge of semantic equivalence).
- ``rule``: ``strategy`` = ``exact`` (strip, optionally lowercase, compare), ``rouge`` (ROUGE-L F1,
  stemmed), ``jaccard`` (whitespace token sets) or ``bertscore`` (the source ``semantic_match``:
  BERTScore F1 with ``bertscore_model``, default deberta-xlarge-mnli; needs ``[semantic]``; empty
  text scores 0). Each strategy keeps its source pass threshold (exact 1.0, rouge 0.7, jaccard 0.5,
  bertscore 0.8) unless the suite sets ``threshold``.
"""

from __future__ import annotations

from ...judges.prompts.quality import answer_correctness as prompts
from ...types.case import EvalCase
from ..base import MetricContext, MetricSpec, Outcome
from ..judge_base import judge_params, native_method
from ..registry import register
from ..text._common import best_of, output_text, references, rule_method
from ..text.bertscore import DEFAULT_MODEL as DEFAULT_BERTSCORE_MODEL
from ..text.bertscore import bertscore
from ..text.exact import _apply
from ..text.overlap import jaccard, rouge_prf
from ._common import QualityJudge

# Source defaults: exact match passes only on equality; rouge_threshold=0.7; jaccard_threshold=0.5.
STRATEGY_THRESHOLDS = {"exact": 1.0, "rouge": 0.7, "jaccard": 0.5, "bertscore": 0.8}


def strategy_score(
    strategy: str,
    actual: str,
    expected: str,
    language: str | None,
    bertscore_model: str = DEFAULT_BERTSCORE_MODEL,
) -> float:
    if strategy == "exact":
        return float(actual == expected)
    if strategy == "bertscore":  # AgentSight calculate_bertscore: empty text -> 0.0
        if not actual.strip() or not expected.strip():
            return 0.0
        return bertscore(actual, [expected], bertscore_model)[0][2]
    if strategy == "rouge":
        return rouge_prf(expected, actual, "rougeL", use_stemmer=True, language=language)[2]
    return jaccard(expected, actual, language=language)


@register
class AnswerCorrectness(QualityJudge):
    output_or_conversation = True  # AgentSight: actual output or conversation
    templates = prompts.VERSIONS
    spec = MetricSpec(
        id="quality.answer_correctness",
        category="quality",
        description="The answer conveys the same information as the expected answer.",
        methods=(
            native_method("output", "expected", optional=("input", "conversation")),
            rule_method("output", "expected"),
        ),
        params_schema=judge_params(
            {
                "strategy": {"enum": list(STRATEGY_THRESHOLDS)},
                "case_insensitive": {"type": "boolean"},
                "bertscore_model": {"type": "string", "minLength": 1, "maxLength": 200},
            }
        ),
        default_threshold=0.5,
        sources=("agentsight:AnswerCorrectnessEvaluator", "ragas:answer_accuracy"),
        # v2: strategy bertscore keeps case (AgentSight semantic_match)
        version="3",  # v3: a lone conversation message without output is judged as a conversation
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        strategy = ctx.params.get("strategy", "exact")
        # AgentSight ``case_insensitive`` applies to exact / rouge / jaccard; semantic_match
        # (bertscore) only strips, so a cased encoder sees the original text.
        lower = ctx.params.get("case_insensitive", True) and strategy != "bertscore"
        steps = ["case"] if lower else []
        actual = _apply(output_text(case), steps, ctx.language)
        refs = references(case.expected)
        model = str(ctx.params.get("bertscore_model", DEFAULT_BERTSCORE_MODEL))
        score, best = best_of(
            refs,
            lambda r: strategy_score(
                strategy, actual, _apply(r, steps, ctx.language), ctx.language, model
            ),
        )
        threshold = STRATEGY_THRESHOLDS[strategy]
        details = {"strategy": strategy, "strategy_threshold": threshold}
        if len(refs) > 1:
            details["best_reference"] = best
        return Outcome(
            score=score,
            reason=f"{strategy}: {score:.3f} (threshold: {threshold})",
            details=details,
            threshold=threshold,  # strategy default; an explicit suite threshold wins
        )
