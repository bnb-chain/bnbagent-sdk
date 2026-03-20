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
- **APEX Protocol** — Agentic commerce protocol comprising multiple contracts:
  - **ERC-8183** — core job lifecycle contract (create, fund, complete, reject, refund).
  - **APEX Evaluator** — pluggable settlement contract; current implementation
    uses UMA Optimistic Oracle v3 (OOv3) for dispute resolution.

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
        │  erc8004   │ │  apex  │ │ wallets  │
        │  Identity  │ │Commerce│ │ Signing  │
        └─────┬──────┘ └───┬────┘ └────┬─────┘
              │            │            │
              └──────┬─────┘            │
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
| `exceptions.py` | `BNBAgentError` hierarchy (7 domain-specific exception types) |

### `bnbagent/core/` — Internal Infrastructure

Not part of the public API. Provides shared plumbing for protocol modules.

| File | Purpose |
|------|---------|
| `module.py` | `BNBAgentModule` ABC and `ModuleInfo` dataclass — the plugin contract |
| `registry.py` | `ModuleRegistry` — discovery (built-in + entry points), dependency validation, topological initialization |
| `contract_mixin.py` | `ContractClientMixin` — shared transaction signing, nonce management, and retry with exponential backoff |
| `nonce_manager.py` | `NonceManager` — thread-safe per-account nonce tracking with chain re-sync on conflict |
| `paymaster.py` | `Paymaster` — ERC-4337 gas sponsorship client |
| `abi_loader.py` | ABI file loading from bundled JSON |

### `bnbagent/erc8004/` — ERC-8004 Identity Registry

| File | Purpose |
|------|---------|
| `agent.py` | `ERC8004Agent` — high-level SDK: `register_agent()`, `get_agent_info()`, `get_all_agents()` |
| `contract.py` | `ContractInterface` — low-level web3 contract calls |
| `models.py` | `AgentEndpoint` dataclass (name, endpoint URL, version, capabilities) |
| `constants.py` | `get_erc8004_config()` — lazy per-network contract addresses |
| `module.py` | `ERC8004Module` plugin + `create_module()` factory |

### `bnbagent/apex/` — APEX Protocol

| File | Purpose |
|------|---------|
| `client.py` | `APEXClient` — ERC-8183 contract interaction (create/complete/cancel jobs); `APEXStatus` enum |
| `evaluator_client.py` | `APEXEvaluatorClient` — UMA optimistic oracle evaluator interface |
| `config.py` | `APEXConfig` — unified config (wallet_provider + storage + contract addresses) |
| `negotiation.py` | `NegotiationHandler`, request/response models, `TermSpecification`, `ReasonCode` |
| `service_record.py` | `ServiceRecord`, `RequestData`, `ResponseData` — dispute evidence structure |
| `constants.py` | `get_apex_config()` — lazy per-network contract addresses |
| `module.py` | `APEXModule` plugin (declares dependency on `erc8004`) |

### `bnbagent/apex/server/` — FastAPI Integration

| File | Purpose |
|------|---------|
| `routes.py` | `create_apex_app()` — FastAPI app factory; `APEXState` |
| `job_ops.py` | `APEXJobOps` — async wrapper over synchronous `APEXClient` via `asyncio.to_thread()`; includes `get_response()` for retrieving stored deliverables |

### `bnbagent/wallets/` — Wallet Providers

| File | Purpose |
|------|---------|
| `wallet_provider.py` | `WalletProvider` ABC — `address`, `sign_transaction()`, `sign_message()` |
| `evm_wallet_provider.py` | `EVMWalletProvider` — Keystore V3 encryption (scrypt + AES-128-CTR), `persist=True/False` |
| `mpc_wallet_provider.py` | `MPCWalletProvider` — stub for future MPC signer support |

### `bnbagent/storage/` — Storage Providers

| File | Purpose |
|------|---------|
| `interface.py` | `StorageProvider` ABC — async `upload()`, `download()`, `exists()`, `compute_hash()` |
| `config.py` | `StorageConfig` dataclass |
| `factory.py` | `create_storage_provider(config)` — factory function |
| `local_provider.py` | `LocalStorageProvider` — file-system storage (`file://` URLs) |
| `ipfs_provider.py` | `IPFSStorageProvider` — IPFS pinning via HTTP API (Pinata-compatible) |
| `sync_utils.py` | `upload_sync()` — synchronous bridge for calling async providers from non-async code |

### `bnbagent/utils/` — Utilities

