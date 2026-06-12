# Architecture

This document describes the high-level architecture of the BNBAgent SDK.
If you want to familiarize yourself with the codebase, this is a good place
to start.

## Bird's Eye View

BNBAgent SDK is the **protocol meta-capability layer** for building on-chain
AI agents on BNB Chain. It provides wallet management, signing policy,
off-chain storage abstraction, and built-in support for the following
protocols:

- **ERC-8004** — On-chain identity registry for AI agents (register, discover,
  resolve endpoints).
- **ERC-8183 Protocol** — a three-layer agentic commerce stack:
  - **AgenticCommerce** (ERC-8183 kernel) — job lifecycle + escrow
    (create / setBudget / fund / submit / complete / reject / claimRefund).
  - **EvaluatorRouter** — routing layer that doubles as `job.evaluator`
    and `job.hook`. Binds `jobId → policy` on `registerJob`, pulls verdicts
    on the permissionless `settle`.
  - **OptimisticPolicy** — reference UMA-style policy. Silence past the
    dispute window is implicit approval; a client can raise a dispute
    that the whitelisted voters resolve by `voteReject` reaching quorum.
- **x402** — HTTP 402 micropayment signing (EIP-3009) with budget tracking
  and delegated payers.

Wallet signing and off-chain storage are abstracted behind provider
interfaces, making the SDK backend-agnostic. Each protocol package is
independently importable — there is no framework, registry, or required
composition root.

### Layering: where the SDK ends

The SDK is the bottom of a three-layer stack:

| Layer | Owns | Examples |
|---|---|---|
| **SDK** (`bnbagent`) | Protocol-defined, security-critical, or universally needed capability | protocol clients, wallet/capability model, signing policy, headless provider primitives, amount conversion, registration constructors |
| **Product/workflow** (e.g. `bnbagent-studio-core`) | Config interpretation, opinionated multi-step workflows, product policy | studio.toml factories, budget gates, buy/submit/settle workflows |
| **Distribution / application** | How an agent faces the world | CLI, recipes, **serving surfaces** |

The boundary criterion, in one sentence: **protocol-defined / security-critical
/ needed by every consumer → SDK; interprets a product's config or carries a
product opinion → the layer above.** Capabilities migrate downward item by
item when they prove universal (e.g. `to_raw`/`from_raw` were lifted into
`bnbagent.utils` from a downstream consumer) — never as a wholesale merge.

**Serving surfaces are deliberately NOT in the SDK.** How an agent faces the
world — A2A, MCP, plain HTTP — is an application choice. The recommended
direction is **A2A first, MCP second**; the SDK's role is limited to:

- the **registration side**: `AgentEndpoint.a2a()` / `AgentEndpoint.mcp()`
  encode each protocol's ERC-8004 registration convention;
- the **headless primitives** every serving form shares: `ERC8183JobOps`,
  `funded_job_watcher`, `NegotiationHandler`, `SlidingWindowLimiter`.

Reference serving implementations live in `examples/` (`a2a-agent/` for A2A,
`agent-server/` for HTTP) as copy-and-own code, not SDK API.

```
   examples/a2a-agent     examples/agent-server      (serving forms — application layer)
        │ A2A                  │ HTTP
        └──────────┬───────────┘
                   │ uses
        ┌──────────▼───────────────────────────────┐
        │  bnbagent (protocol meta-capabilities)    │
        │                                           │
        │  erc8004    erc8183     x402    signing   │
        │  (identity) (commerce)  (pay)   (policy)  │
        │       │         │        │        │       │
        │  ┌────▼─────────▼────────▼────────▼────┐  │
        │  │ wallets (providers, capabilities,   │  │
        │  │ intents)   storage    core   utils  │  │
        │  └─────────────────────────────────────┘  │
        └───────────────────────────────────────────┘
```

Arrows point **downward** — upper layers depend on lower layers, never the
reverse.

## Code Map

### `bnbagent/` — Main Package

| File | Purpose |
|------|---------|
| `__init__.py` | Tier 1 public API (re-exports from subpackages) |
| `config.py` | `NetworkConfig`, `NETWORKS` registry, `resolve_network()` with per-network RPC override |
| `constants.py` | Global constants (`SCAN_API_URL`) |
| `exceptions.py` | `BNBAgentError` hierarchy |

