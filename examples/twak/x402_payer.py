"""Delegated x402 payments with a TWAK wallet — quote, precheck, optionally pay.

twak's x402 client is a complete HTTP buyer: it discovers the 402 challenge,
signs the EIP-3009/Permit2 authorization INSIDE its own process and settles.
So a TWAK wallet does not plug into ``X402Signer`` (no ``sign.typed_data`` —
see quickstart.py gate 2); it plugs in one level up, as a *delegated payer*:

    payer = wallet.make_x402_payer(...)   # -> TwakX402Payer
    payer.quote(url)                      # read-only challenge fetch
    payer.request(url, max_payment=...)   # quote -> precheck -> pay

This script uses your REAL twak wallet (no ``home`` override): quoting is
free, and the default mode NEVER pays — every payment-shaped call is either
read-only or constructed to be rejected by the payer's precheck before any
payment could occur. Actual payment hides behind explicit flags.

Prerequisites:
    - A configured twak wallet (>= v0.18.0 CLI): credentials + wallet +
      password reachable (TWAK_WALLET_PASSWORD or OS keychain).
    - Internet access (the quote endpoints are live x402 sellers).
    - twak pays mainnet routes only (it rejects testnet routes as
      "no supported route") — paid mode moves REAL funds (Base USDC here).

Usage:
    # FREE mode (default): quotes + an offline precheck rejection. No spend.
    python examples/twak/x402_payer.py

    # PAID mode: actually buy a resource. SPENDS REAL FUNDS.
    python examples/twak/x402_payer.py \
        --pay https://skills.onesource.io/api/chain/chain-id --max-payment 1000
"""

from __future__ import annotations

import argparse
import os
import sys

from bnbagent.wallets import TWAKProvider
from bnbagent.x402 import (
    SessionBudgetTracker,
    X402Quote,
    X402RecipientMismatchError,
)

TWAK_BIN = os.environ.get("TWAK_BIN", "twak")

# A Bazaar-spec x402 seller: ~0.001 USDC (6 decimals) on Base for a chain-id
# lookup. Cheap enough to be the canonical paid-mode demo target.
ONESOURCE_URL = "https://skills.onesource.io/api/chain/chain-id"
# The x402.org reference endpoint sells on base-sepolia — a TESTNET route.
# twak filters out routes its client cannot pay, which makes this endpoint
# the live demonstration of empty-accepts filtering.
X402_ORG_URL = "https://www.x402.org/protected"

# USDC on Base (eip155:8453) — the asset the onesource route quotes in.
# For EIP-3009 routes this address is also the EIP-712 verifyingContract,
# which is why the payer's expected_asset pin is the SigningPolicy domain
# allowlist relocated to the quote terms.
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Session cap: at most 0.05 USDC (atomic, 6dp) across this payer's lifetime,
# no matter how many requests run. Shared shape with the X402Signer path.
SESSION_CAP_ATOMIC = 50_000


def banner(msg: str) -> None:
    print()
    print("=" * 64)
    print(f" {msg}")
    print("=" * 64)


def print_quote(quote: X402Quote) -> None:
    """Pretty-print the parsed X402Quote (the modeled fields, not raw JSON)."""
    print(f"  resource url : {quote.url}")
    print(f"  description  : {quote.description}")
    print(f"  options      : {len(quote.accepts)}")
    for i, opt in enumerate(quote.accepts):
        star = " (preferred)" if opt.preferred else ""
        print(f"  [{i}]{star}")
        print(f"      network           = {opt.network}")
        print(f"      scheme            = {opt.scheme}")
        print(f"      asset             = {opt.asset} ({opt.token_name})")
        print(f"      amount            = {opt.amount} atomic units")
        print(f"      payTo             = {opt.pay_to}")
        print(f"      transferMethod    = {opt.transfer_method}")
        print(f"      maxTimeoutSeconds = {opt.max_timeout_seconds}")


