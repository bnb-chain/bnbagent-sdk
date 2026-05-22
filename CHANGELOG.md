# Changelog

All notable changes to `bnbagent` SDK. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses
SemVer-flavoured 0.x.y versions where the minor bump signals a meaningful
feature set even when nothing existing is broken.

## [0.4.0] - 2026-05-25

First release that ships EIP-712 typed-data signing and a defense-in-depth
policy stack. See ADR #30 in the
[bnbchain-studio](https://github.com/bnb-chain/bnbchain-studio) repo
(`docs/decisions.md`) for the full design and threat model.

### Added — signing layer

- `bnbagent.signing` package. New `SigningPolicy` frozen dataclass +
  `check()` function + `PolicyViolation` exception. `strict_default()`
  factory ships fail-closed defaults:
  - `domain_allowlist`: U-token deployments on BSC mainnet (56) + testnet
    (97), recovered against the live `DOMAIN_SEPARATOR()` of each chain.
  - `primary_type_allowlist`: only EIP-3009 `TransferWithAuthorization` /
    `ReceiveWithAuthorization`.
  - `primary_type_denylist`: every unbounded Permit variant
    (`Permit` / `PermitSingle` / `PermitBatch`). Denylist takes precedence
    over allowlist so a misconfiguration can't re-enable Permit.
  - `max_validity_window_seconds = 600`, `max_future_validity_seconds = 900`.
- `SigningPolicy.extend()` for adding domains / primary types / overriding
  validity bounds without rewriting the defaults.
- `SigningPolicy.permissive()` — testing-only escape that refuses to
  construct when `ENV` / `ENVIRONMENT` env var matches `{prod, production,
  live, mainnet-prod}` (case-insensitive); pass `allow_in_production=True`
  for break-glass scenarios. Always logs a WARNING.
- `SigningPolicy.to_dict()` / `SigningPolicy.from_dict()` — round-trips
  through JSON / TOML for declarative-config storage; deterministic output
  (sorted lists).
- `SigningPolicy.__str__` / `__repr__` — multi-line human-readable summary
  for operator-facing tooling (e.g. `bcs wallet policy show`).

### Added — networks registry

- `bnbagent.networks` package. New `DeployedAddresses` snapshot dataclass
  plus a `BNB_CHAIN_ADDRESSES` table for BSC mainnet/testnet (14 addresses
  total: payment token + treasury + commerce/router proxy+impl + policy).
- `PAYMENT_TOKEN_EIP712_NAME = "United Stables"` and
  `PAYMENT_TOKEN_EIP712_VERSION = "1"` constants, verified on-chain.
- `get_address(chain_id)` / `known_payment_tokens()` helpers.

### Added — wallet layer

- `WalletProvider.sign_typed_data(domain, types, message)` — abstract EIP-712
  signing primitive. Implementations MUST gate every call through their
  configured `SigningPolicy`.
- `EVMWalletProvider` gains a `signing_policy: SigningPolicy | None = None`
  kwarg (defaults to `SigningPolicy.strict_default()`) and a `signing_policy`
  read-only property.
- `EVMWalletProvider._DANGEROUS_sign_typed_data_no_policy()` — explicit
  escape hatch for tests / trusted SDK code; logs a WARNING with the
  caller's filename:lineno on every invocation.
- `MPCWalletProvider.sign_typed_data` raises `NotImplementedError`. The
  SDK does not ship an in-process MPC implementation by design —
  production high-value flows should integrate an external MPC provider
  (Coinbase CDP, Fireblocks, Web3Auth, etc.) through a custom
  `WalletProvider` subclass.

### Added — x402 helper

- `bnbagent.x402` package. New `X402Signer(wallet, max_value_per_call=...,
  session_budget=...)` wrapper for x402 payment flows:
  - `sign_payment(domain, types, message, expected_to)` byte-equal
    recipient check (case-insensitive) defends against an upstream LLM
    altering the payee.
  - Per-call `max_value` cap rejects inflated 402 challenges before
    signing.
  - `SessionBudgetTracker` thread-safe cumulative cap; budget commits
    **only after** the underlying wallet sign succeeds, so a rejected
    sign never deducts.
  - Underlying `PolicyViolation` surfaces as `X402PolicyError` with the
    original chained via `__cause__`.

### Added — contract layer

- `ContractInterface(receipt_timeout=...)` — configurable transaction
  receipt timeout (default 300s; web3.py's own 120s default was too short
  on congested BNB Chain / paymaster paths).

### Added — tooling, examples, docs

- `tools/lint_capability.py` — minimum AST lint catching agent tool files
  that import `WalletProvider` / `EVMWalletProvider` /
  `MPCWalletProvider` directly. Per-function bypass with
  `# capability-ok: <reason>` on the def line; per-file bypass in the
  first 10 lines.
- `examples/security_e2e.py` — off-chain end-to-end security validation
  script (6 assertions covering default-allow, denylist, extend, X402Signer
  over-value, X402Signer recipient mismatch).
- `examples/x402_buyer_demo.py` — complete x402 buyer loop with mock 402
  server (GET → 402 → EIP-3009 sign via X402Signer → retry with X-PAYMENT
  envelope). Off-chain only.
- `README.md` Security section expanded with canonical examples + a
  decision tree for "what to configure when".

### Tier 1 public API additions

```python
from bnbagent import (
    SigningPolicy,
    PolicyViolation,
    X402Signer,
)
```

Existing Tier 1 symbols (`BNBAgent`, `EVMWalletProvider`, `ERC8183Client`,
etc.) unchanged.

### Notes on upgrade

`sign_typed_data` has never appeared in a prior 0.3.x release (verified
via `git log -S "sign_typed_data"`). 0.4.0 is therefore a **feature
release, not a breaking change** — all 0.3.x APIs (`sign_message`,
`sign_transaction`, ERC-8004 / ERC-8183 client surface) work identically.

For 0.3.x users that signed EIP-712 payloads via `export_private_key()` +
external `eth_account.Account.sign_typed_data`: the new
`wallet.sign_typed_data(...)` direct path is supported and preferred. The
default `SigningPolicy.strict_default()` accepts U-token EIP-3009 flows
out of the box, so most existing code paths don't need configuration
changes.

## [0.3.3] - earlier

Prior releases — see `git log v0.3.3..HEAD~4` for unrecorded history.
