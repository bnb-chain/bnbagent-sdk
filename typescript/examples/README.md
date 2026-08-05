# Examples

Runnable ports of `python/examples/*` demonstrating the TypeScript SDK's public API. There is no dedicated `typescript/examples/twak/` directory; the shared TWAK capability reference and examples live in [`docs/twak.md`](../../docs/twak.md) and [`python/examples/twak/`](../../python/examples/twak/). The TypeScript SDK itself exports `TWAKProvider`.

Run everything with the local workspace's `tsx` (already a devDependency):

```bash
pnpm -C typescript exec tsx examples/<path>.ts
```

## client/ - ERC-8183 lifecycle flows (live testnet)

Five canonical flows through the ERC-8183 escrow state machine. **These hit real bsc-testnet contracts** - they are not run in CI, only typechecked (`pnpm -C typescript run typecheck:examples`). Configure a `.env` next to `examples/client/` (or export the vars directly) per the table below, then run e.g. `pnpm -C typescript exec tsx examples/client/happy.ts`.

| Script | Flow |
| --- | --- |
| `happy.ts` | createJob → registerJob → setBudget → fund → submit → wait past dispute window → settle → `COMPLETED` |
| `zeroPrice.ts` | createJob → registerJob → `setBudget(0)` → `fund(0)` → submit → wait past dispute window → settle → `COMPLETED` (zero price: no escrow, nobody paid) |
| `disputeReject.ts` | fund → submit → client `dispute` → whitelisted voter `voteReject` (quorum met) → settle → `REJECTED` |
| `stalemateExpire.ts` | fund → submit → `dispute` with no quorum ever reached → wait past `expiredAt` → `claimRefund` → `EXPIRED` |
| `neverSubmit.ts` | fund → provider stays silent → wait past `expiredAt` → `claimRefund` → `EXPIRED` |
| `cancelOpen.ts` | createJob → (no funding) → client `cancelOpen` → `REJECTED`, no escrow ever moved |

Required env vars: `PRIVATE_KEY` (client), `PROVIDER_ADDRESS`. Optional: `PROVIDER_PRIVATE_KEY` (the script pauses and asks you to run the provider side manually if omitted), `VOTER_PRIVATE_KEY` (same, for `disputeReject.ts`), `NETWORK` (defaults to `bsc-testnet`).

`zeroPrice.ts` demonstrates **zero price** (ERC-8183 `budget == 0`). `setBudget(0n)` may be sent by either the client or the provider - the demo drives the buyer flow (client sets the budget) and still **requires** `PROVIDER_PRIVATE_KEY` for the provider-only `submit`. `fund(0n)` moves no tokens and the SDK skips the ERC-20 approve entirely, so the client needs no payment-token balance or allowance.

`_helpers.ts` centralizes the shared setup: `loadSettings()` reads `.env`, `makePrimaryClient()` builds the client-role `ERC8183Client` directly from an `EVMWalletProvider`, so these particular flow scripts require `PRIVATE_KEY` and do not read `WALLET_KIND`. Applications that construct `ERC8183Config.fromEnv()` can select the self-broadcasting `TWAKProvider` with `WALLET_KIND=twak` and a configured `@trustwallet/cli` >= 0.20.0. `expiryFor(client)` computes an `expiredAt` that clears the policy's `disputeWindow` plus a safety slack.

## voter/ - whitelisted-voter workflows (live testnet)

- `voteReject.ts <jobId>` - cast `voteReject` on a disputed job, with pre-flight checks (caller is a whitelisted voter, the job is actually disputed, the caller hasn't already voted).
- `watch.ts` - polling loop that detects disputed jobs, downloads and hash-verifies the deliverable manifest, prompts `[r]eject`/`[s]kip`, and auto-settles once `rejectVotes >= voteQuorum`. Adapted from the Python reference's raw `Disputed`/`VoteCast` event-log subscription: `PolicyClient` does not expose a public event-log read (that seam is `ContractBase`-internal), so this port polls `commerce.jobCounter()` + `policy.disputed()` / `policy.rejectVotes()` directly instead - re-polling `disputed()` for each still-undisputed job on every tick. Two differences from the event version: it only watches jobs created after startup (a dispute on a pre-existing job is not caught), and detection lags by up to one poll interval.

Requires `VOTER_PRIVATE_KEY` and `NETWORK` (defaults to `bsc-testnet`); not run in CI.

## x402/buyerDemo.ts - offline, CI-runnable

`pnpm -C typescript run example:x402`

Spins up an in-process `node:http` mock 402 server on loopback, builds an EIP-712 `TransferWithAuthorization` from its `accepts[]` challenge, signs it through `X402Signer` (policy-gated: recipient + amount guards on top of the wallet's `SigningPolicy`), retries the request with a base64 `X-PAYMENT` header, and confirms the protected payload comes back. No real network, no testnet U spent. Exits 0 and prints `ALL STEPS OK` on success.

## security/e2e.ts - offline, CI-runnable

`pnpm -C typescript run example:security`

Six sign/recover assertions against the real bsc-testnet U-token EIP-712 domain - no transaction is ever sent:

1. Default wallet signs a U-token `TransferWithAuthorization` and the signature round-trips through `recoverTypedDataAddress`.
2. Default wallet refuses to sign against an unknown `verifyingContract`.
3. Default wallet refuses the EIP-2612 `Permit` primary type (denylisted - unbounded allowance).
4. A `SigningPolicy` extended with a custom domain allowlist entry accepts that domain.
5. `X402Signer` refuses a payment whose value exceeds `maxValuePerCall`.
6. `X402Signer` refuses a payment whose `message.to` doesn't match the caller's `expectedTo`.

Exits 0 and prints `ALL 6 ASSERTIONS PASSED` on success. Run this after any change to the signing layer.

## agent-server/ - ERC-8183 HTTP serving reference (live testnet)

`pnpm -C typescript run example:agent-server`

The SDK's HTTP serving reference: a provider agent that picks up funded jobs (`fundedJobWatcher`), searches DuckDuckGo for blockchain news, and submits the deliverable on-chain. The whole serving layer is a ~100-line `node:http` router (`src/http.ts`) + a factory (`src/erc8183Server.ts`) - example code, not SDK API. Includes `scripts/register.ts` (ERC-8004) and `scripts/settle.ts` (operator settle). See `agent-server/README.md`.

## a2a-agent/ - A2A-fronted ERC-8183 provider (live testnet)

`pnpm -C typescript run example:a2a-server`

The recommended agent-facing surface: an A2A agent card + JSON-RPC `message/send` over `node:http`, backed by `NegotiationHandler` (signed quotes) and `ERC8183Client` (job reads). `scripts/register.ts` registers the card URL on ERC-8004; `scripts/buyer.ts` discovers → quotes → optionally funds a job (`pnpm run example:a2a-buyer`). See `a2a-agent/README.md`.

## Typechecking

`examples/tsconfig.json` extends the project's `tsconfig.json` and includes both `examples/**` and `../src/**`, so the examples typecheck against the real, current SDK API (not the published package) without needing a build:

```bash
pnpm -C typescript run typecheck:examples
```
