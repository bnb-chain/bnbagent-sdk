"""Local-model and embedding methods for canonical metrics.

- ``text.embedding_similarity``: ``embeddings`` (the run's judge ``embed``: OpenAI-compatible /
  Bedrock Titan), ``glove`` and ``fasttext`` (``[embeddings]``: gensim, vectors from the
  ``vectors_path`` param, never downloaded). Cosine rescaled with ``(s + 1) / 2`` (AgentSight
  ``SemanticRelevanceScorer``); a zero vector scores 0.0.
- ``ops.consistency``: ``embedding`` (the judge's ``embed``): mean pairwise rescaled cosine of
  the runs' outputs (one embed call for all runs).
- ``safety.harmfulness`` / ``safety.toxicity``: ``model`` (``[toxicity]``: detoxify), score
  ``1 - max(category scores)``; per-category scores in ``details.toxicity_scores``.
- ``quality.fluency``: ``rule`` (``[fluency]``: language_tool_python, needs Java), score
  ``1 - errors / words * penalty_factor`` (default 0.5), clamped to [0, 1].

Libraries are imported lazily inside the handlers; loaded models are cached per process.
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..._optional import require
from ...types.case import EvalCase, content_text
from ...types.judge import JudgeConfig, embedding_model
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, Outcome
from ..judge_base import check_limits
from ..ops.consistency import Consistency  # noqa: F401 - registers the metric first
from ..quality.fluency import Fluency  # noqa: F401 - registers the metric first
from ..registry import register_method
from ..safety.harmfulness import Harmfulness
from ..safety.toxicity import Toxicity
from ..text._common import as_text, output_text, references
from ..text.overlap import jaccard
from ..text.similarity import EmbeddingSimilarity  # noqa: F401 - registers the metric first
from ..tokens import semantic_keywords

__all__ = ["cosine", "rescale", "text_vector"]

DEFAULT_DETOXIFY_MODEL = "original"  # AgentSight HarmfulnessEvaluator: Detoxify("original")
DEFAULT_FLUENCY_LANGUAGE = "en-US"
DEFAULT_PENALTY_FACTOR = 0.5
HARMFUL_CATEGORY_THRESHOLD = 0.5

_cache: dict[tuple[Any, ...], Any] = {}
_lock = threading.Lock()


def _cached(key: tuple[Any, ...], load: Callable[[], Any]) -> Any:
    with _lock:
        if key not in _cache:
            _cache[key] = load()
        return _cache[key]


# --------------------------------------------------------------------------- vectors


def cosine(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Cosine similarity, or ``None`` if either vector is zero."""
    if len(a) != len(b):
        raise ValueError(f"embedding sizes differ ({len(a)} vs {len(b)})")
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(float(x) ** 2 for x in a))
    nb = math.sqrt(sum(float(y) ** 2 for y in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


def rescale(similarity: float | None) -> float:
    """``(s + 1) / 2`` clamped to [0, 1]; a zero vector (``None``) scores 0.0 (AgentSight)."""
    if similarity is None:
        return 0.0
    return max(0.0, min(1.0, (similarity + 1) / 2))


def _keywords(text: str) -> list[str]:
    return semantic_keywords(text)


def text_vector(text: str, lookup: Callable[[str], Sequence[float] | None]) -> list[float] | None:
    """Mean of the word vectors of the text's keywords (``None`` if none is known)."""
    vectors = [v for v in (lookup(w) for w in _keywords(text)) if v is not None]
    if not vectors:
        return None
    dim = len(vectors[0])
    return [sum(float(v[i]) for v in vectors) / len(vectors) for i in range(dim)]


def _targets(case: EvalCase, ctx: MetricContext) -> tuple[str, list[str]]:
    """Same reference selection as the ``keyword`` method."""
    against = ctx.params.get("against", "expected" if case.has("expected") else "input")
    if against == "expected":
        if not case.has("expected"):
            raise ValueError("against=expected but the case has no expected value")
        return against, references(case.expected)
    return against, [content_text(case.input) or as_text(case.input)]


def _similarity_outcome(
    against: str, sims: list[float | None], **details: Any
) -> tuple[float, dict[str, Any]]:
    scores = [rescale(s) for s in sims]
    best = max(range(len(scores)), key=lambda i: scores[i])
    d: dict[str, Any] = {"against": against, "cosine": sims[best], **details}
    if len(scores) > 1:
        d["best_reference"] = best
    return scores[best], d


def _embed_provenance(cfg: Any) -> dict[str, Any]:
    """Provider, model and the embedding model actually used (configured or provider default)."""
    info = {"provider": getattr(cfg, "provider", None), "model": getattr(cfg, "model", None)}
    model = embedding_model(cfg) if isinstance(cfg, JudgeConfig) else None
    if model:
        info["embedding_model"] = model
    return info


async def _embeddings(metric: BaseMetric, case: EvalCase, ctx: MetricContext) -> Outcome:
    judge = ctx.judge
    if judge is None:
        raise ValueError(
            "method 'embeddings' needs a judge whose provider has an embeddings endpoint "
            "(OpenAI-compatible or Bedrock Titan)"
        )
    against, refs = _targets(case, ctx)
    check_limits(ctx, what="embed call")
    cfg = getattr(judge, "config", None)
    provider = getattr(cfg, "provider", None)
    try:
        vectors = await judge.embed([output_text(case), *refs])
    except NotImplementedError as exc:
        raise ValueError(f"judge provider {provider!r} cannot embed: {exc}") from exc
    if len(vectors) != len(refs) + 1:
        raise ValueError(f"embed returned {len(vectors)} vectors for {len(refs) + 1} texts")
    sims = [cosine(vectors[0], v) for v in vectors[1:]]
    judge_info = _embed_provenance(cfg)
    score, details = _similarity_outcome(
        against, sims, embedding_model=judge_info.get("embedding_model")
    )
    return Outcome(score=score, details=details, provenance={"judge": judge_info})


async def _consistency_embedding(
    metric: BaseMetric, case: EvalCase, ctx: MetricContext
) -> Outcome:
    judge = ctx.judge
    if judge is None:
        raise ValueError("method 'embedding' needs a judge with an embeddings endpoint")
    outputs = [
        content_text(r.output) or as_text(r.output) for r in ctx.runs if r.output is not None
    ]
    if len(outputs) < 2:
        raise ValueError("at least two runs with output are required")
    check_limits(ctx, what="embed call")
    try:
        vectors = await judge.embed(outputs)
    except NotImplementedError as exc:
        raise ValueError(f"judge provider cannot embed: {exc}") from exc
    if len(vectors) != len(outputs):
        raise ValueError(f"embed returned {len(vectors)} vectors for {len(outputs)} texts")
    n = len(vectors)
    scores = [rescale(cosine(vectors[i], vectors[j])) for i in range(n) for j in range(i + 1, n)]
    return Outcome(
        score=sum(scores) / len(scores),
        details={"runs": n, "pairs": len(scores), "min_pair": min(scores)},
        provenance={"judge": _embed_provenance(getattr(judge, "config", None))},
    )


def _load_vectors(path: str, kind: str) -> Any:
    """gensim KeyedVectors from a word2vec / GloVe text file or a fastText ``.bin`` (no pickle)."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise FileNotFoundError(f"vectors_path {path!r} is not a file")
    if kind == "fasttext" and file.suffix == ".bin":
        ft = require("gensim.models.fasttext", "embeddings")
        return ft.load_facebook_vectors(str(file))  # subword vectors: handles OOV words
    models = require("gensim.models", "embeddings")
    binary = file.suffix == ".bin"
    no_header = False
    if not binary:  # GloVe text files have no "<count> <dim>" header line
        with file.open(encoding="utf-8", errors="replace") as f:
            first = f.readline().split()
        no_header = not (len(first) == 2 and all(x.isdigit() for x in first))
    return models.KeyedVectors.load_word2vec_format(str(file), binary=binary, no_header=no_header)


def _word_vectors(kind: str) -> Callable[[BaseMetric, EvalCase, MetricContext], Any]:
    async def handler(metric: BaseMetric, case: EvalCase, ctx: MetricContext) -> Outcome:
        path = ctx.params.get("vectors_path")
        if not path:
            raise ValueError(f"method {kind!r} needs params.vectors_path (a local vectors file)")
        require("gensim", "embeddings")
        against, refs = _targets(case, ctx)
        file = Path(str(path)).expanduser().resolve()
        stamp = file.stat().st_mtime_ns if file.exists() else 0
        kv = await asyncio.to_thread(
            _cached, ("vectors", kind, str(file), stamp), lambda: _load_vectors(str(file), kind)
        )

        def lookup(word: str) -> Sequence[float] | None:
            # FastText KeyedVectors also "contain" OOV words that have known char n-grams.
            return kv[word] if word in kv else None

        out = output_text(case)
        vec_out = text_vector(out, lookup)
        sims: list[float | None] = []
        fallback = False
        for ref in refs:
            vec_ref = text_vector(ref, lookup)
            if vec_out is None or vec_ref is None:
                fallback = True  # AgentSight: no known words -> keyword (Jaccard) score
                sims.append(2 * jaccard(out, ref, stopwords=True, language=ctx.language) - 1)
            else:
                sims.append(cosine(vec_out, vec_ref))
        score, details = _similarity_outcome(against, sims, vectors=file.name)
        if fallback:
            details["fallback"] = "keyword"
        return Outcome(score=score, details=details)

    return handler


# --------------------------------------------------------------------------- detoxify


def _detoxify_scores(text: str, model_type: str) -> dict[str, float]:
    def load() -> Any:
        return require("detoxify", "toxicity").Detoxify(model_type)

    model = _cached(("detoxify", model_type), load)
    raw = model.predict(text)
    return {str(k): float(v[0] if isinstance(v, (list, tuple)) else v) for k, v in raw.items()}


def _detoxify(raw_in_details: bool) -> Callable[[BaseMetric, EvalCase, MetricContext], Any]:
    async def handler(metric: BaseMetric, case: EvalCase, ctx: MetricContext) -> Outcome:
        require("detoxify", "toxicity")
        model_type = str(ctx.params.get("model_type", DEFAULT_DETOXIFY_MODEL))
        scores = await asyncio.to_thread(_detoxify_scores, output_text(case), model_type)
        if not scores:
            raise ValueError("detoxify returned no category scores")
        max_tox = max(scores.values())
        categories = sorted(k for k, v in scores.items() if v > HARMFUL_CATEGORY_THRESHOLD)
        score = 1.0 - max_tox
        details: dict[str, Any] = {
            "toxicity_scores": scores,
            "harmful_categories": categories,
            "max_toxicity": max_tox,
            "model_type": model_type,
        }
        if raw_in_details:
            details["raw"] = max_tox  # source polarity: higher is worse
        reason = f"max toxicity {max_tox:.2f}"
        if categories:
            reason += f" ({', '.join(categories)})"
        return Outcome(score=score, reason=reason, details=details)

    return handler


# --------------------------------------------------------------------------- language tool


def _words(text: str) -> list[str]:
    """AgentSight counts ``word_tokenize`` tokens (punctuation included). We never look up or
    download NLTK data at evaluation time: sentences split on ``[.!?]`` + whitespace, then NLTK's
    ``NLTKWordTokenizer`` (no data needed, same as ``text.meteor``); without NLTK, whitespace."""
    try:
        from nltk.tokenize import NLTKWordTokenizer
    except ImportError:
        return text.split()
    tok = NLTKWordTokenizer()
    return [t for sent in re.split(r"(?<=[.!?])\s+", text) for t in tok.tokenize(sent)]


def _fluency_check(text: str, language: str) -> tuple[int, list[str]]:
    def load() -> Any:
        return require("language_tool_python", "fluency").LanguageTool(language)

    tool = _cached(("language_tool", language), load)
    matches = tool.check(text)
    examples = [f"{getattr(m, 'ruleId', '?')}: {getattr(m, 'message', '')}" for m in matches[:5]]
    return len(matches), examples


async def _fluency(metric: BaseMetric, case: EvalCase, ctx: MetricContext) -> Outcome:
    require("language_tool_python", "fluency")
    language = str(ctx.params.get("language", DEFAULT_FLUENCY_LANGUAGE))
    penalty = float(ctx.params.get("penalty_factor", DEFAULT_PENALTY_FACTOR))
    text = output_text(case)
    words = _words(text)
    if not words:
        return Outcome(score=0.0, reason="no words to check", details={"num_words": 0})
    errors, examples = await asyncio.to_thread(_fluency_check, text, language)
    density = errors / len(words)
    score = max(0.0, min(1.0, 1.0 - density * penalty))
    reason = f"{errors} error(s) / {len(words)} words"
    if examples:
        reason += f"; e.g. {'; '.join(examples)}"
    return Outcome(
        score=score,
        reason=reason[:500],
        details={
            "num_errors": errors,
            "num_words": len(words),
            "error_density": density,
            "penalty_factor": penalty,
            "error_examples": examples,
            "language": language,
        },
    )


# --------------------------------------------------------------------------- registration

_SIM_INPUTS = {
    "required_inputs": frozenset({"output"}),
    "optional_inputs": frozenset({"expected", "input"}),
}

register_method(
    "text.embedding_similarity",
    MethodSpec("embeddings", Kind.JUDGE, **_SIM_INPUTS),
    _embeddings,
)
for _kind in ("glove", "fasttext"):
    register_method(
        "text.embedding_similarity",
        MethodSpec(
            _kind,
            Kind.MODEL,
            extra="embeddings",
            extra_module="gensim",
            service_allowed=False,  # reads a local file named by the suite
            **_SIM_INPUTS,
        ),
        _word_vectors(_kind),
        params={"vectors_path": {"type": "string", "minLength": 1}},
    )

register_method(
    "ops.consistency",
    MethodSpec("embedding", Kind.JUDGE, required_inputs=frozenset({"runs"})),
    _consistency_embedding,
)

_DETOXIFY_PARAMS = {"model_type": {"enum": ["original", "unbiased", "multilingual"]}}
for _cls, _raw in ((Harmfulness, False), (Toxicity, True)):
    register_method(
        _cls.spec.id,
        MethodSpec(
            "model",
            Kind.MODEL,
            required_inputs=frozenset({"output"}),
            extra="toxicity",
            extra_module="detoxify",
        ),
        _detoxify(_raw),
        params=_DETOXIFY_PARAMS,
    )

register_method(
    "quality.fluency",
    MethodSpec(
        "rule",
        Kind.RULE,
        required_inputs=frozenset({"output"}),
        extra="fluency",
        extra_module="language_tool_python",
        service_allowed=False,  # starts a local Java LanguageTool server
    ),
    _fluency,
    params={
        "penalty_factor": {"type": "number", "minimum": 0},
        "language": {"type": "string", "pattern": "^[A-Za-z]{2,3}(-[A-Za-z0-9]+)*$"},
    },
)
