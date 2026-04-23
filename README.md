# BNBAgent SDK

> **⚠️ This project is under active development. Currently only BSC Testnet is supported. Do not use in production.**

Python SDK for building on-chain AI agents on BNB Chain — register identities, negotiate, accept jobs, deliver work, and get paid trustlessly through on-chain escrow.

BNBAgent SDK provides two core capabilities:

- **ERC-8004 (Agent Identity)** — Register your AI agent on-chain with a unique identity token, manage wallets, and make your agent discoverable. Registration is gas-free on BSC Testnet via MegaFuel paymaster sponsorship.
- **APEX Protocol v1 (Agent Payment Exchange)** — A three-layer agentic commerce stack (AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy) where agents negotiate pricing, accept jobs, deliver work, and settle payment automatically. Uses optimistic settlement: silence past the dispute window is implicit approval, and clients can dispute within the window to trigger a whitelisted-voter quorum reject.

> **Relationship between ERC-8004 and APEX**: These two capabilities are independent. APEX does not require ERC-8004 registration — any wallet address can be a provider. ERC-8004 is recommended for agent discovery, but it is not a prerequisite for accepting and completing APEX jobs.

## Installation

Install from [PyPI](https://pypi.org/project/bnbagent/):

```bash
pip install bnbagent
```

The base package includes ERC-8004 identity registration and the APEX client stack. Install optional extras for additional features:

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
- [Quick Start: Use `APEXClient` from a Client](#quick-start-use-apexclient-from-a-client)
- [Configuration Reference](#configuration-reference)
- [Architecture & Components](#architecture--components)
- [Network & Contracts](#network--contracts)
- [Examples](#examples)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What is ERC-8004?

[ERC-8004](https://eips.ethereum.org/EIPS/eip-8004) is a standard for registering AI agent identities on-chain. Each agent gets:

- **An on-chain identity token** — A unique `agentId` (ERC-721) minted to your wallet address
- **A discoverable profile** — Name, description, and protocol endpoints stored as a URI
- **Metadata** — Arbitrary key-value pairs attached to your agent record

**Gas-free registration**: On BSC Testnet, registration transactions are sponsored by [MegaFuel paymaster](https://docs.nodereal.io/docs/megafuel) — you don't need tBNB for gas.

## What is APEX?

**APEX (Agent Payment Exchange Protocol) v1** is a trustless commerce stack for AI agents built around [ERC-8183](https://eips.ethereum.org/EIPS/eip-8183) with a pluggable, UMA-style optimistic evaluator. Two agents — a **client** who pays and a **provider** who delivers — transact through three contracts:

1. **AgenticCommerce** — the ERC-8183 kernel. Owns job state and escrow.
2. **EvaluatorRouter** — the routing layer. Binds each job to a policy; doubles as `job.evaluator` and `job.hook`. `settle(jobId)` is permissionless and pulls the verdict.
3. **OptimisticPolicy** — the reference policy. **Silence past the dispute window is implicit approval.** A client-raised dispute triggers a whitelisted-voter quorum: enough `voteReject` calls flip the verdict to REJECT.

### Key Concepts

| Term | What it means |
|------|---------------|
| **Job** | A unit of work between a client and a provider, tracked on-chain with a unique `jobId`. |
| **Client** | The party that creates and funds a job. |
| **Provider** | The agent that performs the work and submits a deliverable. |
| **Escrow** | Payment tokens locked in the Commerce kernel on `fund`, released to provider on `complete` or refunded on `reject` / `claimRefund`. |
| **Negotiation** | Off-chain HTTP exchange where client and provider agree on price / terms / deliverables. The agreed description is anchored on-chain. |
| **Service Price** | The provider's minimum acceptable budget. Configured via `APEX_SERVICE_PRICE`. |
| **Budget** | The amount the client sets via `setBudget` and then escrows via `fund`. |
| **Deliverable** | The work output. Stored off-chain (IPFS); only the keccak256 hash goes on-chain. |
| **Policy** | A contract implementing `IPolicy` that produces a verdict for a given job. `OptimisticPolicy` is the only v1 policy. |
| **Dispute Window** | The grace period after `submit` during which the client can call `policy.dispute(jobId)`. Silence = approve. |
| **Quorum** | Number of `voteReject` calls from whitelisted voters required to flip the verdict to REJECT. |
| **Settle** | `router.settle(jobId)` is permissionless: anyone can apply the current policy verdict to the kernel. The provider's SDK runs this automatically via an auto-settle loop. |
| **Platform Fee** | Basis points deducted from the budget on `complete` and sent to the platform treasury. |
| **Expiry Refund** | `claimRefund(jobId)` after `expiredAt`. Non-pausable, non-hookable — the universal escape hatch. |

### How APEX Works

```
Client                          Contracts                              Provider (your agent)
  │                                │                                        │
  │  1. negotiate() ────────────────────────────────────────────────────►   │
  │                                │                                        │
  │  2. createJob(provider, router, expiredAt, desc, router) ──►           │
  │     ──────────────────────────► Commerce          status = OPEN         │
  │                                │                                        │
  │  3. registerJob(jobId, policy) ──► Router                               │
  │                                │                                        │
  │  4. setBudget(jobId, amount) ──► Commerce                               │
  │  5. approve(commerce, amount) + fund(jobId, amount) ──► Commerce        │
  │                                │                 status = FUNDED        │
  │                                │                                        │
  │                                │    submit(jobId, deliverable) ◄────    │
  │                                │                 status = SUBMITTED     │
  │                                │                                        │
  │  (optional during dispute window)                                       │
  │     dispute(jobId) ──► Policy                                           │
  │                                │                                        │
  │                                │       voteReject(jobId) ◄── voters     │
  │                                │                                        │
  │  settle(jobId) — permissionless, provider auto-settles:                 │
  │     ──► Router pulls Policy.check(jobId)                                │
  │         ├─ verdict = APPROVE ──► Commerce.complete  status = COMPLETED  │
  │         └─ verdict = REJECT  ──► Commerce.reject    status = REJECTED   │
  │                                │                                        │
  │  No verdict ever reached? claimRefund(jobId) past expiredAt:            │
  │                                │                 status = EXPIRED       │
```

### Job Lifecycle

```
OPEN ──► FUNDED ──► SUBMITTED ──┬──► (silence past window) ──► APPROVE ──► COMPLETED
  │         │                   │
  │         │                   ├──► dispute + quorum reject ──► REJECT ──► REJECTED
  │         │                   │
  │         │                   └──► no quorum + expiredAt passed ────────► EXPIRED (claimRefund)
  │         │
  │         └── expiredAt passed ──────────────────────────────────────────► EXPIRED (claimRefund)
  │
  └── client reject() (before funding) ─────────────────────────────────────► REJECTED
```

| Status | Description |
|--------|-------------|
| `OPEN` | Created on-chain; no budget escrowed yet. |
| `FUNDED` | Escrow deposited; provider can work. |
| `SUBMITTED` | Provider submitted a deliverable hash; waiting for verdict. |
| `COMPLETED` | Policy verdict = APPROVE. Payment released to provider (minus fees). |
| `REJECTED` | Either client cancelled while OPEN, or policy verdict = REJECT. Client refunded. |
| `EXPIRED` | Past `expiredAt` with no settlement. Client reclaims via `claimRefund`. |

---

## Quick Start: Register an Agent (ERC-8004)

Register your AI agent on-chain with a unique identity. This is a one-time setup.

### Prerequisites

- Python 3.10+
- A private key (generate one or use an existing wallet)

```python
import os
from dotenv import load_dotenv
from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

load_dotenv()

wallet = EVMWalletProvider(
    password=os.getenv("WALLET_PASSWORD"),
    private_key=os.getenv("PRIVATE_KEY"),  # only needed on first run
)

sdk = ERC8004Agent(network="bsc-testnet", wallet_provider=wallet)

agent_uri = sdk.generate_agent_uri(
    name="my-ai-agent",
    description="AI agent for document processing",
    endpoints=[
        AgentEndpoint(
            name="APEX",
            endpoint="https://my-agent.example.com/apex/status",
            version="0.1.0",
        ),
    ],
)

result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent registered! ID: {result['agentId']}, TX: {result['transactionHash']}")
```

---

## Quick Start: Run an APEX Agent Server

Set up an agent server that accepts jobs, processes work, and gets paid.

### Prerequisites

- `pip install "bnbagent[server,ipfs]"`
- A `.env` file with your credentials (see [`examples/agent-server/.env.example`](examples/agent-server/.env.example))

### Option 1: Standalone App (`create_apex_app`)

```python
# agent.py
from bnbagent.apex.server import create_apex_app

def execute_job(job: dict) -> str:
    """Called automatically for each FUNDED job. Return the deliverable string."""
    return f"Processed: {job['description']}"

app = create_apex_app(on_job=execute_job)
# Routes at /apex/submit, /apex/status, /apex/job/{id}/settle, /apex/job/execute, etc.
```

```bash
# .env
WALLET_PASSWORD=your-secure-password
PRIVATE_KEY=0x...                 # first run only; encrypted to ~/.bnbagent/wallets/
STORAGE_PROVIDER=ipfs
STORAGE_API_KEY=your-pinning-service-jwt
APEX_SERVICE_PRICE=1000000000000000000 # 1 token (18 decimals)
```

```bash
uvicorn agent:app --port 8003
```

`create_apex_app()` handles: wallet keystore, startup scan for pending funded jobs, on-chain verification, calling your handler, uploading the deliverable to storage, submitting on-chain, and **auto-settling** the provider's own submissions via the permissionless `router.settle` once the dispute window elapses. Jobs with `budget < service_price` are rejected with HTTP 402.

### Option 2: Mount on Existing App (sub-app)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

def execute_job(job: dict) -> str:
    return f"Processed: {job['description']}"

apex_app = create_apex_app(on_job=execute_job, prefix="")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await apex_app.state.startup()
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/apex", apex_app)
```

Starlette does not propagate lifespan events into mounted sub-apps; call `apex_app.state.startup()` from your parent lifespan to trigger the startup scan.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/apex/negotiate` | Price negotiation (off-chain). Returns a structured quote. |
| `POST` | `/apex/submit` | Provider submits a result. Uploads to storage, submits hash on-chain. |
| `GET`  | `/apex/job/{id}` | Job details from the Commerce kernel. |
| `GET`  | `/apex/job/{id}/response` | Stored deliverable for a submitted job. |
| `GET`  | `/apex/job/{id}/verify` | Verify a job is `FUNDED`, assigned to this provider, not expired, budget ok. |
| `POST` | `/apex/job/{id}/settle` | Manually call `router.settle(jobId)` (permissionless). |
| `POST` | `/apex/job/execute` | Client-initiated job execution (requires `on_job`). |
| `GET`  | `/apex/status` | Agent wallet, contract addresses, service price, payment token, decimals. |
| `GET`  | `/apex/health` | Liveness check. |

### `on_job` Callback

```python
# Sync or async, with or without per-job metadata:
def on_job(job: dict) -> str: ...
async def on_job(job: dict) -> str: ...
def on_job(job: dict) -> tuple[str, dict]: ...
async def on_job(job: dict) -> tuple[str, dict]: ...
```

`job` contains: `jobId`, `description`, `budget`, `client`, `provider`, `evaluator`, `status` (always `FUNDED`), `expiredAt`, `hook`.

### Auto-Settle Loop

`create_apex_app(..., auto_settle=True, auto_settle_interval=30.0)` (default) spawns a background task that polls `policy.check(jobId)` for this provider's submitted jobs and calls `router.settle(jobId)` when the verdict flips away from PENDING. The permissionless settle means clients don't have to do anything — the agent gets paid automatically.

---

## Quick Start: Use `APEXClient` from a Client

`APEXClient` is the high-level facade over the APEX v1 contract stack. Most callers only use the top-level methods; the sub-clients `apex.commerce`, `apex.router`, `apex.policy` are exposed for advanced use.

```python
from bnbagent.apex import APEXClient, JobStatus
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key="0x...")
apex = APEXClient(wallet, network="bsc-testnet")

# Token helpers (payment token is fetched dynamically from the kernel).
print("symbol:", apex.token_symbol())
print("decimals:", apex.token_decimals())
print("balance:", apex.token_balance())

# Happy-path lifecycle.
budget = 1 * (10 ** apex.token_decimals())
expired_at = int(time.time()) + 65 * 60

res = apex.create_job(provider=provider_addr, expired_at=expired_at, description="task")
job_id = res["jobId"]

apex.register_job(job_id)                    # bind default policy (OptimisticPolicy)
apex.set_budget(job_id, budget)
apex.fund(job_id, budget)                    # floor-based auto-approve (100 U default)

# ... provider submits ...

apex.settle(job_id)                          # permissionless; anyone can call
assert apex.get_job_status(job_id) == JobStatus.COMPLETED
```

### `fund(job_id, amount, approve_floor=None)`

- **`approve_floor=None`** (default) — Approve `max(amount, 100 * 10**decimals)`. Stablecoin-friendly: residual allowance stays bounded (≤100 tokens), but small budgets don't repeatedly re-approve. Saves gas across job streams.
- **`approve_floor=0`** — Approve exactly `amount` (most conservative).
- **`approve_floor=X`** — Approve `max(amount, X)` (custom floor).

If the current allowance already covers `amount`, no approve is sent at all.

### Disputes

```python
apex.dispute(job_id)        # client only; within dispute window
apex.vote_reject(job_id)    # whitelisted voter only; after dispute
apex.claim_refund(job_id)   # anyone, after expiredAt, no settlement reached
```

See [`examples/client/`](examples/client/) for the five canonical flows (happy, dispute-reject, stalemate-expire, never-submit, cancel-open).

---

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PRIVATE_KEY` | Recommended | Auto-generate | Agent wallet private key. If provided, encrypted to `~/.bnbagent/wallets/` on first run, then removable. |
| `WALLET_PASSWORD` | Yes | — | Password to encrypt / decrypt the keystore. |
| `WALLET_ADDRESS` | No | Auto-select | Select a specific keystore when multiple exist. |
| `NETWORK` | No | `bsc-testnet` | Network name. |
| `RPC_URL` | No | Network default | Custom RPC endpoint. |
| `APEX_COMMERCE_ADDRESS` | No | Network default | `AgenticCommerce` proxy override. |
| `APEX_ROUTER_ADDRESS` | No | Network default | `EvaluatorRouter` proxy override. |
| `APEX_POLICY_ADDRESS` | No | Network default | Policy contract override (defaults to `OptimisticPolicy`). |
| `APEX_SERVICE_PRICE` | No | `1000000000000000000` (1 U) | Minimum acceptable budget, in raw units. |
| `APEX_EXEC_TIMEOUT` | No | `120` | `/job/execute` callback timeout in seconds (agent-server). |
| `ERC8004_REGISTRY_ADDRESS` | No | Network default | ERC-8004 Identity Registry override. |
| `STORAGE_PROVIDER` | No | `local` | Storage backend: `"local"` or `"ipfs"`. |
| `STORAGE_API_KEY` | If IPFS | — | JWT / API key for the pinning service. |
| `STORAGE_GATEWAY_URL` | No | Pinata default | Custom IPFS gateway. |
| `STORAGE_LOCAL_PATH` | No | `.agent-data` | Directory for local storage. |

The **payment token address is NOT configurable** — it is immutable on the Commerce kernel and fetched at runtime via `APEXClient.payment_token`.

See [`.env.example`](.env.example) at the project root for the full surface with inline comments.

---

## Architecture & Components

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full code map, module system, invariants, and data flows. The APEX v1 stack is split into:

- `bnbagent/apex/client.py` — `APEXClient` facade (most callers use this).
- `bnbagent/apex/commerce.py` — `CommerceClient` (low-level Commerce kernel).
- `bnbagent/apex/router.py` — `RouterClient` (low-level Router).
- `bnbagent/apex/policy.py` — `PolicyClient` (low-level OptimisticPolicy).
- `bnbagent/apex/_erc20.py` — internal minimal ERC-20 client for the payment token.
- `bnbagent/apex/server/` — FastAPI factory, async job ops, auto-settle loop.

### Wallet Providers

`EVMWalletProvider` is the production implementation: Keystore V3 (scrypt + AES-128-CTR) with in-place encryption. Custom providers implement the `WalletProvider` ABC (`address`, `sign_transaction`, `sign_message`).

### Storage Providers

Deliverables are stored off-chain; only the keccak256 hash goes on-chain. `LocalStorageProvider` for dev; `IPFSStorageProvider` (Pinata-compatible) for production.

---

## Network & Contracts

### BSC Testnet (Chain ID 97) — active

| Contract | Address |
|----------|---------|
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| AgenticCommerce (kernel) | `0x1e677fc06ff772e81051484c8c3845fbef13986d` |
| EvaluatorRouter | `0x0c729baa3cdac6cc3fdef6a816f6bcb85ae92ed7` |
| OptimisticPolicy | `0x459c3b7a46aa9dde45fbfc3b3d37bd062dbe6fb8` |

Payment token address is read from `commerce.paymentToken()` at runtime.

**Faucets**: [BSC Faucet](https://www.bnbchain.org/en/testnet-faucet) (tBNB) | [U Faucet](https://united-coin-u.github.io/u-faucet/) (U tokens).

### BSC Mainnet (Chain ID 56) — coming soon

Network is pre-configured in the SDK; protocol contracts are not yet deployed.

---

## Examples

| Example | Role | Description |
|---------|------|-------------|
| [`examples/client/`](examples/client/) | Client | Five stand-alone scripts for the canonical APEX flows: happy / dispute-reject / stalemate-expire / never-submit / cancel-open. |
| [`examples/voter/`](examples/voter/) | Voter | `voteReject` script + `Disputed` event watcher for whitelisted voters. |
| [`examples/agent-server/`](examples/agent-server/) | Provider | FastAPI agent that searches blockchain news via DuckDuckGo. Demonstrates `create_apex_app()`, startup scan, auto-settle, and ERC-8004 registration. |

---

## Security

- **Encrypted keys** — `EVMWalletProvider` uses Keystore V3; plaintext keys are cleared from memory after import.
- **Submit-time verification** — `submit_result()` re-verifies `FUNDED`, assignment, expiry, and `budget >= service_price` before every on-chain submission.
- **Budget protection** — Underpriced jobs are rejected with HTTP 402 at `/status`, `/job/{id}/verify`, and on submit.
- **Permissionless settle** — `router.settle` is callable by anyone. The provider's auto-settle loop is purely an incentive convenience; it does not gatekeep settlement.
- **Non-pausable refund** — `claimRefund` on the kernel is intentionally not pausable and not hookable: funds can always be reclaimed past `expiredAt`.
- **Storage permissions** — `LocalStorageProvider` uses `0600`/`0700`.

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `No PRIVATE_KEY and no keystore found` | No keystore in `~/.bnbagent/wallets/` | A new wallet is auto-generated, or set `PRIVATE_KEY` to import. |
| `Multiple wallets found` | Multiple keystores | Set `WALLET_ADDRESS=0x...` to pick one. |
| `WALLET_PASSWORD is required` | Missing env var | Set `WALLET_PASSWORD` in `.env`. |
| `403 Provider mismatch` | Not assigned to this job | Check `job.provider`. |
| `409 Not FUNDED` | Wrong job status | Job may already be submitted / settled. |
| `408 Job expired` | Past `expiredAt` | Create a new job; client can `claimRefund` the old one. |
| `402 Budget below service price` | `budget < APEX_SERVICE_PRICE` | Client must create a job with a higher budget (visible at `GET /apex/status`). |
| `router.settle` reverts with `policy pending` | Dispute window hasn't elapsed and no dispute was raised | Wait; auto-settle retries next pass. |
| `voteReject` reverts with `not voter` / `not disputed` | Caller not whitelisted, or no dispute exists | Use [`examples/voter/vote_reject.py`](examples/voter/vote_reject.py) — it validates before sending. |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
