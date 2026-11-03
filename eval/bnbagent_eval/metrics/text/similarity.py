"""``text.embedding_similarity``: semantic relevance of output and reference (or input).

``keyword`` = AgentSight ``SemanticRelevanceScorer`` keyword mode (``_keyword_score``: Jaccard of
punctuation-stripped, stop-word-filtered words of >= 2 characters; 0.0 if either side has none).
Embedding-model methods (judge ``embed``, GloVe, FastText) are in ``vendors/local_models.py``.
"""

from __future__ import annotations

from ...types.case import EvalCase, content_text
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register
from ..tokens import keyword_jaccard, semantic_keywords
from ._common import as_text, output_text, references


@register
class EmbeddingSimilarity(BaseMetric):
    spec = MetricSpec(
        id="text.embedding_similarity",
        category="text",
        description="Semantic similarity of output and reference (or input, if no reference).",
        methods=(
            MethodSpec(
                "keyword",
                Kind.RULE,
                required_inputs=frozenset({"output"}),
                optional_inputs=frozenset({"expected", "input"}),
            ),
        ),
        params_schema={
            "type": "object",
            "properties": {"against": {"enum": ["expected", "input"]}},
            "additionalProperties": False,
        },
        default_threshold=0.5,
        sources=("agentsight:SemanticRelevanceScorer",),
    )

    def _evaluate_keyword(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        against = ctx.params.get("against", "expected" if case.has("expected") else "input")
        if against == "expected":
            if not case.has("expected"):
                raise ValueError("against=expected but the case has no expected value")
            refs = references(case.expected)
        else:
            refs = [content_text(case.input) or as_text(case.input)]
        out = output_text(case)
        lang = ctx.language
        keys = semantic_keywords(out, lang)
        score = max(keyword_jaccard(keys, semantic_keywords(r, lang)) for r in refs)
        return Outcome(score=score, details={"against": against})
