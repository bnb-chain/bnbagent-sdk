"""CLI ``bnbagent-eval calibrate``: labels from metadata / files, error injection, exit codes."""

from __future__ import annotations

import json

import yaml

from bnbagent_eval.cli import EXIT_USAGE, main
from bnbagent_eval.judges.base import JudgeRequest
from bnbagent_eval.judges.providers.fake import FakeJudge

ANSWER = "Your balance is 12 BNB. The transfer can be sent today. Fees are 0.5 BNB."
WRONG = "Your balance is 99 BNB."


def _oracle(request: JudgeRequest) -> dict:
    """Rates the response Excellent only when it is exactly the clean answer."""
    clean = f'field="actual_output">\n{ANSWER}\n</untrusted' in request.user
    return {"reasoning": "checked", "score": "Excellent" if clean else "Poor"}


def make_oracle_judge() -> FakeJudge:
    return FakeJudge(_oracle, model="oracle-judge")


def make_lazy_judge() -> FakeJudge:
    return FakeJudge({"reasoning": "fine", "score": "Excellent"}, model="lazy-judge")


def _ref(factory: str) -> str:
    return f"{__name__}:{factory}"


def _write(tmp_path, *, judge_metrics: bool = True):
    metrics = [{"id": "quality.helpfulness", "name": "helpfulness"}]
    if not judge_metrics:
        metrics = [{"id": "text.exact_match"}]
    else:
        metrics.append({"id": "text.exact_match"})  # rule metrics are ignored
    suite = tmp_path / "suite.yaml"
    suite.write_text(yaml.safe_dump({"name": "cal", "version": "1", "metrics": metrics}))
    cases = [
        {
            "id": f"g{i}",
            "input": "What is my balance?",
            "output": ANSWER,
            "expected": ANSWER,
            "metadata": {"label": "pass"},
        }
        for i in range(3)
    ] + [
        {
            "id": f"b{i}",
            "input": "What is my balance?",
            "output": WRONG,
            "expected": ANSWER,
            "metadata": {"human_label": "fail"},
        }
        for i in range(3)
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    return str(suite), str(path)


def test_agreeing_judge_passes(tmp_path, capsys) -> None:
    suite, cases = _write(tmp_path)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"oracle": {"input_per_1k": 1.0, "output_per_1k": 2.0}}))
    code = main(
        [
            "calibrate",
            "--suite",
            suite,
            "--cases",
            cases,
            "--judge",
            _ref("make_oracle_judge"),
            "--prices",
            str(prices),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["passed"] and out["failures"] == []
    report = out["metrics"]["helpfulness"]
    assert list(out["metrics"]) == ["helpfulness"]
    assert report["score_type"] == "binary" and report["n_compared"] == 6
    assert report["cohens_kappa"] == 1.0 and report["confusion"]["matrix"] == [[3, 0], [0, 3]]
    assert out["cost"]["calls"] == 6 and out["cost"]["model"] == "oracle-judge"
    assert out["cost"]["usd"] > 0
    assert out["judge"]["model"] == "oracle-judge"


def test_disagreeing_judge_fails_min_kappa(tmp_path, capsys) -> None:
    suite, cases = _write(tmp_path)
    code = main(
        ["calibrate", "--suite", suite, "--cases", cases, "--judge", _ref("make_lazy_judge")]
    )
    text = capsys.readouterr().out
    assert code == 1
    assert "helpfulness" in text and "kappa=0.000" in text and "FAIL" in text
    # A permissive floor passes the same run.
    assert (
        main(
            [
                "calibrate",
                "--suite",
                suite,
                "--cases",
                cases,
                "--judge",
                _ref("make_lazy_judge"),
                "--min-kappa",
                "0",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["passed"]


def test_labels_file_overrides_metadata(tmp_path, capsys) -> None:
    suite, cases = _write(tmp_path)
    labels = tmp_path / "labels.csv"
    # Inverted labels: the oracle disagrees everywhere.
    rows = ["case_id,metric,label"] + [f"g{i},helpfulness,fail" for i in range(3)]
    rows += [f"b{i},helpfulness,pass" for i in range(3)]
    labels.write_text("\n".join(rows) + "\n")
    code = main(
        [
            "calibrate",
            "--suite",
            suite,
            "--cases",
            cases,
            "--labels",
            str(labels),
            "--judge",
            _ref("make_oracle_judge"),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["metrics"]["helpfulness"]["cohens_kappa"] < 0
    jsonl = tmp_path / "labels.jsonl"
    jsonl.write_text("\n".join(json.dumps({"id": f"g{i}", "score": 1.0}) for i in range(3)))
    assert (
        main(
            [
                "calibrate",
                "--suite",
                suite,
                "--cases",
                cases,
                "--labels",
                str(jsonl),
                "--judge",
                _ref("make_oracle_judge"),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["metrics"]["helpfulness"]["n_compared"] == 6


def test_error_injection_dataset(tmp_path, capsys) -> None:
    suite, _ = _write(tmp_path)
    clean = [{"id": f"c{i}", "input": "What is my balance?", "output": ANSWER} for i in range(6)]
    cases = tmp_path / "clean.json"
    cases.write_text(json.dumps(clean))
    args = [
        "calibrate",
        "--suite",
        suite,
        "--cases",
        str(cases),
        "--inject",
        "--seed",
        "3",
        "--judge",
        _ref("make_oracle_judge"),
        "--json",
    ]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["injection"]["clean"] == 3 and out["injection"]["faulty"] == 3
    assert out["metrics"]["helpfulness"]["cohens_kappa"] == 1.0 and out["cases"] == 6


def test_usage_errors(tmp_path, capsys) -> None:
    suite, cases = _write(tmp_path, judge_metrics=False)
    assert main(["calibrate", "--suite", suite, "--cases", cases]) == EXIT_USAGE
    assert "no judge metrics" in capsys.readouterr().err
    suite, cases = _write(tmp_path)
    labels = tmp_path / "bad.json"
    labels.write_text(json.dumps({"g0": "maybe"}))
    code = main(
        [
            "calibrate",
            "--suite",
            suite,
            "--cases",
            cases,
            "--labels",
            str(labels),
            "--judge",
            _ref("make_oracle_judge"),
        ]
    )
    assert code == EXIT_USAGE and "unrecognized label" in capsys.readouterr().err


def make_broken_judge() -> FakeJudge:
    from bnbagent_eval.exceptions import JudgeNonRetryable

    def fail(request: JudgeRequest) -> dict:
        raise JudgeNonRetryable("model rejected the request")

    return FakeJudge(fail, model="broken-judge")


def test_judge_errors_are_reported_not_hidden_in_kappa(tmp_path, capsys) -> None:
    suite, cases = _write(tmp_path)
    code = main(
        ["calibrate", "--suite", suite, "--cases", cases, "--judge", _ref("make_broken_judge"),
         "--json"]
    )  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["judge_errors"] == {"helpfulness": 6}
    assert "judge error" in out["failures"][0] and "rejected" in out["failures"][0]


def test_malformed_labels_and_prices_files_raise_eval_error(tmp_path) -> None:
    import pytest

    from bnbagent_eval.cli import _read_data_file
    from bnbagent_eval.exceptions import EvalError

    bad = tmp_path / "labels.yaml"
    bad.write_text("a: [\n")
    with pytest.raises(EvalError, match="cannot read"):
        _read_data_file(str(bad))
    with pytest.raises(EvalError, match="cannot read"):
        _read_data_file(str(tmp_path / "missing.jsonl"))
