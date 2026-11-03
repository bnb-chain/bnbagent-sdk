from __future__ import annotations

import json

from bnbagent_eval import EvalClient, MetricConfig, Suite, register_python_metric
from bnbagent_eval.cli import main
from bnbagent_eval.reporting import to_json, to_markdown
from bnbagent_eval.types import EvalCase, EvalResponse

register_python_metric("cli_const", lambda case: float(case.output == "ok"))


def _resp() -> EvalResponse:
    s = Suite(
        name="t",
        version="1",
        metrics=(MetricConfig(id="custom.python", params={"fn": "cli_const"}),),
    )
    return EvalClient().evaluate_sync(suite=s, cases=[EvalCase(id="a", output="ok")])


def test_json_roundtrip() -> None:
    resp = _resp()
    assert EvalResponse.from_dict(json.loads(to_json(resp))) == resp


def test_markdown_contains_verdict_and_metrics() -> None:
    md = to_markdown(_resp())
    assert "Verdict:** `pass`" in md and "`custom.python`" in md and "`a`" in md


def test_cli_metrics(capsys) -> None:
    assert main(["metrics"]) == 0
    assert "custom.python" in capsys.readouterr().out
    assert main(["metrics", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"]


def test_cli_run(tmp_path, capsys) -> None:
    suite = tmp_path / "s.yaml"
    suite.write_text(
        "name: s\nversion: '1'\nmetrics:\n"
        "  - {id: custom.python, params: {fn: cli_const}, threshold: 0.5}\n"
    )
    cases = tmp_path / "c.jsonl"
    cases.write_text('{"id": "1", "output": "ok"}\n{"id": "2", "output": "no"}\n')
    out = tmp_path / "r.md"
    code = main(
        ["run", "--suite", str(suite), "--cases", str(cases), "--format", "md", "--out", str(out)]
    )
    assert code == 1 and "fail" in out.read_text()
    cases.write_text('{"id": "1", "output": "ok"}\n')
    assert main(["run", "--suite", str(suite), "--cases", str(cases)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"]["status"] == "pass"


def test_cli_usage_error(tmp_path, capsys) -> None:
    assert main(["run", "--suite", str(tmp_path / "missing.yaml"), "--cases", "x.json"]) == 64
    assert "error:" in capsys.readouterr().err


def test_schema_dir_defaults_to_the_checkout_else_the_working_directory(tmp_path, monkeypatch):
    from pathlib import Path

    from bnbagent_eval import cli

    checkout = Path(cli.__file__).resolve().parent.parent
    assert cli._default_schema_dir() == str(checkout / "schemas")
    installed = tmp_path / "site-packages" / "bnbagent_eval" / "cli.py"
    installed.parent.mkdir(parents=True)
    monkeypatch.setattr(cli, "__file__", str(installed))
    assert cli._default_schema_dir() == "schemas"
