from __future__ import annotations

import csv
import io

import pytest

from bnbagent_eval.reporting.csv import (
    CASE_COLUMNS,
    METRIC_COLUMNS,
    SUMMARY_COLUMNS,
    safe_cell,
    to_csv,
    to_summary_csv,
)

from ._data import SECRET, response


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_metric_columns_snapshot() -> None:
    assert METRIC_COLUMNS == (
        "case_id",
        "metric_id",
        "name",
        "method",
        "status",
        "score",
        "value",
        "unit",
        "passed",
        "threshold",
        "required",
        "weight",
        "error_code",
        "metric_version",
        "judge_source",
    )
    rows = _rows(to_csv(response()))
    assert rows[0] == list(METRIC_COLUMNS)
    assert rows[1] == [
        "a",
        "text.exact",
        "text.exact",
        "rule",
        "scored",
        "1.0",
        "",
        "",
        "true",
        "0.5",
        "false",
        "1.0",
        "",
        "1",
        "live",
    ]
    assert rows[2][5:8] == ["-0.5", "120.0", "ms"]  # numbers are not formula-guarded
    assert rows[3][4] == "error" and rows[3][12] == "missing_input"
    assert rows[4][4] == "skipped"
    assert len(rows) == 5


def test_case_and_summary_columns() -> None:
    rows = _rows(to_csv(response(), per="case"))
    assert rows[0] == list(CASE_COLUMNS)
    assert rows[1] == ["a", "0.25", "true", "2", "2", "0", "0", ""]
    assert rows[2][1:] == ["", "false", "2", "0", "1", "1", "missing_input"]
    summary = _rows(to_summary_csv(response()))
    assert summary[0] == list(SUMMARY_COLUMNS)
    head = dict(zip(summary[0], summary[1], strict=True))
    assert head["count"] == "2" and head["errors"] == "1" and head["mean"] == "1.0"
    assert head["std"] == "0.0" and head["pass_rate"] == "1.0"
    assert summary[2][-1] == "ms"
    assert to_csv(response(), per="summary") == to_summary_csv(response())


def test_injection_guard() -> None:
    rows = _rows(to_csv(response(), include_text=True))
    assert rows[3][0] == "'=cmd|' /C calc'!A0"
    assert rows[2][-1] == "'=HYPERLINK(1)"
    for bad in ("=1", "+1", "-1", "@SUM", "\tx", "\rx"):
        assert safe_cell(bad) == "'" + bad
    assert safe_cell("ok") == "ok" and safe_cell(None) == "" and safe_cell(-1) == "-1"
    assert safe_cell(True) == "true"


def test_include_text_gating() -> None:
    plain = to_csv(response())
    assert SECRET not in plain and "reason" not in _rows(plain)[0]
    full = to_csv(response(), include_text=True)
    assert _rows(full)[0][-1] == "reason" and SECRET in full


def test_path_written(tmp_path) -> None:
    out = tmp_path / "r.csv"
    text = to_csv(response(), path=out, per="case")
    assert out.read_text(encoding="utf-8") == text


def test_bad_per() -> None:
    with pytest.raises(ValueError):
        to_csv(response(), per="nope")  # type: ignore[arg-type]
