# TWAK CLI × bnbagent-sdk — Capability Matrix (x402 / ERC-8004 / ERC-8183)

Maintained by the BNB Chain **bnbagent-sdk** team & **twak** team as the shared
tracking document for the twak integration. Based on davidkulman-tw's *TWAK CLI
Reference*, restructured as one table per protocol, one row per parameter.

**Integration surface:** bnbagent-sdk drives twak through the **CLI** (`twak …
--json`, stateless subprocess; password resolved by twak from keychain/env).
Issues below were discovered while integrating against v0.18.0. Requests must
land on the CLI surface; please keep the MCP surface (`twak serve`) in parity.

**Legend:**
- ✅ = implemented in twak **v0.18.0** (field-tested on `bsctestnet` with real transactions — see Verification log)
- 🙅 = ***missing — see the matching `REQ-n` (blocking) or `S-n` (suggestion)***
- **Networks** column (on each command's first row): per-network support, e.g. `bsc-mainnet ✅ · bsc-testnet 🙅`

## Open requests (blocking bnbagent-sdk)

Only items bnbagent-sdk actually consumes. Status lifecycle: `🙅 open` →
`🤝 acked (target version)` → `✅ shipped in vX`. Both teams edit only the
**Status** column as things progress.

| ID | Request | Priority | Blocks | Status |
|----|---------|----------|--------|--------|
| REQ-1 | `erc8183 submit --opt-params <hex>` (raw passthrough) | **P0** | the provider role entirely: bnbagent-sdk's `submit` carries `{"deliverable_url": …}` in optParams; without it a twak-submitted job cannot be evaluated (see note 1) | ✅ shipped in v0.19.0 |
| REQ-2 | paymaster broadcast for `erc8004`/`erc8183` writes (`--paymaster-url <rpc>` or `TWAK_PAYMASTER_URL`) | P1 | zero-BNB sponsored deployments | 🤝 — bsc-mainnet ✅ (v0.18.0, automatic) · bsc-testnet 🙅 |
| REQ-3 | tx-hash field naming stability: spec says `{ success, txHash }`, CLI returns `hash` / `approveHash` with no `success`. We parse `hash` today — **pick one as canonical, align the spec document, and don't change it without notice** | P2 | client parsing stability | 🤝 — canonical is `hash` (+ `approveHash`), ✅ in v0.19.0; spec document alignment pending |

## Suggestions (non-blocking — we have client-side workarounds, or they serve CLI-only users)

| ID | Suggestion | Why non-blocking for us |
|----|------------|--------------------------|
| S-1 | `--opt-params <hex>` on the other 5 `erc8183` write commands (`set-provider`, `set-budget`, `fund`, `complete`, `reject`) — ✅ (v0.19.0) | the contract takes it, but bnbagent-sdk currently always sends empty optParams on these; only `submit` (REQ-1) carries real content |
| S-2 | `erc8183 fund --expected-budget <atomic>` — ✅ (v0.19.0) | superseded — the SDK now passes `--expected-budget` and removed its client-side pre-check |
| S-3 | `erc8183 policy-info <jobId>` read command (`{ policy, disputeWindow, submittedAt, disputed, rejectVotes, voteQuorum, quorumSnapshot, settleableAt }`) — ✅ (v0.19.0) | bnbagent-sdk reads these views via its own RPC; valuable for CLI-only users (pre-flight `--expires-at`, settle timing, avoiding opaque reverts on repeat `dispute`/`vote-reject`) |
| S-4 | `wallet sign-message`: `0x`-prefix the signature, add a `digest` field — ✅ (v0.19.0) | CLI fixed; SDK keeps the ecrecover self-check as an integrity loop |
| S-5 | a config-home override (`TWAK_HOME` env var or `--home` flag) — `~/.twak` is hardcoded (`os.homedir()`), which breaks read-only code mounts and one-wallet-per-OS-user multi-agent hosts (re-verified unchanged on v0.19.0) | we relocate by overriding `HOME` on the subprocess (field-verified to work, but it is unpromised behavior we'd rather not depend on) |
| S-6 | `wallet import` (mnemonic / private key) — `wallet create` only mints a fresh mnemonic, so an agent's existing on-chain identity cannot move into twak | we accept the address change + ERC-8004 re-register on wallet-kind switch; the ERC-8004 `agentWallet` / set-wallet mechanism (in the spec, not in the CLI) would be the identity-continuity fix |
| S-8 | `wallet create` should honor `TWAK_WALLET_PASSWORD` when `--password` is absent (and support headless creation without forcing the OS keychain) — today creation hard-requires the password **on argv** (`requiredOption`), which leaks it to every process via `ps`; the env var only unlocks existing wallets (re-verified unchanged on v0.19.0) | we refuse to pass secrets on argv: programmatic creation raises a descriptive error pointing at manual creation or `materialize_twak_home()` |
| S-9 | `x402 quote` should exit 0 when the endpoint answers but no route is payable — today an empty `accepts` exits non-zero while the JSON says `success: true` (exit code and envelope disagree) (re-verified unchanged on v0.19.0) | we trust the explicit success envelope over the exit code in that direction |
| S-7 | `x402 request --json`: include payment receipt metadata (e.g. `{payment: {amount, asset, network, txHash}, body: …}`) — the success output today is the paid endpoint's response body verbatim; the amount/asset/payTo only appear in a human-readable stderr banner, and the x402 `PAYMENT-RESPONSE` header twak consumes already carries these fields | our session-budget accounting debits the *quoted* amount (or the `--max-payment` cap) instead of the actual settlement |
| S-10 | `wallet sign-message --chain` should accept the same chain keys as the rest of the CLI — today it is a *key-family* selector (help: "e.g., ethereum, solana") that accepts `bsc` but rejects `bsctestnet` with `Unsupported chain` (field-verified v0.19.0), unlike every `erc8183` command | EIP-191 is chain-agnostic and the address is identical on both BNB networks, so the SDK always passes `bsc` for signing (with an ecrecover self-check) |
| S-11 | `wallet sign-message --message` silently switches semantics on a `0x` prefix (hex-decoded as raw bytes vs signed as text) — a message that *happens* to look like hex (e.g. a negotiation hash) gets different bytes signed than the same string under text semantics, with no flag to disambiguate (field-verified v0.19.0) | the SDK always hex-encodes the text itself (`0x` + utf-8 hex), making the bytes mode explicit and byte-identical to text-semantics `personal_sign` |

**Open question for the twak team:** does the hosted/NaaS side have (or plan) server-side
spending policies (daily/monthly caps, recipient allowlists)? The CLI surface only enforces
the per-invocation `--max-payment`; today we layer day/month budgets client-side.

## Deployed contracts

| Standard | Contract | BSC mainnet | BSC testnet |
|----------|----------|-------------|-------------|
| ERC-8004 | Identity Registry (UUPS proxy) | `0x8004a169fb4a3325136eb29fa0ceb6d2e539a432` | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| ERC-8183 | `AgenticCommerce` (escrow) | `0xea4daa3100a767e86fded867729ae7446476eba6` | `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` |
| ERC-8183 | `EvaluatorRouter` | `0x51895229e12f9876011789b04f8698af06ccd6da` | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| ERC-8183 | `OptimisticPolicy` | `0x9c01845705b3078aa2e8cff7520a6376fd766de5` | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |

Payment token (`AgenticCommerce.paymentToken()`): **U** (United Stables,
`0xce24439f2d9c6a2289f741120fe202248b666666`, 18 dp) on mainnet; test token
`0xc70b8741b8b07a6d61e54fd4b20f22fa648e5565` on testnet. x402 has no contract of
ours — it pays via the asset's EIP-3009 / Permit2 methods.

---

## ERC-8004 — `twak erc8004 <subcommand>`

Override the registry address with `ERC8004_REGISTRY_ADDRESS` for custom deployments.

| twak command | Parameter | Solidity function | Networks |
|---|---|---|---|
| `register` | `--uri <url>` (`https://` / `ipfs://` / `data:` inline) ✅ | `register(string agentURI, MetadataEntry[] metadata) → uint256 agentId` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--metadata <key=value>` (repeatable, atomic with the mint) ✅ | ↑ | |
| `set-uri <agentId>` | `--uri <url>` ✅ | `setAgentURI(uint256 agentId, string newURI)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| `set-metadata <agentId>` | `--key <string>` ✅ | `setMetadata(uint256 agentId, string metadataKey, bytes metadataValue)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--value <hex\|string>` (`0x…` taken as hex, else UTF-8) ✅ | ↑ | |
| `get-metadata <agentId>` | `--key <string>` ✅ | `getMetadata(uint256 agentId, string metadataKey) → bytes` *(read-only)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `show <agentId>` | — | `ownerOf(uint256)` + `tokenURI(uint256)` + `getAgentWallet(uint256)` *(read-only)* | bsc-mainnet ✅ · bsc-testnet ✅ |

No custom-error selectors specific to the registry surfaced in our testing.

---

## ERC-8183 — `twak erc8183 <subcommand>`

A **client** (payer) creates and funds a job; a **provider** (worker) submits a
deliverable; disputes resolve through a **policy** routed by the `EvaluatorRouter`.

### Lifecycle & constraints

```
create-job → set-budget → register-job → fund → submit → settle
```

1. **`register-job` must run before `fund`** — else the Router-as-hook reverts `PolicyNotSet()`.
2. **`--evaluator` and `--hook` must both be the Router** — else `RouterNotEvaluator()` / `HookRequired()`.
3. **Expiry is checked twice**: `create-job` bounds `--expires-at` to `(now, now + 365d]`; `submit` additionally requires `now ≤ expiredAt − disputeWindow` (7d on mainnet) → `SubmissionTooLate()`. Use ~30 days.

| twak command | Parameter | Solidity function | Networks |
|---|---|---|---|
| `create-job` | `--provider <addr>` ✅ | `AgenticCommerce.createJob(address provider, address evaluator, uint256 expiredAt, string description, address hook) → uint256 jobId` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--evaluator <addr>` ✅ | ↑ | |
| | `--expires-at <unix>` ✅ | ↑ | |
| | `--description <text>` ✅ | ↑ | |
| | `--hook <addr>` ✅ | ↑ | |
| `set-provider <jobId>` | `--provider <addr>` ✅ | `AgenticCommerce.setProvider(uint256 jobId, address provider, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → S-1 | ↑ | |
| `set-budget <jobId>` | `--amount <atomic>` ✅ | `AgenticCommerce.setBudget(uint256 jobId, uint256 amount, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → S-1 | ↑ | |
| `fund <jobId>` | `--expected-budget <atomic>` ✅ (v0.19.0) → S-2 | **Two txs:** ERC-20 `approve(commerce, budget)`, then `AgenticCommerce.fund(uint256 jobId, uint256 expectedBudget, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → S-1 | ↑ | |
| `submit <jobId>` | `--deliverable <bytes32>` ✅ | `AgenticCommerce.submit(uint256 jobId, bytes32 deliverable, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → REQ-1 (note 1) | ↑ | |
| `complete <jobId>` | `--reason <bytes32>` (defaults to zero) ✅ | `AgenticCommerce.complete(uint256 jobId, bytes32 reason, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → S-1 | ↑ | |
| `reject <jobId>` | `--reason <bytes32>` (defaults to zero) ✅ | `AgenticCommerce.reject(uint256 jobId, bytes32 reason, bytes optParams)` | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--opt-params <hex>` ✅ (v0.19.0) → S-1 | ↑ | |
| `claim-refund <jobId>` | — | `AgenticCommerce.claimRefund(uint256 jobId)` *(permissionless after expiry)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `status <jobId>` | — | `AgenticCommerce.getJob(uint256 jobId) → Job` + `paymentToken()` *(read-only)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `register-job <jobId>` | `--policy <addr>` (defaults to the deployed `OptimisticPolicy`) ✅ | `EvaluatorRouter.registerJob(uint256 jobId, address policy)` *(client only)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `settle <jobId>` | `--evidence <hex>` (defaults to `0x`) ✅ | `EvaluatorRouter.settle(uint256 jobId, bytes evidence)` *(permissionless — in practice the provider calls it after the dispute window to release payment; the client after a quorum-reject to refund)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `mark-expired <jobId>` | — | `EvaluatorRouter.markExpired(uint256 jobId)` *(permissionless — Router bookkeeping after `claim-refund`, which bypasses hooks)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `dispute <jobId>` | — | `OptimisticPolicy.dispute(uint256 jobId)` *(client, within the dispute window)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `vote-reject <jobId>` | — | `OptimisticPolicy.voteReject(uint256 jobId)` *(whitelisted voter; quorum = 3)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| `policy-info <jobId>` ✅ (v0.19.0) → S-3 | — *(read-only)* | `Router.jobPolicy(jobId)` + `Policy.disputeWindow()` + `submittedAt(jobId)` + `disputed(jobId)` + `rejectVotes(jobId)` + `disputeQuorumSnapshot(jobId)` | bsc-mainnet ✅ (v0.19.0) · bsc-testnet ✅ (v0.19.0) |

**Note 1 — REQ-1, the `submit` optParams gap.** Every mutating kernel function
carries a trailing `bytes optParams`; the CLI always sends `0x`. On `submit`
this is **protocol-breaking**: the Router's `afterAction` forwards optParams to
`policy.onSubmitted`, and `OptimisticPolicy` re-emits it in
`JobInitialised(jobId, deliverable, submittedAt, optParams)` — where off-chain
evaluators/voters read the deliverable manifest URL (e.g.
`{"deliverable_url":"ipfs://…"}` in the bnbagent-sdk optimistic flow). A
twak-submitted job emits **empty** optParams and cannot be evaluated. On-chain
evidence: see Verification log. **✅ fixed in v0.19.0** — counter-evidence on
job 150 in the Verification log (the same event now carries the full
`deliverable_url` JSON).

### Custom-error selectors

Raw 4-byte selectors (`keccak256("<ErrorName()>")[:4]`), each defined in the
contract that throws it:

| Selector | Error | Defined in / thrown by | Surfaces on | Cause |
|----------|-------|------------------------|-------------|-------|
| `0x55c45de1` | `HookRequired()` | `AgenticCommerce` | `create-job` | zero `--hook` |
| `0xf7a0748c` | `ExpiryTooShort()` | `AgenticCommerce` | `create-job` | `--expires-at` not far enough in the future |
| `0xb40b2a0e` | `ExpiryTooLong()` | `AgenticCommerce` | `create-job` | `--expires-at` > now + 365d |
| `0x99b0fc87` | `BudgetMismatch()` | `AgenticCommerce` | `fund` | on-chain budget changed after `set-budget` |
| `0xec43ea50` | `RouterNotEvaluator()` | `EvaluatorRouter` | `register-job` | `--evaluator` ≠ Router |
| `0x32d53d69` | `PolicyNotSet()` | `EvaluatorRouter` (as kernel hook) | `fund` | `fund` before `register-job` |
| `0x15e5dd74` | `SubmissionTooLate()` | `OptimisticPolicy` (via `onSubmitted`) | `submit` | later than `expiredAt − disputeWindow` |

No change requested here — the selectors are documented and **bnbagent-sdk maps
them client-side**. One FYI from field testing: our in-window `settle` revert came
back as `{"error":"execution reverted","errorCode":"UNKNOWN_ERROR"}` with no
selector in the payload. Client-side mapping only works as long as the raw
selector / revert data passes through the `error` field — please keep it passing
through. **✅ fixed in v0.19.0** — the selector now always passes through on both
networks (`{"error":"execution reverted: 0x…","errorCode":"TX_FAILED"}`).

### `status` ordinals

The CLI returns status *names*; event logs (`JobFinalised`) and raw `getJob`
calls return the `uint8` ordinal: `0 Open · 1 Funded · 2 Submitted ·
3 Completed · 4 Rejected · 5 Expired`. (Field order of the raw `getJob` tuple is
the post-audit layout — `submittedAt` at index 9, `deliverable` at index 10.)

---

## x402 — `twak x402 <subcommand>`

Full HTTP client: discovers the 402 challenge, signs the payment authorization
internally (no separate signer primitive), retries with the payment header.

| twak command | Parameter | On-chain method | Networks |
|---|---|---|---|
| `quote <url>` | `--method <http-method>` ✅ | — *(read-only HTTP discovery; no wallet, no chain)* | bsc-mainnet ✅ · bsc-testnet ✅ |
| | `--body <json>` ✅ | ↑ | |
| `request <url>` | `--method <http-method>` ✅ | EIP-3009 `transferWithAuthorization` (gasless) or Permit2 `permit2-exact` on the payment asset, per the negotiated route | bsc-mainnet ✅ · bsc-testnet 🙅 *(testnet routes rejected as "no supported route")* |
| | `--body <json>` ✅ | ↑ | |
| | `--max-payment <atomic>` ✅ | ↑ | |
| | `--prefer-network <key\|CAIP>` ✅ | ↑ | |
| | `--prefer-method <eip3009\|permit2-exact>` ✅ | ↑ | |
| | `--prefer-asset <addr\|name>` ✅ | ↑ | |
| | `--yes` (auto-approve up to `--max-payment`) ✅ | ↑ | |
| | `--auto-approve` (Permit2 one-time `approve(Permit2, MAX)`) ✅ | ↑ | |
| `info` | — | — | — |

---

## Paymaster (gas sponsorship) → REQ-2

bnbagent-sdk broadcasts through the **BNB Chain MegaFuel paymaster** (NodeReal) by
default on both networks: the signed raw tx is pre-checked with `isSponsorable`
and then sent via the paymaster RPC, so sponsored agents need **zero BNB** for
gas on whitelisted contracts.

| Capability | bnbagent-sdk | twak v0.18.0 |
|---|---|---|
| Sponsored broadcast via paymaster RPC | ✅ default on `bsc` (`https://bsc-megafuel.nodereal.io`) and `bsctestnet` (`https://bsc-megafuel-testnet.nodereal.io`) | bsc-mainnet ✅ (v0.18.0, automatic) · bsc-testnet 🙅 → REQ-2 |
| `isSponsorable` pre-check | ✅ | bsc-mainnet ✅ (v0.18.0) · bsc-testnet 🙅 → REQ-2 |

**REQ-2 detail:** a way to route broadcasts through a sponsor RPC for
`erc8004` / `erc8183` write commands — e.g. a `--paymaster-url <rpc>` flag or a
`TWAK_PAYMASTER_URL` env var (send the signed raw tx via that endpoint when set,
optionally pre-checking `isSponsorable`). Without it, twak-backed agents pay gas
from the wallet's BNB (verified: we had to pre-fund the twak wallet before any
write).

---

## Verification log

| twak version | Date | What was verified |
|---|---|---|
| v0.18.0 | 2026-06-10 | 14 commands field-tested on `bsctestnet`: erc8004 `register` (agents 1350, 1351 — incl. atomic `--metadata`), `set-uri`, `set-metadata`, `get-metadata`, `show`; erc8183 `create-job` (jobs 137, 138), `set-provider`, `set-budget`, `register-job`, `fund` (2-tx approve+deposit confirmed), `submit`, `reject`, `dispute`, `status`. optParams gap evidenced on job 137: tx `0xfa057a11cb3a526e9d2351a7bf5a7aa8dd324c9cb80b3343cc28da88980681f4`, decoded calldata `submit(jobId=137, deliverable=0xabab…, optParams='')`, policy event `JobInitialised(…, optParams='')`. x402 `quote` verified against a live 402 endpoint; `request` not exercised end-to-end (mainnet-only routes). Not exercised: `complete` (evaluator-only), `claim-refund` / `mark-expired` (need expiry), `vote-reject` (needs whitelisted voter), `settle` happy path (only the in-window revert was exercised). |
| v0.19.0 | 2026-06-12 | New flag surface probed: `--opt-params` accepted on all six erc8183 writes (help text verified on `set-provider`/`set-budget`/`fund`/`complete`/`reject`; `submit` exercised live), `fund --expected-budget`, `policy-info`. REQ-1 proven on-chain with a fully twak-driven lifecycle on `bsctestnet` job 150 (twak as client AND provider): `create-job` `0x642a0d0d…40fa`, `register-job` `0xfe44a46b…93ad`, `set-budget` `0x29f251cd…c3cd`, `fund --expected-budget` `0x88e158a5…cb95` (approveHash `0x88fd802e…9927`), `submit --opt-params` `0xf0790150…b6e7` → status SUBMITTED, on-chain `deliverable` == manifest hash, and the policy's `JobInitialised` event carries 70 bytes optParams = `{"deliverable_url":"https://example.invalid/req1-proof-manifest.json"}` (contrast: v0.18.0 job 137 emitted empty optParams). `policy-info` verified live (returns policy/disputeWindow/submittedAt/disputed/rejectVotes/voteQuorum/quorumSnapshot/settleableAt; note: this testnet's `voteQuorum` is actually `2`, not the 3 documented above). `sign-message` now returns a `0x`-prefixed signature + `digest` field; the digest byte-matches the SDK's own EIP-191 computation (cross-checked). Error-selector passthrough confirmed fixed: reverts surface as `{"error":"execution reverted: 0x<selector>","errorCode":"TX_FAILED"}` on both networks. Re-verified unchanged: S-5 (no `TWAK_HOME` — HOME override still required and still works), S-8 (`wallet create` still hard-requires `--password` on argv), S-9 (`x402 quote` still exits rc=1 with `success:true` on empty accepts). `wallet status` output shape unchanged (`agentWallet` field). |
| v0.19.0 | 2026-06-12 (2) | `wallet sign-message` field round 2, exercised live through the A2A example's quote signing: (a) its `--chain` is a key-family selector — `bsc` accepted, `bsctestnet` rejected with `Unsupported chain` (→ S-10); (b) a `0x`-prefixed `--message` is silently hex-decoded and signed as raw bytes, so a negotiation hash signed via twak diverged from the SDK's text semantics until the SDK switched to always sending `0x` + utf-8 hex (→ S-11; recovery self-check caught the divergence as designed). After both SDK-side fixes: a twak-signed quote's `ecrecover(negotiation_hash, provider_sig)` matches the wallet address under the same text semantics as `EVMWalletProvider` — wallet kinds are signature-compatible. |

*Contact: BNB Chain bnbagent-sdk team. All findings reproducible on `bsctestnet`;
test scripts and tx hashes available on request.*

---

**twak team (2026-06-11):** the remaining open items — x402 `request` on
`bsc-testnet` and the REQ-2 `bsc-testnet` sponsored broadcast — are being worked on.
