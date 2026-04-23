# Architecture

This document describes the high-level architecture of the BNBAgent SDK.
If you want to familiarize yourself with the codebase, this is a good place
to start.

## Bird's Eye View

BNBAgent SDK is a Python toolkit for building **on-chain AI agents** on
BNB Chain. It provides wallet management, a plugin module system, off-chain
storage abstraction, and built-in support for the following protocols:

- **ERC-8004** — On-chain identity registry for AI agents (register, discover,
  resolve endpoints).
- **APEX Protocol v1** — a three-layer agentic commerce stack:
  - **AgenticCommerce** (ERC-8183 kernel) — job lifecycle + escrow
    (create / setBudget / fund / submit / complete / reject / claimRefund).
  - **EvaluatorRouter** — routing layer that doubles as `job.evaluator`
    and `job.hook`. Binds `jobId → policy` on `registerJob`, pulls verdicts
    on the permissionless `settle`.
  - **OptimisticPolicy** — reference UMA-style policy. Silence past the
    dispute window is implicit approval; a client can raise a dispute
    that the whitelisted voters resolve by `voteReject` reaching quorum.

The SDK is organized as a **plugin system**: each protocol is a self-contained
module that can be used independently or composed via the `BNBAgent` facade.
New protocols can be added as modules without modifying the SDK core.
Wallet signing and off-chain storage are abstracted behind provider interfaces,
making the SDK backend-agnostic.

```
                    ┌─────────────┐
                    │  BNBAgent   │  optional facade (main.py)
                    │  from_env() │
                    └──────┬──────┘
                           │ discovers & initializes
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐ ┌───▼────┐ ┌────▼─────┐
        │  erc8004  │ │  apex  │ │ wallets  │
        │  Identity │ │  v1    │ │ Signing  │
        └─────┬─────┘ └───┬────┘ └────┬─────┘
              │           │           │
              └────┬──────┘           │
                   │                  │
            ┌──────▼──────┐    ┌──────▼──────┐
            │    core     │    │   storage   │
            │ (infra)     │    │ (off-chain) │
            └─────────────┘    └─────────────┘
```

Arrows point **downward** — upper layers depend on lower layers, never the
reverse. `apex` depends on `erc8004` (for agent discovery). Both protocol
modules depend on `core` for transaction management.

## Code Map

### `bnbagent/` — Main Package

| File | Purpose |
|------|---------|
| `__init__.py` | Tier 1 public API (re-exports from subpackages) |
| `main.py` | `BNBAgent` — optional high-level facade over the module system |
| `config.py` | `BNBAgentConfig`, `NetworkConfig`, `NETWORKS` registry, `resolve_network()` |
| `constants.py` | Global constants (`SCAN_API_URL`) |
| `exceptions.py` | `BNBAgentError` hierarchy |

### `bnbagent/core/` — Internal Infrastructure

Not part of the public API. Provides shared plumbing for protocol modules.

| File | Purpose |
|------|---------|
| `module.py` | `BNBAgentModule` ABC and `ModuleInfo` dataclass |
| `registry.py` | `ModuleRegistry` — discovery (built-in + entry points), dependency validation, topological initialization |
| `contract_mixin.py` | `ContractClientMixin` — shared base for `CommerceClient`, `RouterClient`, `PolicyClient`, `MinimalERC20Client` (tx signing, nonce management, retry with backoff) |
| `nonce_manager.py` | `NonceManager` — per-account thread-safe nonce tracking with chain re-sync |
| `multicall.py` | `multicall_read()` — Multicall3 batch view helper |
| `paymaster.py` | `Paymaster` — ERC-4337 gas sponsorship client |
| `abi_loader.py` | ABI file loading from bundled JSON |

### `bnbagent/erc8004/` — ERC-8004 Identity Registry

| File | Purpose |
|------|---------|
| `agent.py` | `ERC8004Agent` — high-level SDK: `register_agent()`, `get_agent_info()`, `get_all_agents()` |
| `contract.py` | `ContractInterface` — low-level web3 contract calls |
| `models.py` | `AgentEndpoint` dataclass |
| `constants.py` | `get_erc8004_config()` — per-network contract addresses |
| `module.py` | `ERC8004Module` plugin |

### `bnbagent/apex/` — APEX Protocol v1

High-level facade over three contracts. Most callers only touch `APEXClient`.

| File | Purpose |
|------|---------|
| `client.py` | `APEXClient` — facade over Commerce / Router / Policy; floor-based `fund` approval; cached `payment_token` / `token_decimals` / `token_symbol`; high-level wrappers for `create_job`, `register_job`, `set_budget`, `fund`, `submit`, `settle`, `dispute`, `vote_reject`, `claim_refund` |
| `commerce.py` | `CommerceClient` — low-level wrapper for `AgenticCommerceUpgradeable` |
| `router.py` | `RouterClient` — low-level wrapper for `EvaluatorRouterUpgradeable` |
| `policy.py` | `PolicyClient` — low-level wrapper for `OptimisticPolicy` (dispute / voteReject / check / voter admin) |
| `_erc20.py` | `MinimalERC20Client` — internal-only, used by `APEXClient` for the payment token (decimals/symbol/balanceOf/allowance/approve) |
| `types.py` | `JobStatus`, `Verdict`, `REASON_APPROVED`, `REASON_REJECTED`, `Job` dataclass |
| `config.py` | `APEXConfig` — unified config (wallet_provider + storage + contract overrides) |
| `negotiation.py` | `NegotiationHandler`, structured description schema, quote expiry |
| `service_record.py` | `ServiceRecord`, `RequestData`, `ResponseData` — deliverable structure |
| `constants.py` | `get_apex_config()` — per-network defaults |
| `module.py` | `APEXModule` plugin |

