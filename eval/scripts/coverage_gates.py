#!/usr/bin/env python3
"""Fail if any gated path is below its line-coverage threshold.

Reads a coverage.py JSON report:

    .venv/bin/python -m pytest -q eval/tests --cov=bnbagent_eval --cov-report=json:coverage.json
    python3 eval/scripts/coverage_gates.py coverage.json

A gate is a path relative to the package root: a directory (every file below it, pooled) or a
single file. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import PurePosixPath

PACKAGE = "bnbagent_eval"

# path (relative to the package) -> minimum line coverage in percent
GATES: dict[str, float] = {
    "metrics/text/": 95.0,
    "metrics/format/": 95.0,
    "metrics/ops/": 95.0,
    "chain/": 95.0,
    "ports.py": 95.0,
    "metrics/onchain/": 95.0,
    "server/payment.py": 95.0,
    "server/policy.py": 95.0,
    "hashing.py": 95.0,
}


def _relative(path: str) -> str | None:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if PACKAGE not in parts:
        return None
    i = len(parts) - 1 - parts[::-1].index(PACKAGE)
    return "/".join(parts[i + 1 :])


def evaluate(report: dict, gates: dict[str, float]) -> list[tuple[str, float, float, int, int]]:
    """``(gate, threshold, percent, covered, statements)`` per gate."""
    files = {
        rel: data["summary"]
        for path, data in report.get("files", {}).items()
        if (rel := _relative(path)) is not None
    }
    rows = []
    for gate, threshold in gates.items():
        matched = [
            s
            for rel, s in files.items()
            if (rel.startswith(gate) if gate.endswith("/") else rel == gate)
        ]
        statements = sum(s["num_statements"] for s in matched)
        covered = sum(s["covered_lines"] for s in matched)
        percent = 100.0 * covered / statements if statements else 0.0
        rows.append((gate, threshold, percent, covered, statements))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", nargs="?", default="coverage.json")
    args = parser.parse_args(argv)
    try:
        with open(args.report, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"cannot read coverage report {args.report}: {exc}", file=sys.stderr)
        return 2
    failed = False
    for gate, threshold, percent, covered, statements in evaluate(report, GATES):
        if statements == 0:
            status, failed = "MISSING", True
        elif percent + 1e-9 < threshold:
            status, failed = "FAIL", True
        else:
            status = "ok"
        print(f"{status:>7}  {gate:<22} {percent:6.2f}% ({covered}/{statements}) >= {threshold}%")
    total = report.get("totals", {}).get("percent_covered")
    if total is not None:
        print(f"{'':>7}  {'TOTAL':<22} {total:6.2f}%")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
