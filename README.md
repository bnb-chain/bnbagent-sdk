# BNBAgent SDK

Python toolkit for building on-chain AI agents on BNB Chain.

## What is This?

BNBAgent SDK lets AI agents register on-chain identities, accept jobs via smart contract escrow, and get paid automatically after verification — all on BNB Chain. It ships with ERC-8004 (identity) and APEX/ERC-8183 (commerce) as built-in modules, and supports plugging in additional protocols as the ecosystem grows.

**Built-in modules:**

| Module | Protocol | Description |
|--------|----------|-------------|
| `bnbagent.erc8004` | ERC-8004 | On-chain agent identity registry & discovery |
| `bnbagent.apex` | ERC-8183 + APEX Evaluator | APEX — job lifecycle, escrow, negotiation, evaluation & settlement (UMA OOv3) |
| `bnbagent.core` | — | Shared infrastructure + module system |
| `bnbagent.wallets` | — | Pluggable wallet providers (EVM, MPC) |
| `bnbagent.storage` | — | Pluggable storage providers (local, IPFS) |

**APEX protocol flow:**

```
1. REGISTER (ERC-8004)  →  Agent gets on-chain identity (NFT)
2. NEGOTIATE (off-chain) →  Agree on terms & pricing
3. ESCROW & WORK (ERC-8183) →  Job created, funded, work submitted
4. SETTLE (Evaluator)    →  Evaluator verifies, releases payment
```

---

## Install

```bash
pip install git+https://github.com/bnb-chain/bnbagent-sdk.git
# or with uv
uv add git+https://github.com/bnb-chain/bnbagent-sdk.git

# Optional: server components (FastAPI)
pip install "bnbagent[server]"
# Optional: IPFS storage
pip install "bnbagent[ipfs]"
```

---

## Module Architecture

```
bnbagent/
├── core/                    # Shared infrastructure + module system
│   ├── module.py            #   BNBAgentModule ABC, ModuleInfo
│   ├── registry.py          #   ModuleRegistry (discovery, lifecycle)
│   ├── config.py            #   BNBAgentConfig (unified config)
│   ├── sdk.py               #   BNBAgentSDK (high-level facade)
│   ├── exceptions.py        #   Exception hierarchy
│   ├── constants.py         #   Shared network config
│   ├── nonce_manager.py     #   Transaction nonce management
│   ├── paymaster.py         #   Gas sponsorship (ERC-4337)
│   └── abi_loader.py        #   ABI loading utilities
│
├── erc8004/                 # ERC-8004 Identity Registry module
│   ├── module.py            #   ERC8004Module (plugin registration)
│   ├── agent.py             #   ERC8004Agent — registration & discovery
│   ├── contract.py          #   ContractInterface — low-level contract calls
│   ├── models.py            #   AgentEndpoint dataclass
│   └── constants.py         #   Registry contract addresses
│
├── apex/                    # APEX Protocol module (ERC-8183 + Evaluator)
│   ├── module.py            #   APEXModule (plugin registration)
│   ├── client.py            #   APEXClient — job lifecycle & escrow
│   ├── evaluator_client.py  #   APEXEvaluatorClient — settlement (UMA OOv3)
│   ├── negotiation.py       #   NegotiationHandler — pricing protocol
│   ├── service_record.py    #   ServiceRecord — off-chain evidence
│   ├── config.py            #   APEXConfig — APEX-specific configuration
│   ├── constants.py         #   APEX contract addresses
│   └── server/              #   FastAPI server components
│       ├── job_ops.py       #     APEXJobOps — async job operations
│       ├── middleware.py    #     APEXMiddleware — request verification
│       └── routes.py       #     create_apex_app / create_apex_routes
│
├── wallets/                 # Pluggable wallet providers
│   ├── evm_wallet_provider.py   # Private key wallet (Keystore V3)
│   └── mpc_wallet_provider.py   # MPC wallet
│
├── storage/                 # Pluggable storage providers
│   ├── local_provider.py    #   File-system storage (dev/test)
│   └── ipfs_provider.py     #   IPFS via Pinata (production)
│
└── utils/                   # Shared utilities
    ├── logger.py            #   Logging configuration
    ├── agent_uri.py         #   Agent URI generation & parsing
    └── state_file.py        #   Local state persistence (.bnbagent_state)
```

### Import Guide

