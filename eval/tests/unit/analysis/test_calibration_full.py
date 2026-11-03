"""analysis.calibration (full): AgentSight benchmark agreement against hand-computed tables,
confusion matrices, judge ranking, price table cost, label parsing."""

from __future__ import annotations

import pytest

from bnbagent_eval.analysis.calibration import (
    JudgeUsage,
    MeteredJudge,
    accuracy,
    agreement,
    as_per_class_mae,
    benchmark_agreement,
    binary_confusion,
    case_label,
    cohen_kappa,
    confusion_matrix,
    estimate_cost,
    label_kappa,
    parse_label,
    price_row,
    rank_judges,
    tiered_confusion,
)
from bnbagent_eval.judges.base import JudgeRequest
from bnbagent_eval.judges.providers.fake import FakeJudge

REF_BIN = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
CAND_BIN = [1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0]
REF_ORD = [1.0, 0.75, 0.5, 0.25, 0.0]
CAND_ORD = [1.0, 0.5, 0.5, 0.25, 0.25]


def test_binary_agreement_hand_computed() -> None:
    r = benchmark_agreement(REF_BIN, CAND_BIN)
    assert r.score_type == "binary" and r.n_compared == 8
    # po = 6/8, pe = 0.5 -> kappa 0.5; MCC = (3*3 - 1*1) / sqrt(4^4) = 0.5.
    assert (r.mae, r.accuracy, r.cohens_kappa, r.mcc) == (0.25, 0.75, 0.5, 0.5)
    assert r.weighted_kappa is None  # ordinal only, as in the source
    assert (r.fail_precision, r.fail_recall, r.fail_f1) == (0.75, 0.75, 0.75)
    assert r.per_class_mae == {"mae_fail": 0.25, "mae_pass": 0.25}
    assert r.confusion is not None and r.confusion.labels == ("fail", "pass")
    assert r.confusion.matrix == ((3, 1), (1, 3))
    assert r.confusion.count("pass", "fail") == 1


def test_ordinal_agreement_hand_computed() -> None:
    r = benchmark_agreement(REF_ORD, CAND_ORD)
    assert r.score_type == "ordinal" and r.mcc is None
    # po = 3/5, pe = (1 + 2 + 2) / 25 -> kappa 0.5.
    assert r.cohens_kappa == 0.5 and r.accuracy == 0.6 and r.mae == 0.1
    # Linear weights |i - j| / 4: 1 - 0.5 / 1.8.
    assert r.weighted_kappa == pytest.approx(1 - 0.5 / 1.8, abs=1e-4)
    # Binarized at the source's 0.75: fail P = 3/4, R = 1.
    assert (r.fail_precision, r.fail_recall, r.fail_f1) == (0.75, 1.0, 0.8571)
    assert r.per_class_mae == {
        "mae_0.0": 0.25,
        "mae_0.25": 0.0,
        "mae_0.5": 0.0,
        "mae_0.75": 0.25,
        "mae_1.0": 0.0,
    }
    assert r.spearman is not None and r.spearman > 0.9
    assert r.to_dict()["confusion"]["labels"] == ["0.0", "0.25", "0.5", "0.75", "1.0"]
    # An explicit threshold changes fail detection only.
    r5 = benchmark_agreement(REF_ORD, CAND_ORD, threshold=0.5)
    assert r5.cohens_kappa == r.cohens_kappa and r5.fail_recall == 1.0


def test_pairing_missing_and_undefined() -> None:
    ref = {"a": 1.0, "b": 0.0, "c": None, "d": 1.0}
    cand = {"a": 1.0, "b": None, "c": 0.0, "d": 1.0, "e": 0.0}
    r = benchmark_agreement(ref, cand)
    # c has no reference (dropped); e is not in the reference; b is penalized as 1 - 0.
    assert r.n_compared == 3 and r.missing_candidate == 1 and r.mae == pytest.approx(0.3333)
    empty = benchmark_agreement({"a": None}, {"a": 1.0})
    assert empty.score_type == "unknown" and empty.cohens_kappa is None
    all_pass = benchmark_agreement([1.0, 1.0], [1.0, 1.0])
    assert all_pass.fail_f1 is None and all_pass.mcc is None and all_pass.cohens_kappa == 1.0


def test_kappa_variants_and_confusion_helpers() -> None:
    assert cohen_kappa([0, 1, 2], [0, 1, 2], 3, "linear") == 1.0
    assert label_kappa(["a", "b"], ["a", "b"]) == 1.0
    assert accuracy(["x", "y"], ["x", "z"]) == 0.5
    with pytest.raises(ValueError):
        accuracy([], [])
    tiers = tiered_confusion([0.9, 0.1], [1.0, 0.3])
    assert tiers.labels == ("0.0", "0.25", "0.5", "0.75", "1.0")
    assert tiers.count("1.0", "1.0") == 1 and tiers.count("0.25", "0.0") == 1
    assert binary_confusion([0.7, 0.2], [0.4, 0.2]).matrix == ((1, 1), (0, 0))
    cm = confusion_matrix(["pass", "fail"], ["pass", "pass"], ["fail", "pass"])
    assert cm.to_dict() == {"labels": ["fail", "pass"], "matrix": [[0, 0], [1, 1]]}
    assert as_per_class_mae([1.0, 0.0], [0.5, 0.0]) == {"mae_fail": 0.0, "mae_pass": 0.5}


