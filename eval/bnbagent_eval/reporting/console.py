"""Plain-text console report (no extra dependencies)."""

from __future__ import annotations

from ..types.request import EvalResponse


def _f(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)
    ]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
    return "\n".join([line, sep, *body])


def to_console(response: EvalResponse) -> str:
    v, a = response.verdict, response.aggregate
    head = [
        f"verdict: {v.status.upper()}" + (f"  (action: {v.action})" if v.action else ""),
        f"score: {_f(a.score)}  pass rate: {_f(a.pass_rate)}  error rate: {_f(a.error_rate)}  "
        f"cases: {a.cases}  stage: {response.stage}",
    ]
    head += [f"  - {r}" for r in v.reasons]
    metrics = _table(
        ["metric", "mean", "pass", "scored", "err", "skip", "p50", "p95"],
        [
            [
                m.name,
                _f(m.mean),
                _f(m.pass_rate),
                f"{m.scored}/{m.count}",
                str(m.errors),
                str(m.skipped),
                _f(m.value_p50),
                _f(m.value_p95),
            ]
            for m in a.metrics
        ],
    )
    cases = _table(
        ["case", "score", "passed", "notes"],
        [
            [
                c.case_id,
                _f(c.score),
                "-" if c.passed is None else ("yes" if c.passed else "no"),
                "; ".join(f"{r.name}: {r.error.code}" for r in c.metrics if r.error),
            ]
            for c in response.results
        ],
    )
    tail = f"result_hash: {response.result_hash}\nsuite_hash:  {response.suite_hash}"
    return "\n".join(head) + "\n\n" + metrics + "\n\n" + cases + "\n\n" + tail + "\n"
