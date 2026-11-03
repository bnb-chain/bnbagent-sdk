"""Token-overlap metrics: token F1, ROUGE, BLEU, METEOR, Jaccard, TF-IDF cosine.

Our implementations (no NLTK / rouge-score at run time) follow the reference definitions (FMEval,
rouge-score, NLTK, scikit-learn defaults); ``tests/parity`` checks them against those libraries.
ROUGE stems with the original Porter algorithm (as rouge-score), METEOR with NLTK's variant and
Penn Treebank tokens (as FMEval). When either text of a pair contains CJK characters, both
are tokenized per character (the same tokenizer on both sides of a comparison).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from ...analysis.confidence import extract_keywords
from ...types.case import EvalCase
from ..base import BaseMetric, MetricContext, MetricSpec, Outcome
from ..registry import register
from ..tokens import (
    STOPWORDS,
    has_cjk,
    keyword_jaccard,
    normalized_tokens,
    stemmer,
    tokenize,
    treebank_tokenize,
)
from ._common import best_of, output_text, ref_outcome, references, rule_method

# --------------------------------------------------------------------------- token F1


def token_prf(
    prediction: str, reference: str, language: str | None = None
) -> tuple[float, float, float]:
    """Precision, recall, F1 over normalized token *sets* (FMEval ``qa_accuracy._split``).

    FMEval compares ``set(text.split())`` with NLTK's ``precision`` / ``recall`` / ``f_measure``,
    so repeated words count once and two empty texts score 0.0 (NLTK returns ``None``).
    """
    pred = set(normalized_tokens(prediction, language))
    ref = set(normalized_tokens(reference, language))
    overlap = len(pred & ref)
    if overlap == 0:
        return 0.0, 0.0, 0.0
    p, r = overlap / len(pred), overlap / len(ref)
    return p, r, 2 * p * r / (p + r)


@register
class TokenF1(BaseMetric):
    spec = MetricSpec(
        id="text.token_f1",
        category="text",
        description="F1 of normalized token overlap with the reference (+ precision, recall).",
        methods=(rule_method("output", "expected"),),
        params_schema={"type": "object", "additionalProperties": False},
        default_threshold=0.5,
        version="2",  # v2: blank <OR> alternatives dropped; articles removed in mixed CJK text
        sources=(
            "fmeval:qa_accuracy.f1_score",
            "fmeval:qa_accuracy.precision_over_words",
            "fmeval:qa_accuracy.recall_over_words",
        ),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out, refs = output_text(case), references(case.expected)
        prfs = [token_prf(out, r, ctx.language) for r in refs]
        best = max(range(len(prfs)), key=lambda i: prfs[i][2])
        p, r, f = prfs[best]
        return ref_outcome(f, refs, best, precision=p, recall=r)


# --------------------------------------------------------------------------- ROUGE

_ROUGE_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def rouge_tokens(
    text: str, use_stemmer: bool, language: str | None = None, cjk: bool | None = None
) -> list[str]:
    """rouge-score tokenization (lowercase, non-alphanumerics -> space, stem tokens len > 3).

    ``cjk`` (default: whether ``text`` has CJK characters) selects per-character tokens."""
    if has_cjk(text) if cjk is None else cjk:
        return [t.lower() for t in tokenize(text, language)]
    tokens = [t for t in _ROUGE_NON_ALNUM.sub(" ", text.lower()).split() if t]
    if use_stemmer:
        st = stemmer("original")  # rouge-score stems with the original Porter algorithm
        tokens = [st.stem(t) if len(t) > 3 else t for t in tokens]
    return tokens


def _ngrams(tokens: Sequence[str], n: int) -> Counter[tuple[str, ...]]:
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _f(p: float, r: float) -> float:
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def rouge_prf(
    reference: str,
    candidate: str,
    variant: str = "rougeL",
    use_stemmer: bool = True,
    language: str | None = None,
) -> tuple[float, float, float]:
    cjk = has_cjk(reference) or has_cjk(candidate)
    ref = rouge_tokens(reference, use_stemmer, language, cjk)
    cand = rouge_tokens(candidate, use_stemmer, language, cjk)
    if variant == "rougeL":
        lcs = _lcs(ref, cand)
        p = lcs / len(cand) if cand else 0.0
        r = lcs / len(ref) if ref else 0.0
        return p, r, _f(p, r)
    n = int(variant.removeprefix("rouge"))
    ref_ng, cand_ng = _ngrams(ref, n), _ngrams(cand, n)
    overlap = sum((ref_ng & cand_ng).values())
    p = overlap / max(sum(cand_ng.values()), 1)
    r = overlap / max(sum(ref_ng.values()), 1)
    return p, r, _f(p, r)


@register
class Rouge(BaseMetric):
    spec = MetricSpec(
        id="text.rouge",
        category="text",
        description="ROUGE F-measure against the reference (rouge1, rouge2 or rougeL).",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {
                "variant": {"enum": ["rouge1", "rouge2", "rougeL"]},
                "use_stemmer": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        default_threshold=0.7,
        version="2",  # v2: one tokenizer for a pair with CJK; blank alternatives dropped
        sources=(
            "agentsight:calculate_rouge_l",
            "agentsight:AnswerCorrectness[rouge]",
            "fmeval:summarization_accuracy.rouge",
        ),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        variant = ctx.params.get("variant", "rougeL")
        use_stemmer = ctx.params.get("use_stemmer", True)
        out, refs = output_text(case), references(case.expected)
        prfs = [rouge_prf(r, out, variant, use_stemmer, ctx.language) for r in refs]
        best = max(range(len(prfs)), key=lambda i: prfs[i][2])
        p, r, f = prfs[best]
        return ref_outcome(
            f,
            refs,
            best,
            precision=p,
            recall=r,
            variant=variant,
        )


# --------------------------------------------------------------------------- BLEU


def bleu(
    reference_tokens: Sequence[str],
    candidate_tokens: Sequence[str],
    max_n: int = 4,
    epsilon: float = 0.1,
) -> float:
    """Sentence BLEU with uniform weights and NLTK ``method1`` smoothing (single reference)."""
    if not candidate_tokens:
        return 0.0
    numerators, denominators = [], []
    for n in range(1, max_n + 1):
        cand, ref = _ngrams(candidate_tokens, n), _ngrams(reference_tokens, n)
        numerators.append(sum((cand & ref).values()))
        denominators.append(max(1, sum(cand.values())))
    if numerators[0] == 0:
        return 0.0  # NLTK returns 0 when there is no unigram overlap
    log_p = 0.0
    for num, den in zip(numerators, denominators, strict=True):
        p = (num + epsilon) / den if num == 0 else num / den
        log_p += math.log(p) / max_n
    c, r = len(candidate_tokens), len(reference_tokens)
    bp = 1.0 if c > r else math.exp(1 - r / c)
    return bp * math.exp(log_p)


def _bleu_tokens(text: str, language: str | None, cjk: bool) -> list[str]:
    return tokenize(text.lower(), language) if cjk else text.lower().split()


def bleu_text(
    reference: str, candidate: str, max_n: int = 4, language: str | None = None
) -> float:
    """``bleu`` of two texts (AgentSight: lowercase whitespace tokens; CJK per character)."""
    cjk = has_cjk(reference) or has_cjk(candidate)
    return bleu(
        _bleu_tokens(reference, language, cjk), _bleu_tokens(candidate, language, cjk), max_n
    )


@register
class Bleu(BaseMetric):
    spec = MetricSpec(
        id="text.bleu",
        category="text",
        description="Sentence BLEU (uniform weights, NLTK method1 smoothing) vs the reference.",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {"max_n": {"type": "integer", "minimum": 1, "maximum": 8}},
            "additionalProperties": False,
        },
        default_threshold=0.5,
        version="2",  # v2: one tokenizer for a pair with CJK; blank alternatives dropped
        sources=("agentsight:calculate_bleu",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        max_n = ctx.params.get("max_n", 4)
        out, refs = output_text(case), references(case.expected)
        score, best = best_of(refs, lambda r: bleu_text(r, out, max_n, ctx.language))
        return ref_outcome(score, refs, best, max_n=max_n)


# --------------------------------------------------------------------------- METEOR


def _align(hyp: list[str], ref: list[str], key) -> list[tuple[int, int]]:
    """NLTK ``meteor_score._match_enums``: both lists are walked from the *end*; each hypothesis
    token takes the last unmatched reference token whose ``key`` is equal."""
    pairs = []
    used_ref: set[int] = set()
    for i in range(len(hyp) - 1, -1, -1):
        kh = key(hyp[i])
        for j in range(len(ref) - 1, -1, -1):
            if j not in used_ref and key(ref[j]) == kh:
                pairs.append((i, j))
                used_ref.add(j)
                break
    return pairs


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def meteor_tokens(text: str, language: str | None = None, cjk: bool | None = None) -> list[str]:
    """FMEval's METEOR tokenization: sentences, then Penn Treebank word tokens (punctuation is a
    token). Sentences split on ``[.!?]`` + whitespace (no downloaded sentence model, so
    abbreviations such as "Dr." can split differently); CJK text (``cjk``, default: whether
    ``text`` has CJK characters) uses ``tokenize``."""
    if has_cjk(text) if cjk is None else cjk:
        return tokenize(text, language)
    return [t for sentence in _SENTENCE_END.split(text) for t in treebank_tokenize(sentence)]


def meteor(
    reference: Sequence[str],
    hypothesis: Sequence[str],
    alpha: float = 0.9,
    beta: float = 3.0,
    gamma: float = 0.5,
) -> float:
    """METEOR with exact and stem matching (NLTK's defaults; no WordNet synonym stage)."""
    hyp, ref = [t.lower() for t in hypothesis], [t.lower() for t in reference]
    if not hyp or not ref:
        return 0.0
    exact = _align(hyp, ref, lambda t: t)
    used_h, used_r = {i for i, _ in exact}, {j for _, j in exact}
    rest_h = [(i, t) for i, t in enumerate(hyp) if i not in used_h]
    rest_r = [(j, t) for j, t in enumerate(ref) if j not in used_r]
    st = stemmer("nltk")  # NLTK's default Porter variant, as FMEval's METEOR
    stem_pairs = _align([t for _, t in rest_h], [t for _, t in rest_r], st.stem)
    matches = sorted(exact + [(rest_h[a][0], rest_r[b][0]) for a, b in stem_pairs])
    m = len(matches)
    if m == 0:
        return 0.0
    p, r = m / len(hyp), m / len(ref)
    fmean = p * r / (alpha * p + (1 - alpha) * r)
    chunks = 1
    for (h0, r0), (h1, r1) in zip(matches, matches[1:], strict=False):
        if not (h1 == h0 + 1 and r1 == r0 + 1):
            chunks += 1
    penalty = gamma * (chunks / m) ** beta
    return fmean * (1 - penalty)


def meteor_text(reference: str, hypothesis: str, language: str | None = None) -> float:
    """``meteor`` of two texts (``meteor_tokens``; CJK per character if either text has CJK)."""
    cjk = has_cjk(reference) or has_cjk(hypothesis)
    return meteor(
        meteor_tokens(reference, language, cjk), meteor_tokens(hypothesis, language, cjk)
    )


@register
class Meteor(BaseMetric):
    spec = MetricSpec(
        id="text.meteor",
        category="text",
        description="METEOR (exact + stem matching, fragmentation penalty) against the reference.",
        methods=(rule_method("output", "expected"),),
        params_schema={"type": "object", "additionalProperties": False},
        default_threshold=0.5,
        version="2",  # v2: one tokenizer for a pair with CJK; blank alternatives dropped
        sources=("fmeval:summarization_accuracy.meteor",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out, refs = output_text(case), references(case.expected)
        score, best = best_of(refs, lambda r: meteor_text(r, out, ctx.language))
        return ref_outcome(score, refs, best)


# --------------------------------------------------------------------------- Jaccard


def jaccard(a: str, b: str, *, stopwords: bool = False, language: str | None = None) -> float:
    """Token-set Jaccard (AgentSight ``calculate_jaccard``: lowercase whitespace tokens; both
    empty -> 1.0). ``stopwords=True`` is AgentSight ``scoring_utils.compute_keyword_overlap`` of
    ``extract_keywords`` (``[a-z]+`` words of >= 3 letters, scoring stop words; 0.0 if either side
    has no keyword); CJK text keeps per-character tokens minus stop words (extension)."""
    cjk = has_cjk(a) or has_cjk(b)
    if stopwords and not cjk:
        return keyword_jaccard(extract_keywords(a), extract_keywords(b))

    def toks(t: str) -> set[str]:
        items = tokenize(t.lower(), language) if cjk else t.lower().split()
        return {x for x in items if not (stopwords and x.strip(".,!?;:") in STOPWORDS)}

    s1, s2 = toks(a), toks(b)
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


@register
class Jaccard(BaseMetric):
    spec = MetricSpec(
        id="text.jaccard",
        category="text",
        description="Jaccard similarity of token sets (optionally ignoring stop words).",
        methods=(rule_method("output", "expected"),),
        params_schema={
            "type": "object",
            "properties": {"stopwords": {"type": "boolean"}},
            "additionalProperties": False,
        },
        default_threshold=0.5,
        version="2",  # v2: one tokenizer for a pair with CJK; blank alternatives dropped
        sources=(
            "agentsight:calculate_jaccard",
            "agentsight:AnswerCorrectness[jaccard]",
            "agentsight:compute_keyword_overlap",
        ),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out, refs = output_text(case), references(case.expected)
        sw = ctx.params.get("stopwords", False)
        score, best = best_of(refs, lambda r: jaccard(out, r, stopwords=sw, language=ctx.language))
        return ref_outcome(score, refs, best)


# --------------------------------------------------------------------------- TF-IDF cosine

_SKLEARN_TOKEN = re.compile(r"(?u)\b\w\w+\b")


def tfidf_cosine(a: str, b: str, language: str | None = None) -> float:
    """Cosine of TF-IDF vectors fit on the two texts (scikit-learn TfidfVectorizer defaults)."""
    if not a.strip() or not b.strip():
        return 0.0

    cjk = has_cjk(a) or has_cjk(b)

    def toks(t: str) -> list[str]:
        t = t.lower()
        return tokenize(t, language) if cjk else _SKLEARN_TOKEN.findall(t)

    docs = [Counter(toks(a)), Counter(toks(b))]
    vocab = set(docs[0]) | set(docs[1])
    df = {w: sum(w in d for d in docs) for w in vocab}
    n = len(docs)
    idf = {w: math.log((1 + n) / (1 + df[w])) + 1 for w in vocab}
    vecs = []
    for d in docs:
        v = {w: d[w] * idf[w] for w in d}
        norm = math.sqrt(sum(x * x for x in v.values()))
        vecs.append({w: x / norm for w, x in v.items()} if norm else {})
    return sum(vecs[0][w] * vecs[1].get(w, 0.0) for w in vecs[0])


@register
class TfidfCosine(BaseMetric):
    spec = MetricSpec(
        id="text.tfidf_cosine",
        category="text",
        description="Cosine similarity of TF-IDF vectors of output and reference.",
        methods=(rule_method("output", "expected"),),
        params_schema={"type": "object", "additionalProperties": False},
        default_threshold=0.5,
        version="2",  # v2: one tokenizer for a pair with CJK; blank alternatives dropped
        sources=("agentsight:calculate_cosine_similarity",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        out, refs = output_text(case), references(case.expected)
        score, best = best_of(refs, lambda r: tfidf_cosine(out, r, ctx.language))
        return ref_outcome(min(1.0, score), refs, best)
