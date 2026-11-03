"""Classification accuracy (FMEval classification_accuracy) and output length checks."""

from __future__ import annotations

from ...types.case import EvalCase
from ..base import BaseMetric, MetricContext, MetricSpec, Outcome
from ..registry import register
from ..tokens import normalize
from ._common import output_text, references, rule_method

UNKNOWN_LABEL = "unknown"


def output_label(model_output: str, valid_labels: list[str]) -> str:
    """FMEval ``convert_model_output_to_label``: the first space-separated word of the output that
    is a valid label (lowercased, stripped), else ``"unknown"`` ("The answer is 2" -> "2")."""
    valid = [v.lower().strip() for v in valid_labels]
    words = [w.lower().strip() for w in model_output.split(" ")]
    return next((w for w in words if w in valid), UNKNOWN_LABEL)


@register
class ClassificationAccuracy(BaseMetric):
    spec = MetricSpec(
        id="text.classification_accuracy",
        category="text",
        description="Predicted label equals the expected label (after label_map, normalization). "
        "Batch accuracy is the metric's mean.",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {
                "label_map": {"type": "object", "additionalProperties": {"type": "string"}},
                "normalize": {"type": "boolean"},
                "valid_labels": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        default_threshold=1.0,
        version="2",  # v2: blank <OR> alternatives dropped; articles removed in mixed CJK text
        sources=("fmeval:classification_accuracy",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        label_map = ctx.params.get("label_map", {})
        do_norm = ctx.params.get("normalize", True)

        def label(text: str) -> str:
            text = label_map.get(text.strip(), text.strip())
            return normalize(text, ctx.language) if do_norm else text

        out = output_text(case)
        if ctx.params.get("valid_labels"):
            out = output_label(out, ctx.params["valid_labels"])
        predicted = label(out)
        expected = [label(r) for r in references(case.expected)]
        return Outcome(
            score=float(predicted in expected),
            details={"predicted": predicted, "expected": expected[0]},
        )


@register
class Length(BaseMetric):
    spec = MetricSpec(
        id="text.length",
        category="text",
        description="Output length is within the configured character / word bounds.",
        methods=(rule_method("output"),),
        params_schema={
            "type": "object",
            "properties": {
                k: {"type": "integer", "minimum": 0}
                for k in ("min_chars", "max_chars", "min_words", "max_words")
            },
            "additionalProperties": False,
            # at least one bound: without one the metric always passes
            "anyOf": [
                {"required": [k]} for k in ("min_chars", "max_chars", "min_words", "max_words")
            ],
            "errorMessage": "set at least one of min_chars, max_chars, min_words, max_words",
        },
        default_threshold=1.0,
        unit="chars",
        sources=("etk:ExampleTextQualityScorer.length",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        text = output_text(case)
        chars, words = len(text), len(text.split())
        # No bounds: ETK ``length`` (non-strict) scores 1 only for non-empty output.
        p = ctx.params or {"min_chars": 1}
        violations = [
            name
            for name, bad in (
                ("min_chars", "min_chars" in p and chars < p["min_chars"]),
                ("max_chars", "max_chars" in p and chars > p["max_chars"]),
                ("min_words", "min_words" in p and words < p["min_words"]),
                ("max_words", "max_words" in p and words > p["max_words"]),
            )
            if bad
        ]
        return Outcome(
            score=0.0 if violations else 1.0,
            value=float(chars),
            reason=("violates " + ", ".join(violations)) if violations else None,
            details={"chars": chars, "words": words},
        )
