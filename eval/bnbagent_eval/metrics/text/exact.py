"""Exact, quasi-exact and inclusion matching (FMEval qa_accuracy / factual_knowledge, DeepEval
exact_match, AgentSight AnswerCorrectness exact strategy)."""

from __future__ import annotations

from ...types.case import EvalCase
from ..base import BaseMetric, MetricContext, MetricSpec, Outcome
from ..registry import register
from ..tokens import normalize, remove_articles, strip_punctuation
from ._common import best_of, output_text, ref_outcome, references, rule_method

_NORMALIZE_OPTIONS = ["case", "whitespace", "punctuation", "articles"]


def _apply(text: str, steps: list[str], language: str | None) -> str:
    """Apply normalization steps in a fixed order: case, punctuation, articles, whitespace."""
    out = text
    if "case" in steps:
        out = out.lower()
    if "punctuation" in steps:
        out = strip_punctuation(out)
    if "articles" in steps:
        out = remove_articles(out, language)
    if "whitespace" in steps:
        out = " ".join(out.split())
    return out.strip()


@register
class ExactMatch(BaseMetric):
    spec = MetricSpec(
        id="text.exact_match",
        category="text",
        description="Output equals the reference (any of several), optionally normalized.",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {
                "normalize": {
                    "anyOf": [
                        {"type": "boolean"},
                        {"type": "array", "items": {"enum": _NORMALIZE_OPTIONS}},
                    ]
                },
                "case_insensitive": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        aliases=("deepeval.exact_match",),
        version="2",  # v2: blank <OR> alternatives dropped; articles removed in mixed CJK text
        sources=(
            "fmeval:qa_accuracy.exact_match",
            "deepeval:exact_match",
            "agentsight:AnswerCorrectness[exact]",
            "etk:example_exact_match",
        ),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        norm = ctx.params.get("normalize", False)
        steps = list(_NORMALIZE_OPTIONS) if norm is True else list(norm or [])
        if ctx.params.get("case_insensitive") and "case" not in steps:
            steps.append("case")
        out = _apply(output_text(case), steps, ctx.language)
        refs = references(case.expected)
        score, best = best_of(refs, lambda r: float(_apply(r, steps, ctx.language) == out))
        return ref_outcome(score, refs, best)


@register
class QuasiExactMatch(BaseMetric):
    spec = MetricSpec(
        id="text.quasi_exact_match",
        category="text",
        description="Exact match after QuAC normalization (case, punctuation, articles, spaces).",
        methods=(rule_method("output", "expected"),),
        params_schema={"type": "object", "additionalProperties": False},
        default_threshold=1.0,
        version="2",  # v2: blank <OR> alternatives dropped; articles removed in mixed CJK text
        sources=("fmeval:qa_accuracy.quasi_exact_match",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out = normalize(output_text(case), ctx.language)
        refs = references(case.expected)
        score, best = best_of(refs, lambda r: float(normalize(r, ctx.language) == out))
        return ref_outcome(score, refs, best)


@register
class Contains(BaseMetric):
    spec = MetricSpec(
        id="text.contains",
        category="text",
        description="The output contains the target(s): any (FMEval factual knowledge) or all.",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {
                "mode": {"enum": ["any", "all"]},
                "delimiter": {"type": "string", "minLength": 1},
                "quasi": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        version="2",  # v2: a blank target (e.g. a trailing <OR>) does not match every output
        sources=("fmeval:factual_knowledge",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        quasi = ctx.params.get("quasi", False)

        def prep(t: str) -> str:
            return normalize(t, ctx.language) if quasi else t.lower()

        out = prep(output_text(case))
        targets = references(case.expected, ctx.params.get("delimiter", "<OR>"))
        hits = [prep(t) in out for t in targets]
        mode = ctx.params.get("mode", "any")
        score = float(all(hits) if mode == "all" else any(hits))
        return Outcome(score=score, details={"hits": sum(hits), "targets": len(targets)})
