"""Human-readable Markdown report."""

from __future__ import annotations

from ..types.request import EvalResponse


def _fmt(x: float | None, digits: int = 3) -> str:
    return "–" if x is None else f"{x:.{digits}f}"


def to_markdown(response: EvalResponse) -> str:
    v, a = response.verdict, response.aggregate
    lines = [
        "# Evaluation report",
        "",
        f"- **Verdict:** `{v.status}`" + (f" → action `{v.action}`" if v.action else ""),
        f"- **Aggregate score:** {_fmt(a.score)} · **pass rate:** {_fmt(a.pass_rate)} · "
        f"**error rate:** {_fmt(a.error_rate)} · **cases:** {a.cases}",
        f"- **Stage:** `{response.stage}` · **suite_hash:** `{response.suite_hash}`",
        f"- **result_hash:** `{response.result_hash}`",
    ]
    if v.reasons:
        lines += ["", "**Reasons**", ""] + [f"- {r}" for r in v.reasons]
    lines += [
        "",
        "## Metrics",
        "",
        "| Metric | Mean | Pass rate | Scored | Errors | Skipped | Value p50 / p95 |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in a.metrics:
        values = (
            "–"
            if m.value_p50 is None
            else f"{_fmt(m.value_p50, 1)} / {_fmt(m.value_p95, 1)} {m.unit or ''}".strip()
        )
        lines.append(
            f"| `{m.name}` | {_fmt(m.mean)} | {_fmt(m.pass_rate)} | {m.scored}/{m.count} "
            f"| {m.errors} | {m.skipped} | {values} |"
        )
    lines += ["", "## Cases", "", "| Case | Score | Passed | Notes |", "|---|---|---|---|"]
    for c in response.results:
        notes = "; ".join(f"{r.name}: {r.error.code}" for r in c.metrics if r.error is not None)
        passed = "–" if c.passed is None else ("yes" if c.passed else "no")
        lines.append(f"| `{c.case_id}` | {_fmt(c.score)} | {passed} | {notes} |")
    return "\n".join(lines) + "\n"
