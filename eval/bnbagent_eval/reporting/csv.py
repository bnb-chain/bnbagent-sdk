"""CSV reports (stdlib ``csv`` only): per metric result, per case, and an aggregate summary.

Free text (``reason``) is only written when ``include_text=True``. Text cells that a spreadsheet
could read as a formula (leading ``=``, ``+``, ``-``, ``@``, tab or CR) get a leading ``'``.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Literal

from ..types.request import EvalResponse

Per = Literal["metric", "case", "summary"]

METRIC_COLUMNS: tuple[str, ...] = (
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
CASE_COLUMNS: tuple[str, ...] = (
    "case_id",
    "score",
    "passed",
    "metrics",
    "scored",
    "errors",
    "skipped",
    "error_codes",
)
# ETK score summary (count / errors / mean) plus our Welford stats and value percentiles.
SUMMARY_COLUMNS: tuple[str, ...] = (
    "metric_id",
    "name",
    "count",
    "scored",
    "errors",
    "skipped",
    "mean",
    "std",
    "min",
    "max",
    "pass_rate",
    "value_mean",
    "value_p50",
    "value_p95",
    "value_p99",
    "unit",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Render one cell; text that could be read as a formula is prefixed with ``'``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)  # numbers are ours, never a formula (e.g. "-0.5")
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _metric_rows(response: EvalResponse, include_text: bool) -> Iterable[list[Any]]:
    for case in response.results:
        for m in case.metrics:
            prov = m.provenance
            row: list[Any] = [
                case.case_id,
                m.metric_id,
                m.name,
                m.method,
                m.status,
                m.score,
                m.value,
                m.unit,
                m.passed,
                m.threshold,
                m.required,
                m.weight,
                m.error.code if m.error else None,
                prov.metric_version if prov else None,
                prov.judge_source if prov else None,
            ]
            if include_text:
                row.append(m.reason)
            yield row


def _case_rows(response: EvalResponse) -> Iterable[list[Any]]:
    for c in response.results:
        statuses = [m.status for m in c.metrics]
        codes = sorted({m.error.code for m in c.metrics if m.error is not None})
        yield [
            c.case_id,
            c.score,
            c.passed,
            len(c.metrics),
            statuses.count("scored"),
            statuses.count("error"),
            statuses.count("skipped"),
            ";".join(codes) or None,
        ]


def _summary_rows(response: EvalResponse) -> Iterable[list[Any]]:
    for m in response.aggregate.metrics:
        yield [getattr(m, col) for col in SUMMARY_COLUMNS]


def _render(header: Sequence[str], rows: Iterable[Sequence[Any]], path: str | Path | None) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([safe_cell(v) for v in row] for row in rows)
    text = buf.getvalue()
    if path is not None:
        Path(path).write_text(text, encoding="utf-8", newline="")
    return text


def to_csv(
    response: EvalResponse,
    *,
    path: str | Path | None = None,
    per: Per = "metric",
    include_text: bool = False,
) -> str:
    """CSV of ``response``: one row per metric result, per case, or per aggregate metric."""
    if per == "metric":
        header = METRIC_COLUMNS + (("reason",) if include_text else ())
        return _render(header, _metric_rows(response, include_text), path)
    if per == "case":
        return _render(CASE_COLUMNS, _case_rows(response), path)
    if per == "summary":
        return _render(SUMMARY_COLUMNS, _summary_rows(response), path)
    raise ValueError(f"per must be 'metric', 'case' or 'summary', not {per!r}")


def to_summary_csv(response: EvalResponse, *, path: str | Path | None = None) -> str:
    """ETK-style score summary (count, errors, mean) plus Welford std / min / max."""
    return to_csv(response, path=path, per="summary")