### `bnbagent/apex/server/` — FastAPI Integration

| File | Purpose |
|------|---------|
| `routes.py` | `create_apex_app()` FastAPI factory; `APEXState`; `/apex/submit`, `/apex/job/{id}`, `/apex/job/{id}/settle`, `/apex/negotiate`, `/apex/status`, `/apex/health`, optional `/apex/job/execute` |
| `job_ops.py` | `APEXJobOps` — async wrapper over `APEXClient`; startup scan; `auto_settle_once` + `run_auto_settle_loop` for permissionless `router.settle` on this provider's submitted jobs |

### `bnbagent/wallets/` — Wallet Providers

| File | Purpose |
|------|---------|
| `wallet_provider.py` | `WalletProvider` ABC — `address`, `sign_transaction()`, `sign_message()` |
| `evm_wallet_provider.py` | `EVMWalletProvider` — Keystore V3 encryption (scrypt + AES-128-CTR) |
| `mpc_wallet_provider.py` | `MPCWalletProvider` — stub for future MPC signer support |

### `bnbagent/storage/` — Storage Providers

| File | Purpose |
|------|---------|
| `interface.py` | `StorageProvider` ABC — async `upload()`, `download()`, `exists()`, `compute_hash()` |
| `config.py` | `StorageConfig` dataclass |
| `factory.py` | `create_storage_provider(config)` factory |
| `local_provider.py` | `LocalStorageProvider` — filesystem (`file://` URLs) |
| `ipfs_provider.py` | `IPFSStorageProvider` — IPFS pinning via HTTP API (Pinata-compatible) |
| `sync_utils.py` | `upload_sync()` — synchronous bridge |

### `examples/`

| Directory | Role | What it demonstrates |
|-----------|------|----------------------|
| `client/` | Client | 5 stand-alone scripts — happy / dispute-reject / stalemate-expire / never-submit / cancel-open |
| `voter/` | Voter | `voteReject` script + `Disputed` event watcher |
| `agent-server/` | Provider | FastAPI agent with startup scan, direct negotiate/submit endpoints, and auto-settle background loop |

### `tests/` — Test Suite

`pytest` + `pytest-mock` + `pytest-asyncio`. Tests mock web3 and external
services; no live chain calls in CI.

## Public API

**Tier 1** — importable directly from `bnbagent`:

```python
from bnbagent import (
    BNBAgent, BNBAgentConfig, NetworkConfig, BNBAgentError,
    ERC8004Agent, AgentEndpoint,
    WalletProvider, EVMWalletProvider,
    APEXClient, JobStatus, Verdict,
    StorageConfig,
)
```

**Tier 2** — import from subpackages:

```python
from bnbagent.apex import (
    APEXClient, CommerceClient, RouterClient, PolicyClient,
    JobStatus, Verdict, Job,
)
from bnbagent.apex.server import create_apex_app, APEXJobOps, run_auto_settle_loop
from bnbagent.apex.config import APEXConfig
from bnbagent.storage import LocalStorageProvider, IPFSStorageProvider
```

## Module System

The SDK uses a plugin architecture. Every protocol is a `BNBAgentModule`
subclass discovered and managed by `ModuleRegistry`.

**Lifecycle:**

1. `discover()` — imports built-in modules (`erc8004`, `apex`) + scans
   `bnbagent.modules` entry-point group for third-party plugins
2. `validate_dependencies()` — ensures all declared dependencies are present
3. `_topological_sort()` — orders modules so dependencies initialize first
4. `initialize_all(config)` — calls `module.initialize()` in order
5. `shutdown_all()` — cleanup in reverse order

**Extending:** implement `BNBAgentModule`, expose a `create_module()` factory,
and register via `pyproject.toml` entry points:

```toml
[project.entry-points."bnbagent.modules"]
my_module = "my_package:create_module"
```

## Configuration

```
NetworkConfig (NETWORKS dict in config.py)
  ├── bsc-testnet  (chain_id=97)  — active, APEX v1 + ERC-8004 deployed
  └── bsc-mainnet  (chain_id=56)  — placeholder, contracts pending

resolve_network(name) + env var overrides
  ↓
BNBAgentConfig
  ├── wallet_provider  (explicit or auto-wrapped from private_key)
  ├── settings         (general key-value)
  └── modules          (namespaced: {"apex": {"commerce_address": "0x..."}})
```

**Environment variable overrides** (module-scoped):

