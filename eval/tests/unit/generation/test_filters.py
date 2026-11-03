"""LlmAsJudgeFilter thresholds, parsing and repair; deterministic dedupe / length / language."""

from __future__ import annotations

import pytest

from bnbagent_eval.exceptions import JudgeError
from bnbagent_eval.generation.filters import (
    DEFAULT_CRITERIA,
    DedupeFilter,
    LanguageFilter,
    LengthFilter,
    LlmAsJudgeFilter,
    detect_language,
    run_filters,
)
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types.case import Context, EvalCase

MID = "generation.filters.llm_as_judge"


def case(i: int, text: str = "What is BNB?", **kw) -> EvalCase:
    return EvalCase(id=f"c{i}", input=text, expected="A chain token.", **kw)


def verdict(met):
    return {"reasoning": "checked", "criteria_met": met}


def test_defaults_are_etk():
    f = LlmAsJudgeFilter(FakeJudge())
    assert f.threshold == 0.8 and f.criteria == DEFAULT_CRITERIA and len(f.criteria) == 3
    with pytest.raises(ValueError):
        LlmAsJudgeFilter(FakeJudge(), criteria=[])
    with pytest.raises(ValueError):
        LlmAsJudgeFilter(FakeJudge(), threshold=1.5)


@pytest.mark.parametrize(
    ("met", "threshold", "kept"),
    [(3, 0.8, True), (2, 0.8, False), (2, 0.6, True), ("3", 0.8, True), (9, 1.0, True)],
)
async def test_threshold_on_share_of_criteria(met, threshold, kept):
    f = LlmAsJudgeFilter(FakeJudge({MID: verdict(met)}), threshold=threshold)
    k, r = await f.filter([case(0)])
    assert (len(k), len(r)) == ((1, 0) if kept else (0, 1))
    if not kept:
        assert r[0].filter == "llm_as_judge"
        assert r[0].score == pytest.approx(2 / 3)
        assert "threshold" in r[0].reason and "checked" in r[0].reason


async def test_prompt_contains_criteria_and_fenced_case():
    judge = FakeJudge({MID: verdict(1)})
    f = LlmAsJudgeFilter(judge, criteria=["Is clear", "Is grounded"])
    await f.filter([case(0, contexts=(Context(text="ctx text"),))])
    req = judge.requests[0]
    assert "1. Is clear" in req.system and "2. Is grounded" in req.system
    assert "<untrusted-" in req.user and "ctx text" in req.user and "What is BNB?" in req.user


async def test_bad_json_is_repaired():
    judge = FakeJudge({MID: ["I think it is fine", verdict(3)]})
    kept, rejected = await LlmAsJudgeFilter(judge).filter([case(0)])
    assert len(kept) == 1 and not rejected
    assert len(judge.requests) == 2
    assert "invalid" in judge.requests[1].user.lower()


async def test_non_integer_count_is_repaired():
    judge = FakeJudge({MID: [verdict("many"), verdict(-1), verdict(3)]})
    kept, _ = await LlmAsJudgeFilter(judge).filter([case(0)])
    assert len(kept) == 1 and len(judge.requests) == 3


async def test_errored_case_is_rejected_and_all_errored_raises():
    def script(request):
        return JudgeError("boom") if "broken" in request.user else verdict(3)

    f = LlmAsJudgeFilter(FakeJudge(script))
    kept, rejected = await f.filter([case(0, "broken one"), case(1)])
    assert [c.id for c in kept] == ["c1"]
    assert rejected[0].case.id == "c0" and rejected[0].reason.startswith("error:")
    with pytest.raises(JudgeError):
        await LlmAsJudgeFilter(FakeJudge(JudgeError("down"))).filter([case(0)])


def test_dedupe_normalizes():
    cases = [case(0, "What is BNB?"), case(1, "  what is   bnb "), case(2, "Other?")]
    kept, rejected = DedupeFilter().apply(cases)
    assert [c.id for c in kept] == ["c0", "c2"]
    assert rejected[0].reason == "duplicate of c0"
    kept, _ = DedupeFilter(key=lambda c: c.id).apply(cases)
    assert len(kept) == 3


def test_length_filter():
    cases = [case(0, ""), case(1, "ok question"), case(2, "x" * 50)]
    kept, rejected = LengthFilter(min_chars=3, max_chars=20).apply(cases)
    assert [c.id for c in kept] == ["c1"]
    assert {r.case.id: r.reason.split()[-2] for r in rejected} == {"c0": "short", "c2": "long"}


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("What is the fee for this transfer and how do I pay it?", "en"),
        ("¿Cuál es la comisión de la transferencia y cómo se paga?", "es"),
        ("Quelle est la commission pour le transfert et comment la payer ?", "fr"),
        ("这笔转账的手续费是多少？", "zh"),
        ("この送金の手数料はいくらですか？", "ja"),
        ("이 송금의 수수료는 얼마입니까?", "ko"),
        ("Какая комиссия за этот перевод?", "ru"),
        ("ok", None),
        ("12345", None),
    ],
)
def test_detect_language(text, lang):
    assert detect_language(text) == lang


async def test_language_filter_and_chain():
    cases = [
        case(0, "What is the fee for this transfer and how do I pay it?"),
        case(1, "这笔转账的手续费是多少？"),
        case(2, "hi"),
        case(3, "any text", language="en-US"),
        case(4, "What is the fee for this transfer and how do I pay it?"),
    ]
    kept, rejected = LanguageFilter(["en"]).apply(cases)
    assert [c.id for c in kept] == ["c0", "c2", "c3", "c4"]
    assert rejected[0].reason == "language zh not allowed"
    kept, _ = LanguageFilter(["en"], keep_undetermined=False).apply(cases)
    assert "c2" not in [c.id for c in kept]

    judge = FakeJudge({MID: verdict(3)})
    kept, rejected = await run_filters(
        cases, [DedupeFilter(), LanguageFilter(["en", "zh"]), LlmAsJudgeFilter(judge)]
    )
    assert [c.id for c in kept] == ["c0", "c1", "c2", "c3"]
    assert [r.filter for r in rejected] == ["dedupe"]
    assert len(judge.requests) == 4  # the judge only sees what earlier filters kept
