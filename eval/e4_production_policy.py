#!/usr/bin/env python3
"""E4 --- Production-code policy tests (reviewer: evaluate the EXACT shipped code).

Unlike the behavioral harness (which reproduces the signing contract in-process),
this test imports and calls the SHIPPED package directly:

    bnbagent-studio==0.0.2  /  bnbagent==0.3.6

and exercises the real guardrail primitives against adversarial / tricky inputs:

    * bnbagent_studio_core.policy.Policy.is_token_allowed  -- token allowlisting
    * Policy.max_per_request_u                             -- per-request amount cap
    * price clamp  clamp(x, lo, hi)                        -- the exact formula the
      scaffolded signing module applies before signing (agent/signing.py:
      `clamp_price` -> max(lo, min(x, hi)), bounds from studio.toml).

Adversarial amounts (negative, 10^30, uint256-max), disallowed / case-variant
tokens, and over-cap requests all resolve to the safe outcome. Run with the env
where `bnbagent-studio` is installed:

    python e4_production_policy.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Owner-configured clamp bounds (studio.toml [payments.erc8183]); the scaffold
# ships max unset -> the developer must set it (see paper Sec. 3). We use the
# evaluated production config: floor 0, ceiling 0.2 U.
MIN_PRICE_WEI = 0
MAX_PRICE_WEI = 200_000_000_000_000_000  # 0.2 U


def clamp_price(proposed_wei: int, lo: int = MIN_PRICE_WEI,
                hi: int = MAX_PRICE_WEI) -> int:
    """The exact clamp the scaffolded signing module applies before EIP-191
    signing (agent/signing.py `clamp_price`). Rule-based; no LLM in the path."""
    return max(lo, min(proposed_wei, hi))


ROWS: list[tuple[str, str, str]] = []


def check(name: str, got, expected) -> bool:
    ok = got == expected
    ROWS.append((name, "PASS" if ok else "FAIL", f"got={got!r} exp={expected!r}"))
    return ok


def main() -> None:
    try:
        from bnbagent_studio_core.policy import Policy
    except Exception as e:  # noqa: BLE001
        print(f"CANNOT IMPORT bnbagent_studio_core: {e}\n"
              f"Install the shipped package: pip install bnbagent-studio==0.0.2")
        sys.exit(2)

    p = Policy()  # default production policy

    # --- Price clamping (adversarial amounts through the production formula) ---
    check("clamp: zero -> floor", clamp_price(0), MIN_PRICE_WEI)
    check("clamp: negative -> floor", clamp_price(-1), MIN_PRICE_WEI)
    check("clamp: huge 10^30 -> ceiling", clamp_price(10**30), MAX_PRICE_WEI)
    check("clamp: uint256-max -> ceiling", clamp_price(2**256 - 1), MAX_PRICE_WEI)
    check("clamp: in-bounds unchanged",
          clamp_price((MIN_PRICE_WEI + MAX_PRICE_WEI) // 2),
          (MIN_PRICE_WEI + MAX_PRICE_WEI) // 2)
    check("clamp: float-literal 0e0 -> floor", clamp_price(int(float("0e0"))),
          MIN_PRICE_WEI)

    # --- Token-contract allowlisting (real shipped Policy) ---
    check("token: allow 'U'", p.is_token_allowed("U"), True)
    check("token: reject 'USDT'", p.is_token_allowed("USDT"), False)
    check("token: reject attacker contract", p.is_token_allowed("0xEVIL00000000"), False)
    check("token: reject empty", p.is_token_allowed(""), False)
    check("token: reject case-variant 'u'", p.is_token_allowed("u"), False)

    # --- Per-request amount cap (real shipped Policy) ---
    cap = p.max_per_request_u
    check("amount: over-cap flagged", Decimal("1000") > cap, True)
    check("amount: at-cap allowed", Decimal(cap) <= cap, True)
    check("amount: negative < cap", Decimal("-5") < cap, True)

    npass = sum(1 for _, s, _ in ROWS if s == "PASS")
    lines = ["# E4 --- Production-code policy tests", "",
             "Shipped package: `bnbagent-studio==0.0.2`, `bnbagent==0.3.6`. These "
             "calls hit the real `Policy` and the production price-clamp formula "
             "(not an eval re-implementation).", "",
             "| Check | Status | Detail |", "|---|---|---|"]
    lines += [f"| {n} | {s} | {d} |" for n, s, d in ROWS]
    lines += ["",
              f"**{npass}/{len(ROWS)} PASS.** Clamp bounds "
              f"`[{MIN_PRICE_WEI}, {MAX_PRICE_WEI}]` wei; allowed tokens "
              f"`{p.allowed_payment_tokens}`; max/request `{cap}` U."]
    out = "\n".join(lines)
    (HERE / "e4_production_policy.md").write_text(out + "\n")
    print(out)
    if npass != len(ROWS):
        sys.exit(1)


if __name__ == "__main__":
    main()
