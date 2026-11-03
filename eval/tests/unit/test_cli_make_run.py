"""CLI ``make`` (eval maker) and ``run --csv`` / ``--export``."""

from __future__ import annotations

import csv
import json

import yaml

from bnbagent_eval.cli import main
from bnbagent_eval.judges.providers.fake import FakeJudge

from .authoring.test_eval_maker import ASSESS, ASSESSMENT, DESCRIPTION, script


def make_scripted_judge() -> FakeJudge:
    return FakeJudge(script())


def make_bad_input_judge() -> FakeJudge:
    low = {"clarity_score": 0.0, "completeness_score": 0.0, "correlation_score": 0.0}
    return FakeJudge(script(**{ASSESS: {**ASSESSMENT, **low}}))


def test_make_writes_a_lint_clean_suite(tmp_path, capsys) -> None:
    out = tmp_path / "suite.yaml"
    rc = main(["make", DESCRIPTION, "--judge", f"{__name__}:make_scripted_judge", "-o", str(out)])
    assert rc == 0
    suite = yaml.safe_load(out.read_text())
    assert suite["metrics"]
    assert main(["lint", str(out), "--use", "dev"]) == 0
    capsys.readouterr()
    desc = tmp_path / "desc.txt"
    desc.write_text(DESCRIPTION)
    rc = main(["make", f"@{desc}", "--judge", f"{__name__}:make_scripted_judge", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["suite"]["metrics"]


def test_make_reports_unusable_input(capsys) -> None:
    rc = main(["make", "x", "--judge", f"{__name__}:make_bad_input_judge"])
    assert rc == 2 and "error" in capsys.readouterr().err


def test_run_writes_csv(tmp_path) -> None:
    suite = tmp_path / "s.yaml"
    suite.write_text(
        yaml.safe_dump({"name": "s", "version": "1", "metrics": [{"id": "text.exact_match"}]})
    )
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps([{"id": "1", "output": "a", "expected": "a"}]))
    out_csv = tmp_path / "r.csv"
    report = str(tmp_path / "r.json")
    rc = main(
        [
            "run",
            "--suite",
            str(suite),
            "--cases",
            str(cases),
            "--out",
            report,
            "--csv",
            str(out_csv),
        ]
    )
    assert rc == 0
    rows = list(csv.DictReader(out_csv.open(encoding="utf-8")))
    assert rows[0]["metric_id"] == "text.exact_match" and rows[0]["score"] == "1.0"


def test_usage_errors_exit_64_not_the_inconclusive_code(capsys) -> None:
    """Usage errors exit 64, not argparse's 2 (the inconclusive-run code); a bad
    MODULE:FACTORY reference is a usage error, not a traceback."""
    import pytest

    with pytest.raises(SystemExit) as info:
        main(["run", "--cases", "c.json"])  # --suite missing
    assert info.value.code == 64
    assert main(["make", "x", "--judge", "no_such_module_xyz:factory"]) == 64
    assert "cannot load" in capsys.readouterr().err
    assert main(["make", "x", "--judge", "no_colon"]) == 64


def test_publish_rejects_a_file_that_is_not_a_response(tmp_path, capsys) -> None:
    """``publish`` on a Markdown report is a usage error, not a traceback."""
    report = tmp_path / "report.md"
    report.write_text("# Evaluation report\n")
    assert main(["publish", str(report), "--agent-id", "7"]) == 64
    assert "not an EvalResponse" in capsys.readouterr().err


def test_run_diagnose_writes_root_causes_for_failing_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EVAL_JUDGE_PROVIDER", raising=False)  # rule-based diagnosis
    suite = tmp_path / "s.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "name": "s",
                "version": "1",
                "metrics": [{"id": "text.exact_match", "required": True}],
            }
        )
    )
    cases = tmp_path / "c.json"
    cases.write_text(json.dumps([{"id": "1", "output": "a", "expected": "b"}]))
    out = tmp_path / "diagnosis.json"
    args = ["run", "--suite", str(suite), "--cases", str(cases), "--out", str(tmp_path / "r.json")]
    assert main([*args, "--diagnose", str(out)]) == 1
    diagnosis = json.loads(out.read_text())
    assert diagnosis["cases"][0]["case_id"] == "1" and diagnosis["cases"][0]["failed_metrics"]


def test_malformed_suite_and_cases_are_usage_errors(tmp_path, capsys) -> None:
    from bnbagent_eval.cli import EXIT_USAGE

    good_suite = tmp_path / "suite.yaml"
    good_suite.write_text("name: s\nversion: '1'\nmetrics:\n  - id: text.exact_match\n")
    good_cases = tmp_path / "cases.jsonl"
    good_cases.write_text('{"id": "1", "output": "a", "expected": "a"}\n')
    bad_suite = tmp_path / "bad.yaml"
    bad_suite.write_text("name: s\nmetrics: [\n")
    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text('{"id": "1"}\n{"id": "2", "output": \n')
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("[{")

    assert main(["lint", str(bad_suite)]) == EXIT_USAGE
    assert "not valid YAML" in capsys.readouterr().err
    assert main(["run", "--suite", str(bad_suite), "--cases", str(good_cases)]) == EXIT_USAGE
    assert "not valid YAML" in capsys.readouterr().err
    assert main(["run", "--suite", str(good_suite), "--cases", str(bad_jsonl)]) == EXIT_USAGE
    assert "bad.jsonl:2: not valid JSON" in capsys.readouterr().err
    assert main(["run", "--suite", str(good_suite), "--cases", str(bad_json)]) == EXIT_USAGE
    assert "not valid JSON" in capsys.readouterr().err