### `bnbagent/core/` — Internal Infrastructure

Not part of the public API. Provides shared plumbing for protocol packages.

| File | Purpose |
|------|---------|
| `contract_mixin.py` | `ContractClientMixin` — shared base for `CommerceClient`, `RouterClient`, `PolicyClient`, `MinimalERC20Client` (tx signing, nonce management, retry with backoff) |
| `nonce_manager.py` | `NonceManager` — per-account thread-safe nonce tracking with chain re-sync |
| `multicall.py` | `multicall_read()` — Multicall3 batch view helper |
| `paymaster.py` | `Paymaster` — ERC-4337 gas sponsorship client |
| `abi_loader.py` | ABI file loading from bundled JSON |
| `config.py` | `get_env` + `AgentConfig` dataclass base (network + wallet plumbing) |
| `env.py` | `load_env()` — opt-in `.env.local`-first loading |

### `bnbagent/erc8004/` — ERC-8004 Identity Registry

| File | Purpose |
|------|---------|
| `agent.py` | `ERC8004Agent` — high-level SDK: `register_agent()`, `get_agent_info()`, `get_all_agents()` |
| `contract.py` | `ContractInterface` — low-level web3 contract calls |
| `models.py` | `AgentEndpoint` dataclass + protocol-aware constructors `AgentEndpoint.a2a()` / `AgentEndpoint.mcp()` (registration conventions only — no protocol runtime) |
| `agent_uri.py` | EIP-8004 registration file ↔ base64 data URI |
| `constants.py` | `get_erc8004_config()` — per-network contract addresses |

### `bnbagent/erc8183/` — ERC-8183 Protocol

High-level facade over three contracts. Most callers only touch `ERC8183Client`.

| File | Purpose |
|------|---------|
| `client.py` | `ERC8183Client` — facade over Commerce / Router / Policy; floor-based `fund` approval; cached `payment_token` / `token_decimals` / `token_symbol`; high-level wrappers for `create_job`, `register_job`, `set_budget`, `fund`, `submit`, `settle`, `dispute`, `vote_reject`, `claim_refund` |
| `commerce.py` | `CommerceClient` — low-level wrapper for `AgenticCommerceUpgradeable` |
| `router.py` | `RouterClient` — low-level wrapper for `EvaluatorRouterUpgradeable` |
| `policy.py` | `PolicyClient` — low-level wrapper for `OptimisticPolicy` (dispute / voteReject / check / voter admin) |
| `../erc20/client.py` | `MinimalERC20Client` — used by `ERC8183Client` for the payment token (decimals/symbol/balanceOf/allowance/approve) |
| `job_ops.py` | **Headless provider primitives**: `ERC8183JobOps` — async wrapper over `ERC8183Client` (verify, submit with off-chain upload); `funded_job_watcher` — signer-free FUNDED-job detection loop. Every serving form (A2A / MCP / HTTP / keyless poller) builds on these |
| `types.py` | `JobStatus`, `Verdict`, `REASON_APPROVED`, `REASON_REJECTED`, `Job` dataclass |
| `config.py` | `ERC8183Config` — provider config (wallet + storage + service price + `ERC8183_*` env overrides) |
| `negotiation.py` | `NegotiationHandler` (wallet-signed quotes, chain-bound anti-replay), structured description schema, `build_job_description()`, quote expiry |
| `schema.py` | `DeliverableManifest`, `JobDescription`, `SCHEMA_VERSION` — on-chain description and off-chain deliverable JSON |
| `constants.py` | `get_erc8183_config()` — per-network defaults |

### `bnbagent/wallets/` — Wallet Providers