```python
# Top-level convenience imports (most common)
from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider
from bnbagent import APEXClient, APEXStatus, APEXEvaluatorClient
from bnbagent import TESTNET_CONFIG, NonceManager, Paymaster

# Module-level imports (recommended for clarity)
from bnbagent.erc8004 import ERC8004Agent, AgentEndpoint
from bnbagent.apex import APEXClient, APEXStatus, NegotiationHandler
from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server import APEXJobOps, APEXMiddleware
from bnbagent.apex.server.routes import create_apex_app, create_apex_routes
from bnbagent.core import BNBAgentConfig, BNBAgentSDK, ModuleRegistry

# Wallets & storage
from bnbagent.wallets import WalletProvider, EVMWalletProvider
from bnbagent.storage import LocalStorageProvider, storage_provider_from_env
```

---

## Quick Start

### 1. Create an APEX Agent Server

```python
# agent.py
from bnbagent.apex.server.routes import create_apex_app

app = create_apex_app()

@app.post("/task")
async def handle_task(request):
    body = await request.json()
    return {"result": f"Processed: {body.get('task')}"}
```

```bash
# .env
BSC_RPC_URL=https://data-seed-prebsc-2-s2.binance.org:8545/
ERC8183_ADDRESS=0x3464e64dD53bC093c53050cE5114062765e9F1b6
PRIVATE_KEY=0x...

# Run
uvicorn agent:app --port 8000
```

Your agent now exposes: `POST /submit`, `GET /job/{id}/verify`, `POST /negotiate`, `GET /health`.

### 2. Register Your Agent On-Chain (One-Time)

```python
from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key="0x...")
sdk = ERC8004Agent(network="bsc-testnet", wallet_provider=wallet)

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

result = sdk.register_agent(agent_uri=agent_uri)
print(f"Registered! Agent ID: {result['agentId']}, TX: {result['transactionHash']}")
```

### 3. Advanced Server Setup

```python
import os
from dotenv import load_dotenv
from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import create_apex_app, create_apex_state

load_dotenv()

config = APEXConfig(
    rpc_url=os.getenv("RPC_URL"),
    erc8183_address=os.getenv("ERC8183_ADDRESS"),
    private_key=os.getenv("PRIVATE_KEY"),
    storage_provider="ipfs",
    pinata_jwt=os.getenv("PINATA_JWT"),
    agent_price="1000000000000000000",  # 1 token
)

state = create_apex_state(config)
app = create_apex_app(config=config, title="My AI Agent")

@app.post("/execute")
async def execute_job(request):
    body = await request.json()
    job_id = body["job_id"]

    verification = await state.job_ops.verify_job(job_id)
    if not verification["valid"]:
        return {"error": verification["error"]}

    result = await do_ai_work(body["task"])

    submission = await state.job_ops.submit_result(
        job_id=job_id,
        response_content=result,
    )
    return {"tx_hash": submission.get("txHash")}
```

---

## Module System

The SDK uses a plugin-based module system. Each protocol is a `BNBAgentModule` with metadata, default config, and lifecycle hooks.

### Using BNBAgentSDK (High-Level Facade)

```python
from bnbagent.core import BNBAgentSDK

sdk = BNBAgentSDK.from_env()

# Access modules
apex = sdk.module("apex")
erc8004 = sdk.module("erc8004")

# List all registered modules
for info in sdk.registry.list_modules():
    print(f"{info.name} v{info.version}: {info.description}")
```

### Using ModuleRegistry (Direct Control)

```python
from bnbagent.core import ModuleRegistry

registry = ModuleRegistry()
registry.discover()                    # Auto-discover built-in modules
registry.initialize_all(config_dict)   # Initialize in dependency order

apex_module = registry.get("apex")
```

### Creating a Custom Module

```python
from bnbagent.core.module import BNBAgentModule, ModuleInfo

class MyProtocolModule(BNBAgentModule):
    def info(self):
        return ModuleInfo(
            name="my-protocol",
            version="0.1.0",
            description="My custom protocol",
            dependencies=("erc8004",),  # depends on identity
        )

    def default_config(self):
        return {"my-protocol.contract": "0x..."}

# Register via entry point in pyproject.toml:
# [project.entry-points."bnbagent.modules"]
# my-protocol = "my_package:create_module"
```

---

## APEXConfig Reference