def test_existing_agreement_api_unchanged() -> None:
    a = agreement([1.0, 0.0], [1.0, 0.0])
    assert a.cohen_kappa == 1.0 and set(a.to_dict()) >= {"mae", "weighted_kappa", "mcc"}


def test_price_table_cost() -> None:
    table = {
        "claude-haiku": {"input": 1.0, "output": 5.0},  # per 1M (AgentSight pricing file)
        "claude": {"input_per_1m": 3.0, "output_per_1m": 15.0},
        "gpt-small": {"input_per_1k": 0.001, "output_per_1k": 0.002},
        "*": {"input_per_1k": 0.01},
    }
    assert price_row("us.anthropic.claude-haiku-4-5", table) is table["claude-haiku"]
    assert estimate_cost("us.anthropic.claude-haiku-4-5", 1_000_000, 200_000, table) == 2.0
    assert estimate_cost("claude-opus", 1_000_000, 0, table) == 3.0
    assert estimate_cost("gpt-small", 2000, 1000, table) == pytest.approx(0.004)
    assert estimate_cost("other", 1000, 1000, table) == pytest.approx(0.01)
    assert estimate_cost("other", 1, 1, {"x": {"input": 1}}) is None


async def test_metered_judge_and_ranking() -> None:
    inner = FakeJudge({"reasoning": "r", "score": "Good"}, model="cheap-model")
    judge = MeteredJudge(inner)
    for _ in range(2):
        await judge.complete(JudgeRequest(system="s" * 40, user="u" * 40))
    assert judge.usage.calls == 2 and judge.usage.input_tokens == 40
    assert judge.config is inner.config and judge.requests is inner.requests

    good = benchmark_agreement(REF_BIN, REF_BIN)
    weak = benchmark_agreement(REF_BIN, CAND_BIN)
    prices = {"cheap": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    usage = {
        "cheap": JudgeUsage("cheap-model", calls=8, input_tokens=1000, output_tokens=0),
        "strong": JudgeUsage("strong-model", calls=8, reported_usd=4.0),
    }
    ranking = rank_judges(
        {"cheap": {"m": weak}, "strong": {"m": good}},
        usage=usage,
        prices=prices,
        reference_cost=4.0,
    )
    assert [r["judge"] for r in ranking] == ["strong", "cheap"]  # best kappa first
    cheap = ranking[1]
    assert cheap["overall_cohens_kappa"] == 0.5 and cheap["usd"] == 1.0
    assert cheap["usd_per_case"] == 0.125 and cheap["cost_ratio"] == 0.25
    assert ranking[0]["usage"]["usd"] == 4.0
    # Ties on kappa: the cheaper judge ranks first.
    tie = rank_judges(
        {"a": {"m": good}, "b": {"m": good}},
        usage={"a": JudgeUsage("a", reported_usd=2.0), "b": JudgeUsage("b", reported_usd=1.0)},
    )
    assert [r["judge"] for r in tie] == ["b", "a"]


def test_label_parsing_and_lookup() -> None:
    assert [parse_label(v) for v in ("pass", "FAIL", "Good", "below average", "0.75", True)] == [
        1.0,
        0.0,
        0.75,
        0.25,
        0.75,
        1.0,
    ]
    assert parse_label(None) is None
    for bad in ("maybe", 3, "2.5"):
        with pytest.raises(ValueError):
            parse_label(bad)
    meta = {"labels": {"helpfulness": "fail"}, "label": "pass"}
    assert case_label("c", meta, "helpfulness", "quality.helpfulness") == 0.0
    assert case_label("c", meta, "clarity", "quality.clarity") == 1.0  # scalar fallback
    assert case_label("c", {}, "k", "m.id", {"c": {"m.id": 0.5}}) == 0.5
    assert case_label("c", {}, "k", "m.id", {"c": "yes"}) == 1.0
    assert case_label("c", {"evaluator": "other", "label": "fail"}, "k", "m.id") is None
    assert case_label("c", {}, "k", "m.id") is None


def test_gate_uses_decision_kappa_for_binary_labels_and_tiered_judge() -> None:
    from bnbagent_eval.analysis.calibration import benchmark_agreement

    labels = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
    judge = [0.75, 1.0, 0.75, 0.0, 0.25, 0.0]  # "Good" for a pass label is not a disagreement
    report = benchmark_agreement(labels, judge)
    assert report.score_type == "ordinal" and report.labels_binary
    assert report.cohens_kappa < 0.6 and report.decision_kappa == 1.0
    assert report.gate_kappa == 1.0 and report.to_dict()["gate_kappa"] == 1.0
    binary = benchmark_agreement([1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 1.0, 0.0])
    assert binary.gate_kappa == binary.cohens_kappa
