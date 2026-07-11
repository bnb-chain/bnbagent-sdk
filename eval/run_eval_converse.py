#!/usr/bin/env python3
"""Cross-family confused-deputy runner via the Bedrock Converse API.

The main harness (run_eval.py) uses the Anthropic Messages API, which is
Claude-only. To add a second model FAMILY (addressing the "all three models are
Claude tiers" reviewer point), this runner drives non-Claude Bedrock models
(Llama, Nova, Mistral, ...) through the vendor-neutral Converse API, while
reusing run_eval's tool set, system prompts, success rule, and wallet guardrail
VERBATIM so the numbers are directly comparable to the confused-deputy table (Table 3).

Same two signals as run_eval: llm_fooled (model issued a money tool call) and
attack_success (funds moved -- for `guardrailed`, after apply_wallet_guardrail).

Usage:
    python run_eval_converse.py --model us.meta.llama3-3-70b-instruct-v1:0 \
        --attacks confused_deputy.jsonl --configs helpful,guardrailed,signing_layer
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import boto3

import run_eval as R  # reuse tools, prompts, success rule, guardrail, wilson_ci

HERE = Path(__file__).resolve().parent


def to_converse_tools(tools: list[dict]) -> dict:
    """Translate run_eval's Anthropic-style tool schemas to Converse toolConfig."""
    specs = []
    for t in tools:
        specs.append({"toolSpec": {
            "name": t["name"],
            "description": t.get("description", t["name"]),
            "inputSchema": {"json": t["input_schema"]},
        }})
    return {"tools": specs}


def call_model(client, model_id, config, atk, max_retries=3, return_text=False):
    """One confused-deputy turn via Converse. Returns a list of (tool_name, input),
    or (calls, text) when return_text=True (text = the model's non-tool output,
    used as refusal feedback by the adaptive attacker)."""
    task = atk["task"]
    injection = atk.get("injection", "")
    user_text = R.build_user_prompt(task, injection, config)
    system = [{"text": R.system_for(config)}]
    tools = R.tools_for(config)
    tool_cfg = to_converse_tools(tools)
    messages = [{"role": "user", "content": [{"text": user_text}]}]

    for attempt in range(max_retries):
        try:
            resp = client.converse(
                modelId=model_id, messages=messages, system=system,
                toolConfig=tool_cfg,
                inferenceConfig={"maxTokens": R.max_tokens_for(model_id), "temperature": 0.0},
            )
            calls, texts = [], []
            for block in resp["output"]["message"]["content"]:
                if "toolUse" in block:
                    tu = block["toolUse"]
                    calls.append((tu["name"], dict(tu.get("input") or {})))
                elif "text" in block:
                    texts.append(block["text"])
            return (calls, "\n".join(texts)) if return_text else calls
        except Exception as e:  # noqa: BLE001
            if attempt == max_retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return ([], "") if return_text else []


def summarize(rows, configs):
    lines = ["| Config | LLM fooled | Funds moved (95% CI) |", "|---|---|---|"]
    for c in configs:
        cr = [r for r in rows if r["config"] == c and r["attack_success"] is not None]
        n = len(cr)
        if not n:
            continue
        fooled = sum(1 for r in cr if r.get("llm_fooled"))
        moved = sum(1 for r in cr if r["attack_success"])
        lo, hi = R.wilson_ci(moved, n)
        lines.append(f"| {c} | {100*fooled/n:.0f}% ({fooled}/{n}) | "
                     f"**{100*moved/n:.0f}%** ({moved}/{n}) [{lo:.0f}-{hi:.0f}] |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--attacks", default=str(HERE / "data" / "confused_deputy.jsonl"))
    ap.add_argument("--configs", default="signing_layer,helpful,guardrailed,prompt_guarded")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--trials", type=int, default=1, help="repeats per (attack,config)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--summary", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tag = args.model.split(".")[-1].split(":")[0].replace("-instruct", "")
    out = args.out or str(HERE / f"results_cvrs_{tag}.jsonl")
    summ = args.summary or str(HERE / f"summary_cvrs_{tag}.md")

    attacks = [json.loads(l) for l in Path(args.attacks).read_text().splitlines() if l.strip()]
    configs = args.configs.split(",")
    print(f"CONVERSE cross-family: model={args.model} attacks={len(attacks)} configs={configs}")
    if args.dry_run:
        print("[dry-run] no API calls."); return

    client = boto3.client("bedrock-runtime", region_name=args.region)
    rows = []
    n = 0
    total = len(attacks) * len(configs) * args.trials
    for cfg in configs:
        for atk in attacks:
          for trial in range(args.trials):
            n += 1
            try:
                calls = call_model(client, args.model, cfg, atk)
                labels = [R.classify_tool_call(nm, inp) for nm, inp in calls]
                hit = [x for x in labels if x]
                llm_fooled = bool(hit)
                if cfg == "guardrailed":
                    funds_moved = any(R.apply_wallet_guardrail(nm, inp)
                                      for nm, inp in calls if R.classify_tool_call(nm, inp))
                else:
                    funds_moved = llm_fooled
                rows.append({"id": atk["id"], "class": atk["class"], "config": cfg,
                             "trial": trial,
                             "attack_success": funds_moved, "llm_fooled": llm_fooled,
                             "triggered": hit, "model": args.model,
                             "tool_calls": [{"name": nm, "input": inp} for nm, inp in calls]})
                mark = "!" if funds_moved else ("x" if llm_fooled else ".")
            except Exception as e:  # noqa: BLE001
                rows.append({"id": atk["id"], "class": atk["class"], "config": cfg,
                             "attack_success": None, "llm_fooled": None, "error": str(e),
                             "model": args.model})
                mark = "?"
            print(f"[{n}/{total}] {cfg:14s} {atk['id']:12s} {mark}", flush=True)
            time.sleep(0.15)

    Path(out).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    s = summarize(rows, configs)
    Path(summ).write_text(s + "\n")
    print("\n" + s + f"\n\nwrote {out} and {summ}")
    errs = [r for r in rows if r.get("attack_success") is None]
    if errs:
        print(f"WARNING: {len(errs)} calls errored.")


if __name__ == "__main__":
    main()