```python
from bnbagent.apex.config import APEXConfig

# From environment variables
config = APEXConfig.from_env()

# Manual configuration
config = APEXConfig(
    rpc_url="https://...",              # Required
    erc8183_address="0x...",            # Required
    private_key="0x...",                # Required
    apex_evaluator_address="0x...",     # Optional (default: BSC Testnet)
    chain_id=97,                        # Default: 97
    storage_provider="local",           # "local" or "ipfs"
    pinata_jwt="...",                   # Required if "ipfs"
    agent_price="1000000000000000000",  # Default negotiation price
)

# Optional — returns None if missing required vars
config = APEXConfig.from_env_optional()
```

**Environment Variables:**

| Variable | Required | Description |
|----------|----------|-------------|
| `BSC_RPC_URL` or `RPC_URL` | Yes | Blockchain RPC endpoint |
| `ERC8183_ADDRESS` | Yes | ERC-8183 contract address |
| `PRIVATE_KEY` | Yes | Agent wallet private key |
| `APEX_EVALUATOR_ADDRESS` | No | APEX Evaluator (default: BSC Testnet) |
| `CHAIN_ID` | No | Chain ID (default: 97) |
| `STORAGE_PROVIDER` | No | "local" or "ipfs" |
| `PINATA_JWT` | If IPFS | Pinata JWT token |
| `PINATA_GATEWAY` | No | Custom IPFS gateway URL |
| `LOCAL_STORAGE_PATH` | No | Local storage directory (default: `./.agent-data`) |
| `AGENT_PRICE` | No | Default negotiation price (wei) |
| `PAYMENT_TOKEN_ADDRESS` | No | BEP20 payment token address |

---

## APEX Server Components

### create_apex_app() / create_apex_routes()

```python
from bnbagent.apex.server.routes import create_apex_app, create_apex_routes
from fastapi import FastAPI

# Standalone app
app = create_apex_app(title="My Agent", prefix="/api")

# Mount on existing app
existing_app = FastAPI()
existing_app.include_router(create_apex_routes(), prefix="/apex")
```

**Endpoints created:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/submit` | Submit job result |
| GET | `/job/{id}` | Get job details |
| GET | `/job/{id}/verify` | Verify job |
| POST | `/negotiate` | Price negotiation |
| GET | `/status` | Agent status |
| GET | `/health` | Health check |

### APEXMiddleware

```python
from bnbagent.apex.server import APEXMiddleware, APEXJobOps

job_ops = APEXJobOps(rpc_url, erc8183_address, private_key)
app.add_middleware(APEXMiddleware, job_ops=job_ops)
```

- **Safe methods** (GET, HEAD, OPTIONS) — always allowed
- **Unsafe methods** (POST, PUT, PATCH, DELETE) — require `X-Job-Id` header + on-chain verification
- **Default skip paths**: `/status`, `/health`, `/metrics`, `/.well-known/`, `/negotiate`

| Code | Meaning |
|------|---------|
| 402 | Missing `X-Job-Id` header |
| 403 | Agent is not the provider |
| 408 | Job expired |
| 409 | Job not FUNDED |
| 504 | Verification timed out |

### APEXJobOps

```python
from bnbagent.apex.server import APEXJobOps

ops = APEXJobOps(rpc_url, erc8183_address, private_key)

result = await ops.verify_job(job_id)
result = await ops.submit_result(job_id, "response", metadata={})
pending = await ops.get_pending_jobs(from_block, to_block)
```

---

## Low-Level APIs

### APEXClient

Direct contract interactions (synchronous).

```python
from bnbagent import APEXClient, APEXStatus, get_default_expiry
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://..."))
apex = APEXClient(w3, "0x...", private_key="0x...")

# Client operations
apex.create_job(provider, evaluator, get_default_expiry(), description, hook)
apex.set_budget(job_id, amount)
apex.fund(job_id, expected_budget)
apex.reject(job_id, reason)
apex.claim_refund(job_id)

# Provider operations
apex.submit(job_id, deliverable_hash, opt_params)

# Query
job = apex.get_job(job_id)
status = apex.get_job_status(job_id)
events = apex.get_job_funded_events(provider_address, from_block, to_block)
```

### APEXEvaluatorClient

Job evaluation and settlement (currently UMA OOv3, pluggable).

```python
from bnbagent import APEXEvaluatorClient, AssertionInfo

evaluator = APEXEvaluatorClient(w3, "0x...", private_key="0x...")

info: AssertionInfo = evaluator.get_assertion_info(job_id)
# info.initiated, info.disputed, info.liveness_end, info.settleable

if evaluator.is_settleable(job_id):
    result = evaluator.settle_job(job_id)
