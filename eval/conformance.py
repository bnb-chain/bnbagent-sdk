#!/usr/bin/env python3
"""ALP conformance suite — offline-testable checks (spec §13.2).

Covers the parts of the ALP conformance checklist that do NOT require a live
deploy or on-chain writes: the lifecycle state machine (valid + forbidden
transitions, §4.1--4.2), guardrail-config schema (Appendix A.2), payment-log
schema (A.3), and the A2A Agent-Card field mapping (§10.1). Checks that DO need
a live agent + testnet (self-funding verification payment at T3, on-chain
identity--wallet binding, suspension latency) are listed as `SKIP (needs live
system)` so the report is honest about coverage.

This is the "released test suite" the paper's §5.4 cites. It is a reference
implementation of the checklist, runnable with no credentials.

Usage:
    python conformance.py                 # run, print report
    python conformance.py --md out.md     # also write a markdown summary
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# --- Lifecycle state machine (spec §4.1) -----------------------------------
STATES = ["DRAFT", "PROVISIONED", "REGISTERED", "ACTIVE", "SUSPENDED", "RETIRED"]
# Allowed transitions T1..T6 (spec §4.2 + the diagram §4.1)
ALLOWED = {
    ("DRAFT", "PROVISIONED"),        # T1
    ("PROVISIONED", "REGISTERED"),   # T2
    ("REGISTERED", "ACTIVE"),        # T3
    ("ACTIVE", "SUSPENDED"),         # T4
    ("SUSPENDED", "ACTIVE"),         # T5
    ("ACTIVE", "RETIRED"),           # T6
    ("SUSPENDED", "RETIRED"),        # T6
}


def is_valid_transition(src: str, dst: str) -> bool:
    return (src, dst) in ALLOWED


# --- Guardrails config schema (Appendix A.2) --------------------------------
A2_REQUIRED = {"balance_threshold", "refill_amount", "daily_spend_cap",
               "currency", "provider_allowlist", "confirmed_by", "confirmed_at",
               "signature"}


def validate_guardrails(doc: dict) -> list[str]:
    errs = [f"missing {k}" for k in A2_REQUIRED if k not in doc]
    # amounts are decimal strings, never floats (spec Appendix A preamble)
    for k in ("balance_threshold", "refill_amount", "daily_spend_cap"):
        if k in doc and not isinstance(doc[k], str):
            errs.append(f"{k} must be a decimal string, not {type(doc[k]).__name__}")
    if "currency" in doc:
        for f in ("chain_id", "address", "symbol", "decimals"):
            if f not in (doc["currency"] or {}):
                errs.append(f"currency.{f} missing")
    if "provider_allowlist" in doc and not isinstance(doc["provider_allowlist"], list):
        errs.append("provider_allowlist must be an array")
    return errs


# --- Payment log entry schema (Appendix A.3) --------------------------------
A3_REQUIRED = {"ts", "agent_id", "payee", "amount", "currency", "rail",
               "purpose", "tx_ref", "balance_after", "guardrail_snapshot"}
A3_PURPOSES = {"llm_refill", "data_purchase", "task_settlement"}


def validate_payment_log(entry: dict) -> list[str]:
    errs = [f"missing {k}" for k in A3_REQUIRED if k not in entry]
    # purpose must be a known tag or an explicitly "other registered value"
    if "purpose" in entry and entry["purpose"] not in A3_PURPOSES \
            and not str(entry["purpose"]).strip():
        errs.append("purpose empty")
    return errs


# --- A2A Agent Card mapping (§10.1) -----------------------------------------
# The mapping a conforming impl MUST be able to produce from an ALP identity.
A2A_CARD_FIELDS = {"name", "description", "url", "skills", "securitySchemes"}
A2A_EXTENSIONS = {"alp.identity", "alp.payment"}


def build_agent_card(identity: dict) -> dict:
    """Reference mapping ALP identity metadata (A.1) -> A2A Agent Card."""
    return {
        "name": identity.get("name"),
        "description": identity.get("description"),
        "url": identity.get("endpoint"),
        "skills": [c for c in identity.get("capabilities", [])],
        "securitySchemes": identity.get("securitySchemes", {}),
        "extensions": {
            "alp.identity": {
                "alp_version": identity.get("alp_version"),
                **(identity.get("identity") or {}),
                "wallet": identity.get("wallet"),
                "metadata_uri": identity.get("metadata_uri"),
            },
            "alp.payment": identity.get("pricing_hint", {}),
        },
    }


# --- Test cases -------------------------------------------------------------
def run() -> list[tuple[str, str, str]]:
    """Returns list of (check, status, detail). status in PASS/FAIL/SKIP."""
    r = []

    # 1. Every allowed transition is accepted
    ok = all(is_valid_transition(s, d) for (s, d) in ALLOWED)
    r.append(("T-order: allowed transitions accepted", "PASS" if ok else "FAIL",
              f"{len(ALLOWED)} transitions"))

    # 2. Forbidden transitions are rejected (a sample of illegal jumps)
    forbidden = [("DRAFT", "ACTIVE"), ("DRAFT", "REGISTERED"),
                 ("PROVISIONED", "ACTIVE"), ("REGISTERED", "SUSPENDED"),
                 ("RETIRED", "ACTIVE"), ("RETIRED", "DRAFT"),
                 ("ACTIVE", "DRAFT"), ("ACTIVE", "PROVISIONED")]
    bad = [(s, d) for (s, d) in forbidden if is_valid_transition(s, d)]
    r.append(("T-order: forbidden transitions rejected",
              "PASS" if not bad else "FAIL",
              f"{len(forbidden)} illegal jumps tested"
              + (f"; LEAKED {bad}" if bad else "")))

    # 3. RETIRED is terminal (no outgoing transitions)
    out = [(s, d) for (s, d) in ALLOWED if s == "RETIRED"]
    r.append(("T-order: RETIRED is terminal", "PASS" if not out else "FAIL",
              "no outgoing edges" if not out else str(out)))

    # 4. Guardrails schema: a well-formed doc passes
    good = {"balance_threshold": "10", "refill_amount": "5",
            "daily_spend_cap": "20",
            "currency": {"chain_id": 97, "address": "0x..", "symbol": "U", "decimals": 18},
            "provider_allowlist": [{"payee_address": "0x..", "label": "pieverse"}],
            "confirmed_by": "0xowner", "confirmed_at": "2026-06-30T00:00:00Z",
            "signature": "0xsig"}
    e = validate_guardrails(good)
    r.append(("A.2 guardrails: valid doc accepted", "PASS" if not e else "FAIL",
              "; ".join(e) or "ok"))

    # 5. Guardrails schema: float amount rejected, missing field rejected
    bad1 = dict(good); bad1["daily_spend_cap"] = 20.0  # float, not string
    bad2 = {k: v for k, v in good.items() if k != "signature"}
    ok5 = bool(validate_guardrails(bad1)) and bool(validate_guardrails(bad2))
    r.append(("A.2 guardrails: malformed docs rejected", "PASS" if ok5 else "FAIL",
              "float amount + missing signature both caught"))

    # 6. Payment log: valid entry passes
    plog = {"ts": "2026-06-30T00:00:00Z", "agent_id": "1", "payee": "0x..",
            "amount": "1.0", "currency": {"symbol": "U"}, "rail": "x402",
            "purpose": "llm_refill", "tx_ref": "0xtx", "balance_after": "9.0",
            "guardrail_snapshot": "0xhash"}
    e6 = validate_payment_log(plog)
    r.append(("A.3 payment log: valid entry accepted", "PASS" if not e6 else "FAIL",
              "; ".join(e6) or "ok"))

    # 7. Payment log: missing tx_ref rejected
    bad3 = {k: v for k, v in plog.items() if k != "tx_ref"}
    r.append(("A.3 payment log: missing tx_ref rejected",
              "PASS" if validate_payment_log(bad3) else "FAIL", "tx_ref required"))

    # 8. A2A Agent Card generated from identity has all required fields + exts
    ident = {"alp_version": "0.3.0", "name": "seller", "description": "does work",
             "status": "active", "identity": {"chain_id": 97, "registry_address": "0xreg", "agent_id": 1},
             "wallet": "0xF2b3", "endpoint": "https://x/.well-known/alp-agent.json",
             "capabilities": ["summarize"], "metadata_uri": "ipfs://cid"}
    card = build_agent_card(ident)
    missing = [f for f in A2A_CARD_FIELDS if f not in card]
    missing_ext = [x for x in A2A_EXTENSIONS if x not in card.get("extensions", {})]
    ok8 = not missing and not missing_ext
    r.append(("§10.1 A2A card: all fields + extensions present",
              "PASS" if ok8 else "FAIL",
              f"missing {missing+missing_ext}" if not ok8 else "name/url/skills/alp.identity/alp.payment"))

    # 9. A2A card: alp.identity carries the ERC-8004 coordinates a verifier needs
    ai = card["extensions"]["alp.identity"]
    ok9 = all(k in ai for k in ("chain_id", "registry_address", "agent_id", "wallet"))
    r.append(("§10.1 alp.identity: on-chain coords present",
              "PASS" if ok9 else "FAIL", "chain_id/registry/agent_id/wallet"))

    # --- checks that genuinely need a live system (honest SKIPs) ------------
    r.append(("T3: self-funding verification payment", "SKIP",
              "needs live agent + funding rail (D4)"))
    r.append(("§6: on-chain identity--wallet binding", "SKIP",
              "needs testnet write + WALLET_PASSWORD (D4)"))
    r.append(("§12: suspension latency under load", "SKIP",
              "needs deployed runtime (D4)"))
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--md", help="write a markdown report here")
    args = ap.parse_args()

    rows = run()
    npass = sum(1 for _, s, _ in rows if s == "PASS")
    nfail = sum(1 for _, s, _ in rows if s == "FAIL")
    nskip = sum(1 for _, s, _ in rows if s == "SKIP")
    testable = npass + nfail

    w = max(len(c) for c, _, _ in rows)
    print(f"{'Check':<{w}}  Status  Detail")
    for c, s, d in rows:
        print(f"{c:<{w}}  {s:<6}  {d}")
    print(f"\nOffline-testable: {npass}/{testable} passed. "
          f"{nskip} checks need the live system (D4).")

    if args.md:
        lines = ["# ALP conformance (offline suite, spec §13.2)", "",
                 f"**{npass}/{testable} offline checks passed** "
                 f"({nskip} require the live system, reported as SKIP).", "",
                 "| Check | Status | Detail |", "|---|---|---|"]
        for c, s, d in rows:
            lines.append(f"| {c} | {s} | {d} |")
        Path(args.md).write_text("\n".join(lines) + "\n")
        print(f"\nwrote {args.md}")


if __name__ == "__main__":
    main()