def free_mode(wallet: TWAKProvider, tracker: SessionBudgetTracker) -> int:
    payer = wallet.make_x402_payer(session_budget=tracker)
    failures = 0

    # ── 1. quote a live Bazaar-spec endpoint (Base USDC) ──────────────────
    banner("1. quote — live endpoint, Base USDC routes (read-only)")
    # quote() is read-only by contract: it never creates a wallet (a price
    # check must not mint an on-chain identity, INV-4) and never pays.
    try:
        quote = payer.quote(ONESOURCE_URL)
        print_quote(quote)
    except RuntimeError as e:
        failures += 1
        print(f"  endpoint unreachable right now — skipping: {str(e)[:160]}")

    # ── 2. quote the x402.org reference endpoint (route filtering) ────────
    banner("2. quote — x402.org/protected (empty-accepts route filtering)")
    # This endpoint advertises base-sepolia (testnet) routes. The twak client
    # pre-filters routes it cannot pay, leaving accepts == []. NOTE the
    # v0.18.0 CLI also EXITS NON-ZERO on that outcome, so the SDK surfaces it
    # as a RuntimeError rather than an empty X402Quote — both shapes mean the
    # same thing: nothing here is payable by this wallet.
    try:
        quote = payer.quote(X402_ORG_URL)
        print_quote(quote)
        if not quote.accepts:
            print("  -> accepts is EMPTY: all advertised routes were filtered out")
    except RuntimeError as e:
        detail = str(e)
        if "none on chains this client supports" in detail:
            print("  -> CLI exited non-zero with: \"…none on chains this client")
            print("     supports.\" — the endpoint's (testnet) routes were all")
            print("     filtered; there is no payable route for this wallet.")
        else:
            failures += 1
            print(f"  endpoint unreachable right now — skipping: {detail[:160]}")

    # ── 3. precheck rejection, offline — provably no payment ──────────────
    banner("3. precheck rejection — wrong recipient pin (no payment possible)")
    # request() re-quotes via the CLI (read-only), then runs the five-point
    # precheck on the quoted terms BEFORE any payment. We make payment
    # impossible three independent ways:
    #   (1) expected_pay_to is pinned to an address the seller cannot quote,
    #       so precheck #1 (recipient byte-equality) raises first;
    #   (2) even if it somehow passed, max_payment=0 fails precheck #3
    #       (amount <= max_payment) for any non-free route;
    #   (3) even if BOTH prechecks were bypassed, `twak x402 request
    #       --max-payment 0` is the wallet-layer hard cap, enforced inside
    #       twak itself.
    # The only network traffic is the quote; `twak x402 request` is never
    # invoked, and the session budget is never debited (reserve happens
    # after the precheck).
    strict_payer = wallet.make_x402_payer(
        session_budget=tracker,
        expected_pay_to="0x" + "42" * 20,  # deliberately not the seller
    )
    try:
        strict_payer.request(ONESOURCE_URL, max_payment=0)
        print("  UNEXPECTED: request() did not raise")
        return 1
    except X402RecipientMismatchError as e:
        print(f"  X402RecipientMismatchError (precheck #1, before paying):\n    {e}")
    except RuntimeError as e:
        failures += 1
        print(f"  endpoint unreachable right now — skipping: {str(e)[:160]}")
    print(f"  session budget debited so far: {tracker.spent(BASE_USDC)} atomic (expect 0)")

    banner("FREE MODE COMPLETE — no payment was made")
    if failures:
        print(f"note: {failures} live endpoint(s) were unreachable; rerun later")
    return 0


def paid_mode(
    wallet: TWAKProvider,
    tracker: SessionBudgetTracker,
    *,
    url: str,
    max_payment: int,
    method: str,
    body: str | None,
) -> int:
    banner("PAID MODE — THIS SPENDS REAL FUNDS")
    print("  !! You passed --pay. twak will sign and settle a REAL x402")
    print(f"  !! payment from wallet {wallet.address}")
    print(f"  !! url         = {url}")
    print(f"  !! max payment = {max_payment} atomic units of the quoted asset")
    print("  !! (twak pays mainnet routes only; gas-free via EIP-3009/Permit2)")

    payer = wallet.make_x402_payer(session_budget=tracker)

    # Show the terms first, then pay. request() re-quotes and prechecks the
    # terms again internally (TOCTOU narrowing via --prefer-* + --max-payment).
    quote = payer.quote(url, method=method, body=body)
    print_quote(quote)

    result = payer.request(url, max_payment=max_payment, method=method, body=body)
    banner("payment result")
    print(f"  success     : {result.success}")
    print(f"  amount      : {result.amount} atomic units (the QUOTED amount —")
    print("                the CLI surfaces no settlement receipt, gaps S-7)")
    print(f"  asset       : {result.asset}")
    print(f"  network     : {result.network}")
    print(f"  payTo       : {result.pay_to}")
    print(f"  transaction : {result.transaction} (best-effort from the body)")
    print(f"  response    : {str(result.response)[:200]}")
    if result.asset:
        print(
            f"  session budget debit: {tracker.spent(result.asset)} atomic of "
            f"{result.asset}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="x402 delegated-payer demo (twak). Default mode is FREE."
    )
    parser.add_argument("--pay", metavar="URL", help="actually PAY this endpoint")
    parser.add_argument(
        "--max-payment", type=int, metavar="ATOMIC",
        help="hard per-payment cap in atomic token units (required with --pay)",
    )
    parser.add_argument("--method", default="GET", help="HTTP method (default GET)")
    parser.add_argument("--body", default=None, help="request body for POST/PUT")
    args = parser.parse_args()

    print("x402 delegated payer demo — TWAK wallet")
    print(f"twak binary: {TWAK_BIN}")

    # No `home` override: this is your real, funded twak wallet. Reading the
    # address needs the wallet password (env or keychain) — quoting itself
    # would not, but the prints below want the address.
    wallet = TWAKProvider(chain="bsc", twak_bin=TWAK_BIN)
    print(f"wallet: {wallet.address}")

    # One tracker shared across every payer in this session: even repeated
    # --pay runs inside one process cannot exceed the per-token cap.
    tracker = SessionBudgetTracker(caps={BASE_USDC: SESSION_CAP_ATOMIC})

    if args.pay:
        if args.max_payment is None:
            parser.error("--pay requires --max-payment <atomic units>")
        return paid_mode(
            wallet, tracker,
            url=args.pay, max_payment=args.max_payment,
            method=args.method, body=args.body,
        )
    return free_mode(wallet, tracker)


if __name__ == "__main__":
    sys.exit(main())
