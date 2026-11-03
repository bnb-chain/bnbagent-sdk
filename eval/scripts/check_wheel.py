"""Wheel content check: run after ``python -m build`` in the CI build job.

    python scripts/check_wheel.py dist/*.whl

Checks: only ``bnbagent_eval/**`` plus dist-info; the prompt modules are included; no tests
or fixtures; ``License-Expression: Apache-2.0``; LICENSE and NOTICE shipped.
"""

from __future__ import annotations

import sys
import zipfile


def check(path: str) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        info = [n for n in names if ".dist-info/" in n]
        payload = [n for n in names if n not in info]
        stray = [n for n in payload if not n.startswith("bnbagent_eval/")]
        if stray:
            problems.append(f"files outside bnbagent_eval/: {stray[:5]}")
        if not any(n.startswith("bnbagent_eval/judges/prompts/") for n in payload):
            problems.append("prompt modules missing")
        if any("/tests/" in n or "/fixtures/" in n for n in payload):
            problems.append("tests or fixtures shipped")
        metadata = next((n for n in info if n.endswith("/METADATA")), None)
        text = wheel.read(metadata).decode() if metadata else ""
        if "License-Expression: Apache-2.0" not in text.splitlines():
            problems.append("License-Expression is not Apache-2.0")
        for lic in ("LICENSE", "NOTICE"):
            if not any(n.endswith(f".dist-info/licenses/{lic}") for n in info):
                problems.append(f"{lic} missing from dist-info/licenses")
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_wheel.py WHEEL...", file=sys.stderr)
        return 2
    failed = False
    for path in argv:
        problems = check(path)
        for p in problems:
            print(f"{path}: {p}", file=sys.stderr)
        failed |= bool(problems)
        if not problems:
            print(f"{path}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
