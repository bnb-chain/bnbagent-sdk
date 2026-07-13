# @bnbagent/sdk

TypeScript toolkit for building on-chain AI agents on BNB Chain: ERC-8004
identity, the ERC-8183 agentic-commerce protocol (escrow + evaluation +
optimistic dispute policy), x402 micropayments, wallets (local keystore +
pluggable executors), and the supporting core (paymaster, nonce management,
tx tuning).

This is a layered port of the reference [Python SDK](../python) — same
protocol semantics, same on-chain deployments, idiomatic TypeScript API. See
[`ARCHITECTURE.md`](../ARCHITECTURE.md) for the full protocol reference; this
README covers the TypeScript-specific surface and quickstart.

## Install

```bash
pnpm add @bnbagent/sdk
# or
npm install @bnbagent/sdk
```

Requires Node.js >= 20. The package ships both ESM (`import`) and CommonJS
(`require`) builds plus TypeScript types.

## Package layout

The public API is split into a small **Tier 1** barrel (the handful of names
most integrations need) and **Tier 2** subpath exports (each protocol
module's full surface):

```ts
// Tier 1 — the essentials
import { ERC8183Client, EVMWalletProvider, JobStatus } from "@bnbagent/sdk";

// Tier 2 — full module surface, imported by subpath
import { ERC8183JobOps, fundedJobWatcher, CommerceClient } from "@bnbagent/sdk/erc8183";
import { ERC8004Agent, AgentURIGenerator } from "@bnbagent/sdk/erc8004";
import { X402Signer, SessionBudgetTracker } from "@bnbagent/sdk/x402";
import { LocalStorageProvider, IPFSStorageProvider } from "@bnbagent/sdk/storage";
import { LocalExecutor, UnsupportedWalletOperation } from "@bnbagent/sdk/wallets";
import { SigningPolicy, check } from "@bnbagent/sdk/signing";
import { getAddress, BNB_CHAIN_ADDRESSES } from "@bnbagent/sdk/networks";
import { SlidingWindowLimiter, RateLimitExceeded } from "@bnbagent/sdk/utils";
```

Subpaths available: `./erc8004`, `./erc8183`, `./x402`, `./storage`,
`./wallets`, `./signing`, `./networks`, `./utils`.

## Quickstart

Both snippets assume a funded wallet on `bsc-testnet` (get test BNB from the
[BNB Chain faucet](https://www.bnbchain.org/en/testnet-faucet)) and an
`.env` populated per the [environment variables](#environment-variables)
table below. Call `loadEnv()` once at your entrypoint to load `.env`/`.env.local`
(the SDK never does this for you — see its docstring for the precedence rules).

### (a) Client-side job lifecycle

The client creates a job, binds the on-chain dispute policy, funds escrow,
and — once the provider submits — settles the job (permissionless: it just
pulls the policy's verdict and applies it on-chain).

```ts
import { loadEnv, EVMWalletProvider, ERC8183Client, JobStatus } from "@bnbagent/sdk";

loadEnv(); // opt-in .env / .env.local loading

const wallet = new EVMWalletProvider({
  password: process.env.WALLET_PASSWORD!,
  // First run only — the SDK encrypts it to ~/.bnbagent/wallets/<address>.json
  // and subsequent runs only need the password.
  privateKey: process.env.PRIVATE_KEY,
});

const client = await ERC8183Client.create({
  walletProvider: wallet,
  network: "bsc-testnet",
});

const decimals = await client.tokenDecimals();
const budget = 1n * 10n ** BigInt(decimals); // 1 token

// expiredAt must clear (disputeWindow + a safety buffer) or createJob()
// throws — a job whose deadline is too close can never be submitted.
const disputeWindow = await client.policy.disputeWindow();
const expiredAt =
  BigInt(Math.floor(Date.now() / 1000)) + disputeWindow + 600n; // +10 min slack

const { jobId } = await client.createJob({
  provider: process.env.PROVIDER_ADDRESS!,
  expiredAt,
  description: "ERC-8183 demo: summarize this week's BSC ecosystem news",
});
console.log(`[client] createJob jobId=${jobId}`);

// Bind the OptimisticPolicy so settle() has a verdict source.
await client.registerJob(jobId!);

// Escrow the budget — auto-approves the payment token if the allowance is short.
await client.fund(jobId!, budget);
console.log("[client] fund OK (Open -> Funded)");

// ... provider calls jobOps.submitResult() / client.submit() here (see below) ...

// After the dispute window elapses with no rejection, settle() finalizes
// the job as COMPLETED (or REJECTED if the policy recorded a reject vote).
await client.settle(jobId!);
const job = await client.getJob(jobId!);
console.log(`[client] settle -> ${JobStatus[job.status]}`);
```

### (b) Provider earn loop

A headless provider agent polls for jobs funded to its address and submits a
deliverable for each one. `ERC8183JobOps` handles verification (status,
assignment, expiry, budget floor) and deliverable upload; `fundedJobWatcher`
is a signer-free polling loop — it only *detects* funded jobs, so the
callback decides what to do (including delegating signing elsewhere).

```ts
import { EVMWalletProvider } from "@bnbagent/sdk";
import { ERC8183JobOps, fundedJobWatcher } from "@bnbagent/sdk/erc8183";
import { LocalStorageProvider } from "@bnbagent/sdk/storage";

const wallet = new EVMWalletProvider({ password: process.env.WALLET_PASSWORD! });

const jobOps = await ERC8183JobOps.create({
  walletProvider: wallet,
  network: "bsc-testnet",
  storageProvider: new LocalStorageProvider(".agent-data"),
  servicePrice: 1n * 10n ** 18n, // reject jobs budgeted below 1 token (18 decimals)
  agentUrl: process.env.ERC8183_AGENT_URL, // public base URL, used as a deliverable-URL fallback
});

await fundedJobWatcher(
  jobOps,
  async (job) => {
    const jobId = job.jobId as number;
    console.log(`[earn-loop] job ${jobId} funded, budget=${job.budget}`);

    const result = await jobOps.submitResult(
      jobId,
      `computed result for job ${jobId}`,
      { model: "my-model-v1" },
    );
    if (!result.success) {
      console.error(`[earn-loop] submit(${jobId}) failed: ${result.error}`);
      // Returning { retry: true } asks the watcher to re-validate and
      // re-fire this job on the next tick (only for transient failures).
      return { retry: result.retryable === true };
    }
    console.log(`[earn-loop] submitted ${jobId}, tx=${result.txHash}`);
  },
  { interval: 30 }, // seconds between polls
);
```

## Environment variables

None of these are read automatically — call `loadEnv()` (from `@bnbagent/sdk`)
at your entrypoint to load `.env.local` then `.env`, or set them however your
deployment normally injects environment variables. See [`.env.example`](../.env.example)
at the repo root for the authoritative, fully-commented reference.

| Variable | Used by | Notes |
| --- | --- | --- |
| `NETWORK` | `resolveNetwork` | Preset name: `bsc-testnet` (default) or `bsc-mainnet`. |
| `RPC_URL` / `RPC_URL_<NETWORK>` | `resolveNetwork` | Override the preset RPC endpoint; per-network var wins. |
| `WALLET_PASSWORD` | `EVMWalletProvider` | **Required.** Encrypts/decrypts the local Keystore V3 file. |
| `PRIVATE_KEY` | `EVMWalletProvider` | First run only — imported then encrypted to disk; remove afterward. |
| `WALLET_ADDRESS` | `EVMWalletProvider` | Disambiguates which keystore to load when several exist. |
| `ERC8183_COMMERCE_ADDRESS` | `getErc8183Config` | Override the AgenticCommerce kernel address (custom deployments only). |
| `ERC8183_ROUTER_ADDRESS` | `getErc8183Config` | Override the EvaluatorRouter address. |
| `ERC8183_POLICY_ADDRESS` | `getErc8183Config` | Override the OptimisticPolicy address. |
| `ERC8183_SERVICE_PRICE` | `ERC8183Config` / `ERC8183JobOps` | Minimum job budget (raw token units) a provider accepts. |
| `ERC8183_AGENT_URL` | `ERC8183JobOps` | Public base URL for the agent; fallback deliverable-URL host. |
| `ERC8183_FUNDED_POLL_INTERVAL` | agent-server examples | Poll interval (seconds) for the funded-job scan. |
| `ERC8183_MAX_RESPONSE_BYTES` / `ERC8183_MAX_METADATA_BYTES` | `ERC8183JobOps.submitResult` | Per-deliverable upload size caps. |
| `ERC8004_REGISTRY_ADDRESS` | `getErc8004Config` | Override the Identity Registry address. |
| `STORAGE_PROVIDER` | storage factory | `local` (default, zero-config) or `ipfs`. |
| `STORAGE_LOCAL_PATH` | `LocalStorageProvider` | Base directory when `STORAGE_PROVIDER=local`. |
| `STORAGE_API_KEY` | `IPFSStorageProvider` | Pinata-compatible JWT; required when `STORAGE_PROVIDER=ipfs`. |
| `STORAGE_API_URL` / `STORAGE_GATEWAY_URL` | `IPFSStorageProvider` | Optional custom pin endpoint / gateway. |
| `BNBAGENT_MIN_GAS_PRICE_WEI` | `minGasPriceWei` | Global gas-price floor override (wei), all chains. |
| `BNBAGENT_RECEIPT_TIMEOUT` | `getDefaultReceiptTimeout` | Default transaction-receipt wait, in seconds. |

## Parity with the Python SDK

This package mirrors [`python/bnbagent`](../python/bnbagent) module-for-module —
same contract addresses, same protocol invariants, same env var names. For
protocol-level detail (the ERC-8183 state machine, negotiation handshake,
x402 payment flow, signing-policy threat model, wallet capability model),
see [`ARCHITECTURE.md`](../ARCHITECTURE.md) and the Python package's own
`README.md` files under `python/bnbagent/*/README.md` — those docs are
protocol reference, not Python-specific, and apply equally here.
