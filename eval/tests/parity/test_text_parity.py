"""Built-in text metrics vs recorded reference-library values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bnbagent_eval.metrics.text.overlap import bleu, rouge_prf, tfidf_cosine
from bnbagent_eval.metrics.tokens import PorterStemmer

DATA = json.loads((Path(__file__).parents[1] / "fixtures" / "parity" / "text.json").read_text())
PAIRS = DATA["pairs"]
pytestmark = pytest.mark.parity


@pytest.mark.parametrize("word", sorted(DATA["porter_original"]))
def test_porter_matches_nltk_original(word: str) -> None:
    assert PorterStemmer().stem(word) == DATA["porter_original"][word]


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["candidate"][:20])
def test_rouge_matches_rouge_score(pair, monkeypatch) -> None:
    ref, cand = pair["reference"], pair["candidate"]
    assert rouge_prf(ref, cand, "rougeL", use_stemmer=False)[2] == pytest.approx(
        pair["rougeL_nostem"], abs=1e-6
    )
    for variant in ("rouge1", "rouge2", "rougeL"):
        assert rouge_prf(ref, cand, variant, use_stemmer=True)[2] == pytest.approx(
            pair[f"{variant}_stem"], abs=1e-6
        )


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["candidate"][:20])
def test_bleu_matches_nltk(pair) -> None:
    got = bleu(pair["reference"].lower().split(), pair["candidate"].lower().split())
    assert got == pytest.approx(pair["bleu"], abs=1e-6)


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["candidate"][:20])
def test_tfidf_matches_sklearn(pair) -> None:
    assert tfidf_cosine(pair["candidate"], pair["reference"]) == pytest.approx(
        pair["tfidf_cosine"], abs=1e-6
    )
