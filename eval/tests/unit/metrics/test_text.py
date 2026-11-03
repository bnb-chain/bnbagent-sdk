"""Golden tables for text.*."""

from __future__ import annotations

import pytest

from bnbagent_eval.metrics.tokens import normalize, tokenize

from ._util import case, run_metric


@pytest.mark.parametrize(
    ("out", "exp", "params", "score"),
    [
        ("Paris", "Paris", {}, 1.0),
        (" Paris ", "Paris", {}, 1.0),
        ("paris", "Paris", {}, 0.0),
        ("paris", "Paris", {"case_insensitive": True}, 1.0),
        ("The  Paris!", "paris", {"normalize": True}, 1.0),
        ("Paris!", "Paris", {"normalize": ["punctuation"]}, 1.0),
        ("Rome", ["Paris", "Rome"], {}, 1.0),
        ("Rome", "Paris<OR>Rome", {}, 1.0),
        ("", "Paris", {}, 0.0),
        ("东京", "东京", {}, 1.0),
    ],
)
async def test_exact_match(out, exp, params, score) -> None:
    assert (await run_metric("text.exact_match", case(out, exp), params)).score == score


@pytest.mark.parametrize(
    ("out", "exp", "score"),
    [
        ("The Eiffel Tower!", "eiffel tower", 1.0),
        ("an apple", "Apple", 1.0),
        ("apples", "apple", 0.0),
    ],
)
async def test_quasi_exact_match(out, exp, score) -> None:
    assert (await run_metric("text.quasi_exact_match", case(out, exp))).score == score


@pytest.mark.parametrize(
    ("out", "exp", "params", "score"),
    [
        ("The capital is Paris.", "paris", {}, 1.0),
        ("The capital is Paris.", "Rome<OR>Paris", {}, 1.0),
        ("The capital is Paris.", ["Rome", "Paris"], {"mode": "all"}, 0.0),
        ("Rome and Paris", ["Rome", "Paris"], {"mode": "all"}, 1.0),
        ("The capital is Paris.", "the  paris", {"quasi": True}, 1.0),
        ("Paris, the capital", "paris capital", {"quasi": True}, 1.0),
        ("Paris, the capital", "paris capital", {}, 0.0),
        ("x | y", "x|y", {"delimiter": "|"}, 1.0),
    ],
)
async def test_contains(out, exp, params, score) -> None:
    assert (await run_metric("text.contains", case(out, exp), params)).score == score


async def test_token_f1_details_and_cjk() -> None:
    r = await run_metric("text.token_f1", case("the cat sat on the mat", "the cat is on the mat"))
    assert r.score == pytest.approx(0.75)
    assert r.details["precision"] == pytest.approx(0.75)
    r_zh = await run_metric("text.token_f1", case("猫在垫子上", "猫坐在垫子上", language="zh"))
    assert r_zh.score == pytest.approx(2 * (5 / 5) * (5 / 6) / (5 / 5 + 5 / 6))
    # FMEval: NLTK f_measure of two empty sets is None -> 0.0.
    assert (await run_metric("text.token_f1", case("", ""))).score == 0.0


async def test_rouge_variants() -> None:
    c = case("the cat sat", "the cat sat on the mat")
    r1 = await run_metric("text.rouge", c, {"variant": "rouge1", "use_stemmer": False})
    assert r1.score == pytest.approx(2 * 1 * 0.5 / 1.5)
    rl = await run_metric("text.rouge", c, {"variant": "rougeL"})
    assert rl.score == pytest.approx(2 / 3)
    r2 = await run_metric("text.rouge", c, {"variant": "rouge2", "use_stemmer": False})
    assert r2.score == pytest.approx(2 * 1.0 * 0.4 / 1.4)


async def test_bleu_and_meteor_ordering() -> None:
    ref = "the quick brown fox jumps over the lazy dog"
    close = await run_metric("text.bleu", case("the quick brown fox jumps over a lazy dog", ref))
    far = await run_metric("text.bleu", case("a dog", ref))
    assert 0 < far.score < close.score < 1
    assert (await run_metric("text.bleu", case("", ref))).score == 0.0
    m_close = await run_metric(
        "text.meteor", case("the quick brown foxes jumped over the dog", ref)
    )
    m_far = await run_metric("text.meteor", case("dog the over", ref))
    assert m_far.score < m_close.score < 1


async def test_jaccard_and_tfidf() -> None:
    j = await run_metric("text.jaccard", case("The cat sat on the mat.", "the cat is on the mat"))
    assert j.score == pytest.approx(3 / 7)  # AgentSight keeps punctuation: "mat." != "mat"
    js = await run_metric("text.jaccard", case("the cat", "a cat"), {"stopwords": True})
    assert js.score == 1.0
    assert (await run_metric("text.jaccard", case("", ""))).score == 1.0
    t = await run_metric("text.tfidf_cosine", case("bnb chain agent", "agent on bnb chain"))
    assert 0.5 < t.score < 1.0
    assert (await run_metric("text.tfidf_cosine", case("", "x"))).score == 0.0


async def test_classification_and_length() -> None:
    assert (
        await run_metric("text.classification_accuracy", case(" POSITIVE ", "positive"))
    ).score == 1
    mapped = await run_metric(
        "text.classification_accuracy", case("pos", "positive"), {"label_map": {"pos": "positive"}}
    )
    assert mapped.score == 1.0
    ok = await run_metric("text.length", case("one two three"), {"min_words": 2, "max_chars": 20})
    assert ok.score == 1.0 and ok.value == 13
    bad = await run_metric("text.length", case("x"), {"min_chars": 5})
    assert bad.score == 0.0 and "min_chars" in bad.reason


async def test_keyword_similarity_against_input() -> None:
    c = case("staking BNB rewards", None, input="How do BNB staking rewards work?")
    r = await run_metric("text.embedding_similarity", c)
    assert r.details["against"] == "input" and r.score > 0.3


def test_tokenizer_and_normalizer() -> None:
    assert normalize("The Quick, brown fox!") == "quick brown fox"
    assert normalize("The cat", language="de") == "the cat"  # articles only removed for English
    assert tokenize("BNB链上的agent") == ["BNB", "链", "上", "的", "agent"]