| Variable | Scope | Overrides |
|----------|-------|-----------|
| `RPC_URL` | global (`resolve_network`) | `rpc_url` |
| `APEX_COMMERCE_ADDRESS` | `APEXConfig.effective_network` | `commerce_contract` |
| `APEX_ROUTER_ADDRESS` | `APEXConfig.effective_network` | `router_contract` |
| `APEX_POLICY_ADDRESS` | `APEXConfig.effective_network` | `policy_contract` |
| `ERC8004_REGISTRY_ADDRESS` | `get_erc8004_config` | `registry_contract` |

`resolve_network()` itself only honours `RPC_URL`. Contract-address overrides
are applied by each module's own config loader — keeps each module's env
surface self-contained and obvious from the prefix.

When `network=NetworkConfig(...)` is passed directly (instead of a preset
name), env overrides are **not** applied — the object is used as-is.

Payment token address is NOT configurable — it is immutable on the Commerce
kernel and fetched at runtime via `APEXClient.payment_token`.

Both `BNBAgentConfig` and `APEXConfig` support the convenience pattern:
pass `private_key` + `wallet_password` and the config auto-wraps them into
an `EVMWalletProvider`, then **clears the plaintext key**.

## Invariants

These properties hold across the codebase and should be preserved:

- **No plaintext keys in config after construction.** `__post_init__()` wraps
  `private_key` into a `WalletProvider` and zeros the string field.
- **Modules never import each other directly.** Inter-module communication
  goes through the registry or shared config. Module dependencies are declared
  in `ModuleInfo.dependencies` and enforced at initialization.
- **`ContractClientMixin` prefers `wallet_provider` over raw `private_key`.**
- **Storage providers are async.** Synchronous callers use `upload_sync()`
  to avoid blocking the event loop.
- **Nonce management is per-account singleton.** `NonceManager.for_account()`
  ensures one manager per address to prevent collisions in concurrent code.
- **Retry with backoff on rate limits (429) and nonce conflicts.** Up to 5
  retries with exponential backoff. Nonce errors trigger chain re-sync.
- **Payment token is dynamically fetched.** It is never part of `NetworkConfig`
  or `APEXConfig` because it is an immutable property of the deployed
  Commerce kernel.

## Data Flows

### Agent Registration (ERC-8004)

```
ERC8004Agent.register_agent(name, endpoint, ...)
  → ContractInterface.register(...)
    → ContractClientMixin._send_tx()
      → WalletProvider.sign_transaction()
      → web3.eth.send_raw_transaction()
  → On-chain: IdentityRegistry stores agent metadata
```

### APEX v1 Job Lifecycle

Happy path (silence approve):

```
1. Discover provider             →  ERC8004Agent.get_all_agents()
2. Negotiate price (off-chain)   →  NegotiationHandler (HTTP)
3. createJob(provider, router)   →  APEXClient.create_job(...)       Open
4. registerJob(jobId, policy)    →  APEXClient.register_job(...)     Open
5. setBudget(jobId, amount)      →  APEXClient.set_budget(...)       Open
6. approve(commerce, amount) +
   fund(jobId, amount)           →  APEXClient.fund(...)             Funded
                                    (floor-based auto-approval)
7. Provider submit(deliverable)  →  APEXClient.submit(...)           Submitted
8. Wait dispute window           →  time passes
9. router.settle(jobId, "")      →  APEXClient.settle(...)           Completed
   (permissionless; the provider agent's auto-settle loop handles it)
```

Dispute branches:

- Client calls `APEXClient.dispute(jobId)` during the window → voters cast
  `APEXClient.vote_reject(jobId)` → once `rejectVotes >= quorum`, `settle`
  moves the job to **REJECTED** and refunds the client.
- No quorum ever reached → `settle` stays blocked; once `expiredAt` passes,
  anyone calls `APEXClient.claim_refund(jobId)` → **EXPIRED**.

`claimRefund` is non-pausable and non-hookable by design — the universal
escape hatch at expiry.

### Server Request Flow (FastAPI)

```
HTTP request
  → Route handler (routes.py)
  → APEXJobOps (async) → asyncio.to_thread() → APEXClient (sync/web3)
  → Response

Background tasks
  - Startup scan: scan jobCounter, auto-process FUNDED jobs for this provider.
  - Auto-settle loop: poll policy.check for our Submitted jobs, call
    router.settle once verdict flips away from PENDING.
```

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

## Extension Points

- **Custom WalletProvider** — subclass `WalletProvider` to support HSMs,
  multisig, or MPC signers.
- **Custom StorageProvider** — implement the `StorageProvider` ABC for
  alternative backends (S3, Arweave, etc.).
- **Custom Module** — extend `BNBAgentModule` and register via entry points
  to add new protocol support without modifying the SDK.

## Dependencies

| Category | Packages |
|----------|----------|
| Core | `web3 ≥ 6.15`, `eth-account ≥ 0.10`, `python-dotenv ≥ 1.0`, `requests ≥ 2.31` |
| Server (optional) | `fastapi ≥ 0.104`, `uvicorn ≥ 0.24` |
| IPFS (optional) | `httpx ≥ 0.25` |
| Dev | `pytest`, `pytest-mock`, `pytest-asyncio`, `ruff` |