| File | Purpose |
|------|---------|
| `wallet_provider.py` | `WalletProvider` ABC — capability model (`capabilities()` / `supports()`), `make_executor()`, `make_x402_payer()`, sign-method defaults that raise `UnsupportedWalletOperation` |
| `capabilities.py` | Open capability-string registry (`sign.*`, `intents.*`, `x402.pay`, ...) |
| `intents.py` | `Intent` (dual-representation), `IntentExecutor`, `ExecutionContext` — the execution seam |
| `evm_wallet_provider.py` | `EVMWalletProvider` — Keystore V3 encryption (scrypt + AES-128-CTR) |
| `twak_provider.py` | `TWAKProvider` — Trust Wallet Agent Kit CLI backend (self-broadcasting, delegated signing) |
| `twak_custody.py` | `materialize_twak_home()` — deploy-time custody materialization |
| `local_executor.py` | `LocalExecutor` — build/sign/broadcast path for pure-signing wallets |
| `mpc_wallet_provider.py` | `MPCWalletProvider` — stub for future MPC signer support |

### `bnbagent/x402/` — x402 Payment Layer

| File | Purpose |
|------|---------|
| `signer.py` | `X402Signer` — EIP-3009 payment signing, typed against `TypedDataSigner` |
| `payer.py` | `X402Payer` protocol + quote/result types (delegated "handle payment" seam) |
| `twak.py` | `TwakX402Payer` — delegated payer over the twak CLI (five-point precheck) |
| `budget.py` | `SessionBudgetTracker` — per-session spend ceiling |

### `bnbagent/signing/` — Signing Policy

| File | Purpose |
|------|---------|
| `policy.py` | `SigningPolicy` — first-line defense against blind-sign attacks on EIP-712 payloads |
| `checks.py` | Named type sets (EIP-3009, PERMIT, PERMIT2) and structural checks |

### `bnbagent/storage/` — Storage Providers

| File | Purpose |
|------|---------|
| `storage_provider.py` | `StorageProvider` ABC — async `upload()`, `download()`, `exists()`, `compute_hash()` |
| `local_storage_provider.py` | `LocalStorageProvider` — filesystem (`file://` URLs); owns its own `from_env()` |
| `ipfs_storage_provider.py` | `IPFSStorageProvider` — IPFS pinning via HTTP API (Pinata-compatible); owns its own `from_env()` |
| `sync_utils.py` | `upload_sync()` — synchronous bridge |

### `bnbagent/utils/` — Generic Utilities

Dependency-free helpers shared by the SDK and its consumers.

| File | Purpose |
|------|---------|
| `amounts.py` | `to_raw()` / `from_raw()` — Decimal-exact human ↔ raw token unit conversion |
| `rate_limit.py` | `SlidingWindowLimiter` (+ `RateLimitExceeded`) — transport-agnostic per-key throttle; serving layers map the exception to their protocol's rejection (HTTP 429, ...) |

### `bnbagent/networks/` — Deployment Registry

Canonical chain IDs, contract addresses and payment-token EIP-712 metadata
(`BNB_CHAIN_ADDRESSES`). Single source of truth for downstream layers.

### `examples/`

| Directory | Role | What it demonstrates |
|-----------|------|----------------------|
| `client/` | Client | 5 stand-alone scripts — happy / dispute-reject / stalemate-expire / never-submit / cancel-open |
| `voter/` | Voter | `voteReject` script + `Disputed` event watcher |
| `a2a-agent/` | Provider (A2A) | **Recommended serving direction**: agent card + `message/send` fronting SDK negotiation; ERC-8004 discovery round-trip |
| `agent-server/` | Provider (HTTP) | Self-contained HTTP serving reference (`src/erc8183_server.py`) with funded-job poll loop |
| `twak/` | Wallet | TWAK custody quickstart, delegated x402 payer, bsctestnet smoke |
| `x402/` | Buyer | x402 buyer flow against a mock 402 server |
| `security/` | Security | Defense-in-depth signing validation |

### `tests/` — Test Suite

`pytest` + `pytest-mock` + `pytest-asyncio`. Tests mock web3 and external
services; no live chain calls in CI. The HTTP serving tests live with the
HTTP example (`examples/agent-server/tests/`), not in the SDK suite.

## Public API

**Tier 1** — importable directly from `bnbagent`:

```python
from bnbagent import (
    NetworkConfig, BNBAgentError,
    ERC8004Agent, AgentEndpoint,
    WalletProvider, EVMWalletProvider,
    ERC8183Client, JobStatus, Verdict,
    SigningPolicy, PolicyViolation,
    X402Signer, load_env,
)
```

**Tier 2** — import from subpackages:

```python
from bnbagent.erc8183 import (
    ERC8183Client, CommerceClient, RouterClient, PolicyClient,
    JobStatus, Verdict, Job,
    ERC8183JobOps, funded_job_watcher, NegotiationHandler,
)
from bnbagent.erc8183.config import ERC8183Config
from bnbagent.utils import to_raw, from_raw, SlidingWindowLimiter, RateLimitExceeded
from bnbagent.storage import LocalStorageProvider, IPFSStorageProvider
from bnbagent.networks import get_address, BNB_CHAIN_ADDRESSES
from bnbagent.x402 import SessionBudgetTracker
```

## Configuration

```
NetworkConfig (NETWORKS dict in config.py)
  ├── bsc-testnet  (chain_id=97)  — active, ERC-8183 + ERC-8004 deployed
  └── bsc-mainnet  (chain_id=56)  — active, ERC-8183 + ERC-8004 deployed

resolve_network(name) + env var overrides
  ↓ (clients assert w3.eth.chain_id == nc.chain_id at init — wrong RPC → ValueError)
per-config wallet plumbing (AgentConfig subclasses, e.g. ERC8183Config)
```

**Environment variable overrides** (module-scoped):

| Variable | Scope | Overrides |
|----------|-------|-----------|
| `RPC_URL_BSC_TESTNET` / `RPC_URL_BSC_MAINNET` | `resolve_network` | `rpc_url` for that network only (wins over `RPC_URL`) |
| `RPC_URL` | global (`resolve_network`) | `rpc_url` (all networks) |
| `ERC8183_COMMERCE_ADDRESS` | `ERC8183Config.effective_network` | `commerce_contract` |
| `ERC8183_ROUTER_ADDRESS` | `ERC8183Config.effective_network` | `router_contract` |
| `ERC8183_POLICY_ADDRESS` | `ERC8183Config.effective_network` | `policy_contract` |
| `ERC8004_REGISTRY_ADDRESS` | `get_erc8004_config` | `registry_contract` |

The per-network RPC pins exist because a single process may touch several
networks at once; a single shared `RPC_URL` would silently apply to the wrong
chain. Contract-address overrides are applied by each module's own config
loader — keeps each module's env surface self-contained and obvious from the
prefix.

When `network=NetworkConfig(...)` is passed directly (instead of a preset
name), env overrides are **not** applied — the object is used as-is.

Payment token address is NOT configurable — it is immutable on the Commerce
kernel and fetched at runtime via `ERC8183Client.payment_token`.

Configs support the convenience pattern: pass `private_key` +
`wallet_password` and the config auto-wraps them into an `EVMWalletProvider`,
then **clears both the plaintext key and password** from the config object
(the provider keeps its own password copy).

## Invariants

These properties hold across the codebase and should be preserved:

- **No plaintext secrets in config after construction.** `__post_init__()` wraps
  `private_key` into a `WalletProvider` and zeros both the `private_key` and
  `wallet_password` string fields (the provider retains its own password copy).
- **Passwords never travel on argv.** (INV-1; see the twak custody design.)
- **The SDK ships no serving runtime.** No HTTP/A2A/MCP server lives under
  `bnbagent/`; `fastapi` is not an SDK dependency. Serving forms are examples
  or downstream applications.
- **Capability declaration cannot drift from behavior.** `sign.*` capabilities
  are auto-derived from method overrides; never override a `sign_*` method to
  raise.
- **`ContractClientMixin` prefers `wallet_provider` over raw `private_key`.**
- **Storage providers are async.** Synchronous callers use `upload_sync()`
  to avoid blocking the event loop.
- **Nonce management is per-account singleton.** `NonceManager.for_account()`
  ensures one manager per address to prevent collisions in concurrent code.
- **Retry with backoff on rate limits (429) and nonce conflicts.** Up to 5
  retries with exponential backoff. Nonce errors trigger chain re-sync.
- **Payment token is dynamically fetched.** It is never part of `NetworkConfig`
  or `ERC8183Config` because it is an immutable property of the deployed
  Commerce kernel.

## Data Flows

### Agent Registration (ERC-8004)

