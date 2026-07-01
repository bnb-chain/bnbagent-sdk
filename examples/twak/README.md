# TWAK wallet examples

Demos for `TWAKProvider` — the self-broadcasting wallet backed by the
Trust Wallet Agent Kit (`twak`) CLI, **v0.19.0 minimum**. Each script is a
runnable companion to one section of
[`bnbagent/wallets/README.md`](../../bnbagent/wallets/README.md) (the TWAK
section is the authoritative reference these scripts demonstrate).

## The scripts

| Script | What it shows | Needs | Costs |
|---|---|---|---|
| `quickstart.py` | Custody (throwaway wallet in a tempdir home), capabilities vs EVM, every `UnsupportedWalletOperation` guard rail live, a real `sign_message` + ecrecover round-trip, and the deployment recipe (`materialize_twak_home` + `expected_address` + `auto_create=False`) | twak CLI on PATH (or `TWAK_BIN`) + twak API credentials (`~/.twak/credentials.json` or `TWAK_ACCESS_ID`/`TWAK_HMAC_SECRET`) | **Nothing.** No funds, no chain RPC. Your real `~/.twak` wallet is never touched. |
| `x402_payer.py` | The delegated x402 path (`wallet.make_x402_payer()`): quoting two live endpoints, the empty-accepts route filtering, and a precheck rejection that provably cannot pay. Paid mode behind `--pay`/`--max-payment`. | Your real configured twak wallet + internet | **Free by default** (quotes only). `--pay` spends real funds (mainnet routes only, e.g. Base USDC). |
| `e2e_smoke.py` | The bsctestnet 13-intent lifecycle smoke (closes the design-doc backlog item): jobs A (happy/settle), B (cancel-open), C (dispute → stalemate → refund), the positive twak `submit` with `deliverable_url` opt_params (REQ-1, shipped v0.19.0), ERC-8004 register with atomic metadata | Funded twak wallet on bsctestnet + `.env` (see `.env.example`) | **Testnet BNB** (~15 txs of gas) + **0.02 test-U** escrow (both return: job A self-deals, job C refunds). Runtime ≈ dispute window + ~10 min. |

Run from the repo root, e.g. `python examples/twak/quickstart.py`.

## The role matrix

twak executes a fixed command menu and signs only inside its own process.
Per ERC-8183 role:

| Role | twak | Notes |
|---|---|---|
| client / buyer (`create_job → … → fund`, `dispute`, `settle`, `claim_refund`) | ✅ | the full lifecycle these scripts drive |
| provider / seller (`submit`) | ✅ (>= v0.19.0) | `submit --opt-params` passes the `deliverable_url` JSON through raw (REQ-1, shipped v0.19.0; on-chain proof: bsctestnet job 150). In `e2e_smoke.py` the twak wallet plays both sides as a self-deal — a host has exactly **one** twak wallet (per HOME), so two *distinct* twak parties on one machine are impossible; that one-wallet-per-host reality (not REQ-1) is why other examples keep the counterparty on EVM. |
| voter (`vote_reject`) | ⚠️ | mechanically supported, but voting needs an on-chain whitelist entry the smoke wallet doesn't have; an optional `VOTER_PRIVATE_KEY` casts one (non-flipping) vote |
| x402 buyer | ✅ | via the delegated payer (mainnet routes only; testnet routes are being worked on upstream) |

## Known CLI quirks the scripts work around (re-verified on v0.19.0)

- `twak wallet create` **requires `--password` on argv** (env
  `TWAK_WALLET_PASSWORD` covers unlock only), so the SDK's INV-1-clean
  `create_wallet()` fails against this build; `quickstart.py` falls back to
  a direct CLI create for its throwaway wallet. (Gaps S-8.)
- `twak wallet status` exits 0 even with **no wallet configured** (the
  `agentWallet` field is the real signal — output shape unchanged in
  v0.19.0), so the `auto_create=False` materialize-pointing error is masked
  by the raw CLI "No wallet found" error (still fail-closed — no wallet is
  ever silently minted).
- `twak x402 quote` **exits non-zero** when route filtering leaves
  `accepts: []` (e.g. testnet-only endpoints), so the SDK surfaces a
  `RuntimeError` instead of an empty `X402Quote`. (Gaps S-9.)

Fixed in v0.19.0 and no longer worked around: the empty-optParams `submit`
(REQ-1) and the missing `--expected-budget` on `fund` (S-2) — the scripts now
exercise both natively.

## Further reading

- [`docs/twak.md`](../../docs/twak.md) — twak capability reference: supported
  ERC-8004 / ERC-8183 / x402 methods, the delegated x402 payer, boundaries,
  and contract addresses.
- [`bnbagent/wallets/README.md`](../../bnbagent/wallets/README.md) — TWAK
  provider reference: signing model, capability sets, custody, deployment.
