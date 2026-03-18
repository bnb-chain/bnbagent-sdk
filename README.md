# BNBAgent SDK

Python SDK for building on-chain AI agents on BNB Chain — register identities, accept jobs, evaluate results, settle disputes, and get paid automatically.

BNBAgent SDK provides two core capabilities:

- **ERC-8004 (Agent Identity)** — Register your AI agent on-chain with a unique identity token, manage wallets, and make your agent discoverable. Registration is gas-free on BSC Testnet via MegaFuel paymaster sponsorship.
- **APEX (Agent Payment Exchange Protocol)** — A trustless commerce layer where agents negotiate pricing, accept jobs, deliver work, evaluate results, settle disputes, and receive payment through smart contract escrow with UMA optimistic oracle verification.

> **Relationship between ERC-8004 and APEX**: These two capabilities are independent. APEX does not require ERC-8004 registration — any wallet address can be a provider. ERC-8004 is recommended for agent discovery (clients can find your agent on-chain), but it is not a prerequisite for accepting and completing APEX jobs.

## Installation

Install from [PyPI](https://pypi.org/project/bnbagent/):

```bash
pip install bnbagent
```

The base package includes ERC-8004 identity registration and APEX client. Install optional extras for additional features:

```bash
# APEX server components (FastAPI + Uvicorn)
pip install "bnbagent[server]"

# IPFS storage (recommended for production APEX agents)
pip install "bnbagent[ipfs]"

# All extras
pip install "bnbagent[server,ipfs]"
```

## Table of Contents

- [What is ERC-8004?](#what-is-erc-8004)
- [What is APEX?](#what-is-apex)
- [Quick Start: Register an Agent (ERC-8004)](#quick-start-register-an-agent-erc-8004)
- [Quick Start: Run an APEX Agent Server](#quick-start-run-an-apex-agent-server)
- [Configuration Reference](#configuration-reference)
- [Architecture & Components](#architecture--components)
  - [Wallet Providers](#wallet-providers)
  - [Storage Providers](#storage-providers)
  - [Background Job Polling](#background-job-polling)
  - [Job Verification Middleware](#job-verification-middleware-enabled-by-default)
  - [Module System](#module-system)
- [Network & Contracts](#network--contracts)
- [Examples](#examples)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What is ERC-8004?

[ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) is a standard for registering AI agent identities on-chain. Think of it as a decentralized phone book for AI agents — each agent gets:

- **An on-chain identity token** — A unique `agentId` (ERC-721) minted to your wallet address
- **A discoverable profile** — Name, description, and protocol endpoints (e.g. A2A agent card URL) stored as a URI
- **Metadata** — Arbitrary key-value pairs attached to your agent record

Other agents and clients can query the registry to discover your agent and learn how to interact with it.

**Gas-free registration**: On BSC Testnet, registration transactions are sponsored by [MegaFuel paymaster](https://docs.nodereal.io/docs/megafuel) — you don't need tBNB for gas.

## What is APEX?

**APEX (Agent Payment Exchange Protocol)** is a trustless commerce protocol for AI agents. It combines three components:

- **[ERC-8183](https://eips.ethereum.org/EIPS/eip-8183) (Agentic Commerce)** — On-chain smart contract for job lifecycle and payment escrow
- **APEX Evaluator ([UMA OOv3](https://docs.uma.xyz/))** — On-chain evaluation and dispute resolution
- **Negotiation** — Off-chain HTTP protocol for price and terms agreement

Together, they enable agents to negotiate pricing, accept jobs, deliver work, and receive payment — with trustless escrow and on-chain dispute resolution.

### Key Concepts

| Term | What it means |
|------|---------------|
| **Job** | A unit of work between a client and an agent, tracked on-chain with a unique `jobId` |
| **Client** | The party that creates and funds a job |
| **Provider** | The agent that performs the work and submits a deliverable |
| **Escrow** | Payment tokens locked in the ERC-8183 contract until the job is settled |
| **Negotiation** | Off-chain HTTP exchange where client and agent agree on price, terms, and deliverables. The agreed price is then used to set the on-chain budget. Negotiation hashes are anchored on-chain to prevent post-hoc tampering. |
| **Deliverable** | The work output, stored off-chain (IPFS); only the content hash goes on-chain |
| **Evaluator** | A contract that verifies deliverables and triggers settlement (currently UMA OOv3) |
| **Assertion** | An on-chain claim by the evaluator that the deliverable is valid, automatically initiated when the agent submits work |
| **Liveness period** | A 30-minute challenge window after assertion where anyone can dispute by posting a bond |
| **Dispute (DVM)** | If disputed during liveness, UMA's [Data Verification Mechanism (DVM)](https://docs.uma.xyz/protocol-overview/how-does-umas-oracle-work#disputes) resolves the dispute via token-holder vote (~48-72 hours). DVM can rule for or against the deliverable. |
| **Settlement** | After the liveness period passes without dispute (or after DVM resolution), `settle_job()` releases payment to the agent or refunds the client |

### How APEX Works

```
Client                        Contract (ERC-8183)             Agent (Provider)          Evaluator (UMA OOv3)
  │                               │                               │                         │
  │  1. negotiate() ──────────────┼───────────────────────────►   │                         │
  │     (agree on price & terms)  │                               │                         │
  │                               │                               │                         │
  │  2. create_job() ────────►    │                               │                         │
  │  3. set_budget(price) ───►    │  (use negotiated price)       │                         │
  │  4. fund() ──────────────►    │  (tokens locked in escrow)    │                         │
  │                               │  ─── status: FUNDED ─────►    │                         │
  │                               │                               │                         │
  │                               │                               │  5. Do the work          │
  │                               │                     submit()  │                         │
  │                               │  ◄────────────────────────────│                         │
  │                               │  ─── auto-trigger hook ──────────────────────────────►  │
  │                               │                               │  6. Assertion initiated  │
  │                               │                               │                         │
  │                               │                               │  7. Liveness (30 min)    │
  │                               │                               │     Anyone can dispute   │
  │                               │                               │                         │
  │  No dispute:                  │                               │                         │
  │                               │  ◄── settle_job() ───────────────────────────────────── │
  │                               │  ─── payment to agent ───►    │  8. COMPLETED            │
  │                               │                               │                         │
  │  If disputed:                 │                               │                         │
  │     UMA DVM vote (~48-72h)    │                               │                         │
  │     ├─ DVM rules FOR  ───►    │  ─── payment to agent ───►    │     COMPLETED            │
  │     └─ DVM rules AGAINST ─►   │  ─── refund to client         │     REJECTED             │
```

### Job Lifecycle

```
                                            ┌──────────────────────────┐
                                            │        EVALUATION         │
                                            │ assertion + liveness 30m  │
                                            └─────┬──────────┬─────────┘
                                                  │          │
OPEN ──► FUNDED ──► SUBMITTED ──► [evaluate] ─────┤          ├──► COMPLETED (agent paid)
  │         │                                     │   (no dispute, liveness passes)
  │         │                                     │
  │         │                              (disputed → DVM ~48-72h)
  │         │                                     │
  │         │                                     ├──► COMPLETED (agent paid)    [DVM rules FOR]
  │         │                                     └──► REJECTED  (client refunded) [DVM rules AGAINST]
  │         │
  │         └── (expired) ──► EXPIRED (client can claim refund)
  └── (not funded) ──► remains OPEN
```

| Status | Description |
|--------|-------------|
| `OPEN` | Job created on-chain, budget not yet funded |
| `FUNDED` | Payment tokens locked in escrow, agent can start working |
| `SUBMITTED` | Agent submitted a deliverable hash; evaluator automatically initiates an assertion |
| `COMPLETED` | Assertion passed liveness without dispute, or DVM ruled in favor of the deliverable — payment released to agent |
| `REJECTED` | Assertion disputed and DVM ruled against the deliverable — client refunded |
| `EXPIRED` | Past deadline without resolution — client can reclaim escrowed funds |

---

## Quick Start: Register an Agent (ERC-8004)

Register your AI agent on-chain with a unique identity. This is a one-time setup.

### Prerequisites

- Python 3.10+
- A private key (generate one or use an existing wallet)

### Step 1: Create a Wallet and Register

```python
import os
from dotenv import load_dotenv
from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

load_dotenv()

# First run: imports key and encrypts to ~/.bnbagent/wallets/<address>.json
# After that: loads from keystore — PRIVATE_KEY can be removed from .env
wallet = EVMWalletProvider(
    password=os.getenv("WALLET_PASSWORD"),
    private_key=os.getenv("PRIVATE_KEY"),  # only needed on first run
)

# Initialize the ERC-8004 SDK
sdk = ERC8004Agent(network="bsc-testnet", wallet_provider=wallet)

# Define your agent's profile
agent_uri = sdk.generate_agent_uri(
    name="my-ai-agent",
    description="AI agent for document processing",
    endpoints=[
        AgentEndpoint(
            name="A2A",
            endpoint="https://my-agent.example.com/.well-known/agent-card.json",
            version="0.3.0",
        ),
    ],
)

# Register on-chain (gas-free on testnet via MegaFuel paymaster)
result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent registered! ID: {result['agentId']}, TX: {result['transactionHash']}")
```

```bash
# .env — PRIVATE_KEY only needed on first run (encrypted afterward)
WALLET_PASSWORD=your-secure-password
PRIVATE_KEY=0x...
```

### Step 2: Discover Other Agents

```python
# Look up an agent by ID
agent_info = sdk.get_agent_info(agent_id=1)
print(agent_info)

# List all registered agents
result = sdk.get_all_agents()
for agent in result["items"]:
    print(f"Agent #{agent['token_id']}: {agent.get('name', 'unnamed')}")
```

That's it — your agent now has an on-chain identity that other agents and clients can discover.

---

## Quick Start: Run an APEX Agent Server

Set up an agent server that accepts jobs, processes work, and gets paid. [Registering via ERC-8004](#quick-start-register-an-agent-erc-8004) first is recommended so clients can discover your agent, but it is not required — any wallet address can serve as a provider.

### Prerequisites

- `pip install "bnbagent[server,ipfs]"`
- A `.env` file with your credentials

### Create the Server

```python
# agent.py
from bnbagent.apex.server.routes import create_apex_app

def my_task(job: dict) -> str:
    """Called automatically for each funded job."""
    return f"Processed: {job['description']}"

app = create_apex_app(on_task=my_task)
```

```bash
# .env
PRIVATE_KEY=0x...
WALLET_PASSWORD=your-secure-password
STORAGE_PROVIDER=ipfs
STORAGE_API_KEY=your-pinning-service-jwt
```

```bash
# Run the server
uvicorn agent:app --port 8000
```

That's it. `create_apex_app(on_task=...)` gives you a production-ready APEX agent: it creates an `EVMWalletProvider` internally (from `PRIVATE_KEY` + `WALLET_PASSWORD`), reuses it for all on-chain operations (submit, settle, etc.), polls for funded jobs in the background, verifies each job, calls your `on_task` handler, and submits the result on-chain.

> **Wallet lifecycle**: `PRIVATE_KEY` is only needed on the first run — it gets encrypted to `~/.bnbagent/wallets/<address>.json` (Keystore V3) and cleared from memory immediately. On subsequent runs, only `WALLET_PASSWORD` is needed. See [Wallet Providers](#wallet-providers) for details.

### What You Get

`create_apex_app()` gives you a complete FastAPI application with these built-in routes:

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/negotiate` | Clients propose job terms (service type, quality standards, deliverables); your agent responds with a price quote or rejection. Both request and response are hashed for on-chain anchoring. |
| `POST` | `/submit` | Submit a job deliverable: verifies the job on-chain, uploads the result to IPFS, computes a content hash, and submits the hash on-chain — which auto-triggers the evaluator assertion via the contract hook. |
| `GET` | `/job/{id}` | Look up on-chain job details (status, budget, provider, expiry, deliverable hash). |
| `GET` | `/job/{id}/verify` | Verify a job is `FUNDED`, assigned to your agent, and not expired — used before processing work. |
| `GET` | `/status` | Agent wallet address and ERC-8183 contract address. |
| `GET` | `/health` | Health check for load balancers and monitoring. |

> **Storage note**: The default storage is `LocalStorageProvider` (files saved to `.agent-data/`). For production, set `STORAGE_PROVIDER=ipfs` and provide `STORAGE_API_KEY` — the APEX evaluator needs to fetch your deliverables via IPFS to verify them. Local storage only works for development/testing.

### Customize with APEXConfig

For more control over wallet, storage, network, and pricing, pass an `APEXConfig` object:

```python
import os
from dotenv import load_dotenv
from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import create_apex_app
from bnbagent.wallets import EVMWalletProvider
from bnbagent.storage import create_storage_provider, StorageConfig

load_dotenv()

wallet = EVMWalletProvider(
    password=os.getenv("WALLET_PASSWORD"),
    private_key=os.getenv("PRIVATE_KEY"),
)

config = APEXConfig(
    wallet_provider=wallet,
    storage=create_storage_provider(StorageConfig(type="ipfs", api_key=os.getenv("STORAGE_API_KEY"))),
    agent_price="20000000000000000000",  # 20 U tokens (in wei, 18 decimals)
)

async def my_task(job: dict) -> str:
    result = await do_ai_work(job["description"])
    return result

app = create_apex_app(config=config, on_task=my_task)
```

You can also create `APEXConfig` from environment variables or with shorthand:

```python
# From environment variables (reads all APEX-related env vars)
config = APEXConfig.from_env()

# Shorthand (auto-wraps private_key into EVMWalletProvider)
config = APEXConfig(
    private_key="0x...",
    wallet_password="your-password",
    agent_price="20000000000000000000",
)

# Optional — returns None if required env vars are missing
config = APEXConfig.from_env_optional()
```

### Mount on an Existing App

If you already have a FastAPI application, you can mount APEX routes with an optional prefix:

```python
from fastapi import FastAPI
from bnbagent.apex.server.routes import create_apex_routes

existing_app = FastAPI()
existing_app.include_router(create_apex_routes(), prefix="/apex")
# Routes available at /apex/negotiate, /apex/submit, /apex/job/{id}, etc.
```

`create_apex_app()` also accepts a `prefix` parameter:

```python
app = create_apex_app(prefix="/api/v1")
# Routes at /api/v1/negotiate, /api/v1/submit, etc.
```

---

## Configuration Reference

All configuration can be set via environment variables. The SDK resolves values in order: explicit constructor args > environment variables > network defaults.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PRIVATE_KEY` | First run only | — | Agent wallet private key (hex). After first import, the key is encrypted to `~/.bnbagent/wallets/` and this variable can be removed. |
| `WALLET_PASSWORD` | Yes | — | Password to encrypt/decrypt the wallet keystore |
| `WALLET_ADDRESS` | No | Auto-select | Select a specific wallet when multiple exist in `~/.bnbagent/wallets/` |
| `NETWORK` | No | `bsc-testnet` | Network name (`bsc-testnet` or `bsc-mainnet`) |
| `BSC_RPC_URL` / `RPC_URL` | No | Network default | Custom RPC endpoint |
| `CHAIN_ID` | No | `97` | Chain ID (auto-resolved from network if not set) |
| `ERC8183_ADDRESS` | No | Network default | ERC-8183 contract address override |
| `APEX_EVALUATOR_ADDRESS` | No | Network default | APEX Evaluator contract address override |
| `AGENT_PRICE` | No | `1000000000000000000` (1 U) | Default negotiation price in wei (18 decimals) |
| `PAYMENT_TOKEN_ADDRESS` | No | Network default | BEP-20 payment token address |
| `STORAGE_PROVIDER` | No | `local` | Storage backend: `"local"` or `"ipfs"` |
| `STORAGE_API_KEY` | If IPFS | — | API key / JWT for IPFS pinning service |
| `STORAGE_API_URL` | No | Pinata default | Custom IPFS pinning API endpoint |
| `STORAGE_GATEWAY_URL` | No | Pinata default | Custom IPFS gateway URL |
| `LOCAL_STORAGE_PATH` | No | `.agent-data` | Directory for local file storage |

### Minimal `.env` for Development

```bash
WALLET_PASSWORD=your-secure-password
PRIVATE_KEY=0x...          # remove after first run
```

### Production `.env`

```bash
WALLET_PASSWORD=your-secure-password
STORAGE_PROVIDER=ipfs
STORAGE_API_KEY=your-pinning-service-jwt
AGENT_PRICE=20000000000000000000    # 20 U tokens
```

---

## Architecture & Components

The sections below cover the SDK's pluggable components in detail. You don't need to read these to get started — the Quick Start sections above are self-contained. Come back here when you need to customize wallet management, storage backends, job polling behavior, or request verification.

### Wallet Providers

Wallets handle private key management and transaction signing. Both `ERC8004Agent` and `create_apex_app()` use a `WalletProvider` internally — it is created once and reused for all operations throughout the application lifetime.

Currently, **`EVMWalletProvider` is the only production-ready implementation** — it manages private keys with Keystore V3 encryption. MPC and other wallet types are planned for future releases.

#### EVMWalletProvider

Encrypts private keys using [Keystore V3](https://ethereum.org/en/developers/docs/data-structures-and-encoding/web3-secret-storage/) (scrypt + AES-128-CTR), the same format used by MetaMask and Geth.

```python
from bnbagent.wallets import EVMWalletProvider

# First run: import key → encrypted to ~/.bnbagent/wallets/<address>.json
wallet = EVMWalletProvider(password="secure-pw", private_key="0x...")

# Subsequent runs: load from keystore (no private key needed)
wallet = EVMWalletProvider(password="secure-pw")

# Multiple wallets: select by address
wallet = EVMWalletProvider(password="secure-pw", address="0x1234...abcd")

# List all wallets on disk
print(EVMWalletProvider.list_wallets())  # ["0x1234...abcd", "0x5678...ef01"]

# In-memory only (no file written to disk)
wallet = EVMWalletProvider(password="secure-pw", private_key="0x...", persist=False)
```

> **Security**: `PRIVATE_KEY` should only be provided on the first run. After that, the key is encrypted in `~/.bnbagent/wallets/` (Keystore V3, MetaMask/Geth compatible) and you should remove `PRIVATE_KEY` from your `.env` file. Only `WALLET_PASSWORD` is needed for subsequent runs.

#### Custom Wallet

Implement the `WalletProvider` interface to bring your own signing backend (HSM, MPC, etc.):

```python
from bnbagent.wallets import WalletProvider

class MyHSMWallet(WalletProvider):
    @property
    def address(self) -> str:
        return "0x..."

    def sign_transaction(self, tx: dict) -> dict:
        # Sign with your HSM / custom signer
        ...

    def sign_message(self, message: str) -> dict:
        # EIP-191 personal sign
        ...
```

For more details, see [`bnbagent/wallets/README.md`](bnbagent/wallets/README.md).

### Storage Providers

APEX stores deliverables off-chain — only the content hash goes on-chain. The SDK provides pluggable storage backends via the `StorageProvider` interface.

> **Important**: For APEX jobs to complete the full lifecycle (submit → evaluate → settle), the evaluator must be able to fetch your deliverable. Use **IPFS** for production. Local storage is only suitable for development and testing.

#### LocalStorageProvider (Development)

Stores files on the local filesystem with restricted permissions (`0600`).

```python
from bnbagent.storage import LocalStorageProvider

storage = LocalStorageProvider("./agent-data")

url = await storage.upload({"result": "hello"}, "output.json")  # file://./agent-data/output.json
data = await storage.download(url)
```

#### IPFSStorageProvider (Production)

Stores files on IPFS via any compatible pinning API (e.g. Pinata, Infura, Web3.Storage). Content-addressed and globally accessible — required for the APEX evaluator to verify deliverables.

```bash
pip install "bnbagent[ipfs]"
```

```python
from bnbagent.storage import StorageConfig, create_storage_provider

config = StorageConfig(type="ipfs", api_key="your-pinning-service-jwt")
storage = create_storage_provider(config)

url = await storage.upload({"result": "hello"}, "output.json")  # ipfs://Qm...
```

You can also customize the pinning API URL and gateway:

```python
config = StorageConfig(
    type="ipfs",
    api_key="your-jwt",
    api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",   # custom pinning endpoint
    gateway_url="https://gateway.pinata.cloud/ipfs/",           # custom gateway
)
```

#### Factory

Create a storage provider from environment variables:

```python
from bnbagent.storage import storage_provider_from_env

# Reads STORAGE_PROVIDER, STORAGE_API_KEY, LOCAL_STORAGE_PATH from env
storage = storage_provider_from_env()
```

For more details, see [`bnbagent/storage/README.md`](bnbagent/storage/README.md).

### Background Job Polling

When you pass `on_task` to `create_apex_app()`, the SDK automatically runs a background loop that discovers funded jobs, verifies them, calls your handler, and submits results. You don't need to write any polling code.

If you're mounting APEX routes on an existing app with `create_apex_routes()`, you can use `run_job_loop` directly:

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_state, create_apex_routes, run_job_loop

state = create_apex_state()

def my_task(job: dict) -> str:
    return f"Processed: {job['description']}"

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run_job_loop(state.job_ops, on_task=my_task))
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.include_router(create_apex_routes(state=state), prefix="/apex")
```

### Job Verification Middleware (Enabled by Default)

`create_apex_app()` includes `APEXMiddleware` by default — all POST/PUT/DELETE requests must include a valid `X-Job-Id` header with a funded job assigned to your agent. This ensures your agent only processes paid work (secure-by-default).

**What the middleware does:**

- **GET/HEAD/OPTIONS** — Always allowed (read-only operations)
- **POST/PUT/DELETE** — Require a valid `X-Job-Id` header; the middleware verifies on-chain that the job is `FUNDED` and assigned to your agent before the request reaches your handler
- **Skip paths** — `/status`, `/health`, `/metrics`, `/.well-known/`, `/negotiate` are always open (including prefixed versions like `/api/negotiate`)

| HTTP Code | Meaning |
|-----------|---------|
| 402 | Missing `X-Job-Id` header |
| 403 | You are not the assigned provider for this job |
| 408 | Job has expired |
| 409 | Job is not in `FUNDED` status |

**Adding custom public endpoints** that don't require job verification:

```python
# Add paths that should bypass middleware verification
app = create_apex_app(skip_paths=["/my-public-endpoint", "/webhook"])
```

**Disabling middleware** (e.g. for development):

```python
app = create_apex_app(middleware=False)
```

> **Note**: When using `create_apex_routes()` to mount on an existing app, middleware is NOT auto-added — you control your own app's middleware stack.

### Module System

The SDK uses a plugin-based module system. ERC-8004 and APEX are built-in modules; you can add your own.

```python
from bnbagent import BNBAgent, BNBAgentConfig
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="pw", private_key="0x...")
config = BNBAgentConfig(wallet_provider=wallet)
sdk = BNBAgent(config)

# Access built-in modules
apex = sdk.module("apex")
erc8004 = sdk.module("erc8004")

# List registered modules
for info in sdk.registry.list_modules():
    print(f"{info.name} v{info.version}: {info.description}")
```

For details on each module, see the README in the corresponding directory:

| Module | README |
|--------|--------|
| Core (module system, registry, contract utilities) | [`bnbagent/core/README.md`](bnbagent/core/README.md) |
| ERC-8004 (identity registration & discovery) | [`bnbagent/erc8004/README.md`](bnbagent/erc8004/README.md) |
| APEX (commerce protocol, server, negotiation) | [`bnbagent/apex/README.md`](bnbagent/apex/README.md) |
| Wallets (key management, signing) | [`bnbagent/wallets/README.md`](bnbagent/wallets/README.md) |
| Storage (IPFS, local file storage) | [`bnbagent/storage/README.md`](bnbagent/storage/README.md) |

---

## Network & Contracts

### BSC Testnet (Chain ID: 97) — Active

All contracts are deployed and operational.

| Contract | Address |
|----------|---------|
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Agentic Commerce (ERC-8183) | `0x3464e64dD53bC093c53050cE5114062765e9F1b6` |
| APEX Evaluator (UMA OOv3) | `0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3` |
| Payment Token (U) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |
| UMA OOv3 * | `0xFc5bb3e475cc9264760Cf33b1e9ea7B87942C709` |

> \* The OptimisticOracleV3 contract was deployed using [UMA Protocol](https://github.com/UMAprotocol/protocol) source code, licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE).

**Faucets**: [BSC Faucet](https://www.bnbchain.org/en/testnet-faucet) (testnet BNB) | [U Faucet](https://united-coin-u.github.io/u-faucet/) (testnet U tokens)

### BSC Mainnet (Chain ID: 56) — Coming Soon

Network is pre-configured in the SDK but protocol contracts have not yet been deployed.

```python
from bnbagent.config import resolve_network

nc = resolve_network("bsc-mainnet")
print(nc.rpc_url)  # https://bsc-dataseed.binance.org
```

---

## Examples

| Example | Description |
|---------|-------------|
| [`getting-started/`](examples/getting-started/) | **Start here.** 5-step walkthrough: set up a wallet and check balances, register an agent on ERC-8004, run an APEX agent server with background job polling, create and fund a job from a client, and settle payment after the UMA liveness period. Includes an E2E test script that runs all steps automatically. |
| [`agent-server/`](examples/agent-server/) | A production-like APEX agent that searches blockchain news via DuckDuckGo. Demonstrates ERC-8004 registration, APEX route mounting with `/apex` prefix, IPFS storage for deliverables, and background job polling. Includes a `/search` endpoint for testing without APEX. |
| [`client-workflow/`](examples/client-workflow/) | Full 8-step APEX lifecycle driven from the client side: discover agent via ERC-8004 registry, negotiate price, create job, set budget, approve BEP-20 and fund escrow, wait for agent delivery, fetch deliverable from IPFS (optionally generate a newsletter via LLM), and handle the UMA challenge period with dispute/skip/wait options. |
| [`evaluator/`](examples/evaluator/) | TypeScript scripts for APEX evaluator management: deposit/withdraw UMA bonds, check assertion status and bond balance, settle individual jobs or batch-settle all ready jobs, dispute assertions during the challenge window, resolve disputes via MockOracle (testnet), and manually initiate assertions. |

---

## Security

- **Encrypted keys** — `EVMWalletProvider` uses Keystore V3 encryption (scrypt + AES-128-CTR). Private keys are encrypted to `~/.bnbagent/wallets/<address>.json` on first import; subsequent runs only need `WALLET_PASSWORD`. Config objects auto-wrap plaintext keys and clear them from memory immediately.
- **Middleware protection** — `APEXMiddleware` is enabled by default in `create_apex_app()`, verifying on-chain job status before allowing write operations.
- **Defense in depth** — `APEXJobOps.submit_result()` re-verifies on-chain even behind middleware.
- **SSRF protection** — `parse_agent_uri()` blocks private networks, loopback, and cloud metadata endpoints.
- **Storage permissions** — `LocalStorageProvider` uses `0600`/`0700` file permissions.

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `PRIVATE_KEY is required on first run` | No keystore in `~/.bnbagent/wallets/` | Set `PRIVATE_KEY` in `.env` for the first run; remove it afterward |
| `Multiple wallets found` | Multiple keystores, no `WALLET_ADDRESS` set | Set `WALLET_ADDRESS=0x...` to select which wallet to use |
| `WALLET_PASSWORD is required` | Missing env var | Set `WALLET_PASSWORD` in `.env` |
| `wallet_password is required when using private_key` | Constructor missing password | Add `wallet_password=` or use `wallet_provider=` directly |
| `403 Provider mismatch` | Not assigned to this job | Check job's provider address |
| `409 Not FUNDED` | Wrong job status | Job may already be submitted/completed |
| `408 Job expired` | Past deadline | Create a new job |
| `429 Rate limited` | Too many RPC calls | Add retry with backoff |
| `InsufficientBudget` | Below minimum | Check `apex.min_budget()` |

---

## Acknowledgments

- **[UMA Protocol](https://uma.xyz/)** — Optimistic Oracle V3 for trustless dispute resolution. Licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE).

## License

MIT License — see [LICENSE](LICENSE) for details.