| File | Purpose |
|------|---------|
| `agent_uri.py` | `AgentURIGenerator` — agent URI generation for discovery |
| `state_file.py` | `StateFileManager` — atomic JSON state persistence |

### `examples/` — Usage Examples

| Directory | What it demonstrates |
|-----------|---------------------|
| `getting-started/` | Step-by-step: wallet setup → agent registration → server → job creation → settlement |
| `client-workflow/` | Client-side job creation workflow |
| `agent-server/` | Full agent server with startup scan and client-driven job execution |
| `evaluator/` | Evaluator/keeper scripts (Node.js) |

### `tests/` — Test Suite

20+ test files covering all packages. Uses `pytest` + `pytest-mock` +
`pytest-asyncio`. Tests mock web3 and external services; no live chain calls
in CI.

## Public API Tiers

**Tier 1** — importable directly from `bnbagent`:

```python
from bnbagent import (
    BNBAgent, BNBAgentConfig, NetworkConfig, BNBAgentError,
    ERC8004Agent, AgentEndpoint,
    WalletProvider, EVMWalletProvider,
    APEXClient, APEXStatus,
    StorageConfig,
)
```

**Tier 2** — import from subpackages:

```python
from bnbagent.apex import NegotiationHandler, APEXEvaluatorClient
from bnbagent.apex.server import create_apex_app, APEXJobOps
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
  ├── bsc-testnet  (chain_id=97)  — active, all contracts deployed
  └── bsc-mainnet  (chain_id=56)  — placeholder, contracts pending

resolve_network(name) + env var overrides
  ↓
BNBAgentConfig
  ├── wallet_provider  (explicit or auto-wrapped from private_key)
  ├── settings         (general key-value)
  └── modules          (namespaced: {"apex": {"evaluator_address": "0x..."}})
```

**Environment variable overrides** (applied by `resolve_network()`):

| Variable | Overrides |
|----------|-----------|
| `RPC_URL` | `rpc_url` |
| `IDENTITY_REGISTRY_ADDRESS` | `registry_contract` |
| `ERC8183_ADDRESS` | `erc8183_contract` |
| `APEX_EVALUATOR_ADDRESS` | `apex_evaluator` |
| `PAYMENT_TOKEN_ADDRESS` | `payment_token` |

Both `BNBAgentConfig` and `APEXConfig` support a convenience pattern:
pass `private_key` + `wallet_password` and the config auto-wraps them into
an `EVMWalletProvider(persist=False)`, then **clears the plaintext key**.

## Invariants

These properties hold across the codebase and should be preserved:

- **No plaintext keys in config after construction.** `__post_init__()` wraps
  `private_key` into a `WalletProvider` and zeros the string field.
- **Modules never import each other directly.** Inter-module communication
  goes through the registry or shared config. Module dependencies are declared
  in `ModuleInfo.dependencies` and enforced at initialization.
- **`ContractClientMixin` prefers `wallet_provider` over raw `private_key`.**
  The raw-key path exists only for backward compatibility in low-level clients.
- **Storage providers are async.** Synchronous callers use `upload_sync()`
  (runs `asyncio.run()` in a thread pool) to avoid blocking the event loop.
- **Nonce management is per-account singleton.** `NonceManager.for_account()`
  ensures one manager per address to prevent nonce collisions in concurrent code.
- **Retry with backoff on rate limits (429) and nonce conflicts.** Up to 5
  retries with exponential backoff. Nonce errors trigger chain re-sync.

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

Other agents discover via `get_all_agents()` / `get_agent_info()`.

### Job Lifecycle (APEX)

```
1. Client discovers provider  →  ERC8004Agent.get_all_agents()
2. Price negotiation          →  NegotiationHandler (off-chain HTTP)
3. Client creates job         →  APEXClient.create_job()  →  on-chain escrow
4. Provider executes task     →  APEXJobOps (async server)
5. Provider uploads evidence  →  StorageProvider.upload()  →  ServiceRecord
6. Provider completes job     →  APEXClient.complete_job()
7. Evaluator settles          →  APEXEvaluatorClient  →  UMA oracle  →  payment release
```

### Server Request Flow (FastAPI)

```
HTTP request
  → Route handler (routes.py)
  → APEXJobOps (async) → asyncio.to_thread() → APEXClient (sync/web3)
  → Response
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
  multisig, or MPC signers. The SDK calls `sign_transaction()` and `address`
  without assuming a key-management backend.

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