```
AgentEndpoint.a2a("https://agent.example")        # registration constructor
  → ERC8004Agent.generate_agent_uri(name, description, endpoints=[...])
  → ERC8004Agent.register_agent(agent_uri)
    → ContractClientMixin._send_tx()
      → WalletProvider.sign_transaction()
      → web3.eth.send_raw_transaction()
  → On-chain: IdentityRegistry stores agent metadata
```

### ERC-8183 Job Lifecycle

Happy path (silence approve):

```
1. Discover provider             →  ERC8004Agent.get_agent_info() → A2A/MCP endpoint
2. Negotiate price (off-chain)   →  NegotiationHandler (over the agent's serving surface)
3. createJob(provider, router)   →  ERC8183Client.create_job(...)       Open
4. registerJob(jobId, policy)    →  ERC8183Client.register_job(...)     Open
5. setBudget(jobId, amount)      →  ERC8183Client.set_budget(...)       Open
6. approve(commerce, amount) +
   fund(jobId, amount)           →  ERC8183Client.fund(...)             Funded
                                    (floor-based auto-approval)
7. Provider submit(deliverable)  →  ERC8183Client.submit(...)           Submitted
8. Wait dispute window           →  time passes
9. router.settle(jobId, "")      →  ERC8183Client.settle(...)           Completed
   (permissionless; any party can call from their own wallet)
```

Dispute branches:

- Client calls `ERC8183Client.dispute(jobId)` during the window → voters cast
  `ERC8183Client.vote_reject(jobId)` → once `rejectVotes >= quorum`, `settle`
  moves the job to **REJECTED** and refunds the client.
- No quorum ever reached → `settle` stays blocked; once `expiredAt` passes,
  anyone calls `ERC8183Client.claim_refund(jobId)` → **EXPIRED**.

`claimRefund` is non-pausable and non-hookable by design — the universal
escape hatch at expiry.

### Provider Earn Loop (headless — no serving surface required)

```python
from bnbagent.erc8183 import ERC8183JobOps, funded_job_watcher

ops = ERC8183JobOps(wallet_provider=wallet, network="bsc-testnet",
                    storage_provider=storage)

async def on_funded(job):
    deliverable = do_work(job)                       # your business logic
    await ops.submit_result(job["jobId"], deliverable)

await funded_job_watcher(ops, on_funded, interval=30)
```

The watcher is signer-free detection (it never submits or settles itself);
`ERC8183JobOps` constructed with only a `provider_address` (no wallet) is the
SDK-level keyless read path — any signing call raises. The SDK does NOT
auto-settle: settle is permissionless and runs as a separate operator script
(see `examples/agent-server/scripts/settle.py`).

## Exception Hierarchy

```
BNBAgentError
├── ConfigurationError   — missing/invalid config
├── ContractError        — transaction reverts, gas failures
├── NetworkError         — RPC errors, rate limits, timeouts
├── ABILoadError         — ABI file not found or invalid JSON
├── StorageError         — upload/download failures
├── JobError             — invalid job state, unauthorized access
└── NegotiationError     — price validation, unsupported terms
```

(`bnbagent.utils.RateLimitExceeded` and the wallet/x402 error families are
deliberately outside this hierarchy — they are utility/capability errors, not
protocol-flow errors.)

## Extension Points

- **Custom WalletProvider** — subclass `WalletProvider` to support HSMs,
  multisig, or MPC signers; declare capabilities by overriding the matching
  methods.
- **Custom StorageProvider** — implement the `StorageProvider` ABC for
  alternative backends (S3, Arweave, etc.).
- **Custom serving surface** — compose `ERC8183JobOps` + `funded_job_watcher`
  + `NegotiationHandler` behind any transport; register it with
  `AgentEndpoint.a2a()` / `.mcp()`. The examples are the templates.

## Dependencies

| Category | Packages |
|----------|----------|
| Core | `web3 ≥ 6.15`, `eth-account ≥ 0.10`, `python-dotenv ≥ 1.0`, `requests ≥ 2.31` |
| IPFS (optional) | `httpx ≥ 0.25` |
| Dev | `pytest`, `pytest-mock`, `pytest-asyncio`, `httpx`, `ruff` |

(`fastapi`/`uvicorn` are dependencies of the HTTP/A2A *examples*, not the SDK.)