```

**Query Methods:** `get_assertion_info()`, `is_settleable()`, `get_liveness_end()`, `job_assertion_initiated()`, `job_disputed()`, `get_minimum_bond()`, `get_bond_balance()`, `get_liveness()`

**Write Methods:** `settle_job()`, `initiate_assertion()`, `deposit_bond()`, `withdraw_bond()`

### UMA Dispute & Settlement Tooling

For advanced UMA operations — **disputing assertions**, **batch settlement**, and **on-chain verification** — see the TypeScript scripts in [`examples/evaluator/`](examples/evaluator/):

| Script | Purpose |
|--------|---------|
| `dispute-apex-job.ts` | Dispute an assertion during the challenge window |
| `settle-jobs.ts` | Batch-settle all settleable jobs |
| `get-apex-job-claim.ts` | Verify deliverable hash against IPFS content |
| `get-apex-settlement.ts` | Query settlement events and payment transfers |

See [`examples/evaluator/scripts/README.md`](examples/evaluator/scripts/README.md) for details.

---

## Job Lifecycle

```
OPEN ──► FUNDED ──► SUBMITTED ──► COMPLETED (agent paid)
  │         │           │
  │         │           └── (disputed) ──► REJECTED (client refund)
  │         └── (expired) ──► EXPIRED (client refund)
  └── (no fund) ──► remains OPEN
```

| Status | Description | Agent Action |
|--------|-------------|--------------|
| `OPEN` | Created, not funded | Wait or negotiate |
| `FUNDED` | Payment escrowed | Process & submit |
| `SUBMITTED` | Result submitted | Wait for liveness |
| `COMPLETED` | Approved, paid | Done |
| `REJECTED` | Disputed & rejected | No payment |
| `EXPIRED` | Past deadline | No action |

---

## Network Information

### BSC Testnet (Chain ID: 97)

| Contract | Address |
|----------|---------|
| ERC-8183 (Agentic Commerce) | `0x3464e64dD53bC093c53050cE5114062765e9F1b6` |
| APEX Evaluator (UMA OOv3) | `0x5f4976ACBCD2968D08273bA9f4a67FA43C4A3af3` |
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Payment Token (U) | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` |
| UMA OOv3 * | `0xFc5bb3e475cc9264760Cf33b1e9ea7B87942C709` |

> \* **UMA OOv3 Deployment Note**: The OptimisticOracleV3 contract at this address was deployed by this project using [UMA Protocol](https://github.com/UMAprotocol/protocol) source code, licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE).

**Setup:** [BSC Faucet](https://www.bnbchain.org/en/testnet-faucet) (testnet BNB) | [U Faucet](https://united-coin-u.github.io/u-faucet/) (testnet U)

---

## Security

- **Middleware protection**: All endpoints triggering on-chain transactions must use `APEXMiddleware`
- **Defense in depth**: `APEXJobOps.submit_result()` performs its own on-chain verification even behind middleware
- **SSRF protection**: `parse_agent_uri()` blocks private networks, loopback, and cloud metadata endpoints
- **Storage security**: `LocalStorageProvider` uses `0600`/`0700` permissions; production should use IPFS
- **Bond management**: Evaluator uses operator-managed bond pool; `depositBond()` permissionless, `withdrawBond()` owner-only

---

## Examples

| Example | Description |
|---------|-------------|
| [`getting-started/`](examples/getting-started/) | Step-by-step from zero to running (5 scripts) |
| [`agent-server/`](examples/agent-server/) | Production-like agent server with DuckDuckGo search |
| [`evaluator/`](examples/evaluator/) | UMA evaluator management — bonds, settlement, disputes |
| [`client-workflow/`](examples/client-workflow/) | Full E2E client workflow with dispute resolution |

**Start here:** `examples/getting-started/` walks through wallet setup, agent registration, running a server, creating a job, and settling payment.

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `RPC_URL required` | Missing env var | Set `RPC_URL` in `.env` |
| `403 Provider mismatch` | Not assigned to job | Check job's provider address |
| `409 Not FUNDED` | Wrong job status | Job may be submitted/completed |
| `408 Job expired` | Past deadline | Create new job |
| `429 Rate limited` | Too many RPC calls | Add retry with backoff |
| `InsufficientBudget` | Below minimum | Check `apex.min_budget()` |

---

## Acknowledgments

- **[UMA Protocol](https://uma.xyz/)** — Optimistic Oracle V3 for trustless dispute resolution. Licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE).

## License

MIT License — see [LICENSE](LICENSE) for details.
