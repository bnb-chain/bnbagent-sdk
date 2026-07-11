#!/usr/bin/env python3
"""Re-score saved benign-utility results with the corrected completion logic
(no Bedrock calls). The first run used a buggy check that reused
apply_wallet_guardrail for `requote` (always False -> all legit quotes wrongly
counted blocked). This recomputes `completed` from the saved tool_calls.

Usage: python rescore_benign.py results_benign_<model>.jsonl [...]
"""
import json
import sys
from pathlib import Path

import benign_utility as B  # fixed completed()


def rescore(path: Path):
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    for r in rows:
        if r.get("completed") is None:  # errored row, leave as-is
            continue
        tcs = [(tc["name"], tc["input"]) for tc in r.get("tool_calls", [])]
        issued, completed = B.completed(r["config"], r["expect"], tcs)
        r["issued_expected"] = issued
        r["completed"] = completed
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summ = B.summarize(rows)
    summ_path = path.parent / path.name.replace("results_", "summary_").replace(".jsonl", ".md")
    summ_path.write_text(summ + "\n")
    print(f"=== {path.name} ===")
    print(summ)
    print()


if __name__ == "__main__":
    for p in sys.argv[1:]:
        rescore(Path(p))
