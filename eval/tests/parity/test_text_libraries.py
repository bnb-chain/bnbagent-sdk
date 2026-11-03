"""Our text building blocks vs the reference libraries (test-only dependencies in ``[dev]``).

The metrics never import NLTK or rouge-score; these tests check our implementations agree:
the Porter stemmer (original and NLTK modes), Penn Treebank tokenization (NLTK's
``word_tokenize`` for one sentence) and ROUGE (``rouge-score``).
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from bnbagent_eval.metrics.text.overlap import rouge_prf
from bnbagent_eval.metrics.tokens import PorterStemmer, treebank_tokenize

PACKAGE = Path(__file__).resolve().parents[2]


def _vocabulary() -> set[str]:
    words: set[str] = set()
    for path in list(PACKAGE.glob("bnbagent_eval/judges/prompts/**/*.py")) + list(
        PACKAGE.glob("docs/**/*.md")
    ):
        words |= set(re.findall(r"[a-z]+", path.read_text(encoding="utf-8").lower()))
    return words | {
        "sky", "skies", "dying", "dies", "died", "spied", "flies", "hopefully",
        "geology", "ideology", "analogies", "as", "is", "sensibly", "possibly",
    }  # fmt: skip


@pytest.mark.parametrize(("ours", "theirs"), [("original", "ORIGINAL_ALGORITHM"),
                                              ("nltk", "NLTK_EXTENSIONS")])  # fmt: skip
def test_porter_stemmer_matches_nltk(ours: str, theirs: str) -> None:
    porter = pytest.importorskip("nltk.stem.porter")
    reference, mine = (
        porter.PorterStemmer(mode=getattr(porter.PorterStemmer, theirs)),
        PorterStemmer(ours),
    )
    vocabulary = _vocabulary()
    assert len(vocabulary) > 2000
    assert [w for w in sorted(vocabulary) if mine.stem(w) != reference.stem(w)] == []


def test_treebank_tokenizer_matches_nltk() -> None:
    tokenize = pytest.importorskip("nltk.tokenize").NLTKWordTokenizer().tokenize
    pieces = [
        "Good", "muffins", "cost", "$3.88", "(roughly", "3,36", "euros)", "York.", "don't",
        "can't", "cannot", "I'm", '"quoted"', "'single'", "U.S.", "e-mail", "--", "...",
        "wait!", "why?", "a;b", "x@y", "#tag", "50%", "it's", "they'll", "gonna", "'tis",
        "“curly”", "«fr»", "a–b", "[x]", "{y}", "<z>", "*star*", 'end."', "3:30", "1,000",
    ]  # fmt: skip
    rng = random.Random(0)
    for _ in range(5000):
        sentence = " ".join(rng.choice(pieces) for _ in range(rng.randint(0, 10)))
        assert treebank_tokenize(sentence) == tokenize(sentence), sentence


def test_rouge_matches_rouge_score() -> None:
    scorer_mod = pytest.importorskip("rouge_score.rouge_scorer")
    words = ("the quick brown fox jumps over lazy dog running runner ran cats agents agent BNB "
             "token tokens transfer transferred fees paid paying , . ! ? 3 seconds generalization "
             "hopefully relational").split()  # fmt: skip
    rng = random.Random(1)
    for _ in range(1000):
        ref = " ".join(rng.choice(words) for _ in range(rng.randint(0, 14)))
        cand = " ".join(rng.choice(words) for _ in range(rng.randint(0, 14)))
        for variant in ("rouge1", "rouge2", "rougeL"):
            for stem in (True, False):
                lib = scorer_mod.RougeScorer([variant], use_stemmer=stem).score(ref, cand)[variant]
                ours = rouge_prf(ref, cand, variant, stem)
                assert ours == pytest.approx((lib.precision, lib.recall, lib.fmeasure), abs=1e-12)
