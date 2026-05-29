# BNBAgent SDK

Python SDK for building on-chain AI agents on BNB Chain — register identities, negotiate, accept jobs, deliver work, and get paid trustlessly through on-chain escrow.

BNBAgent SDK provides two core capabilities:

- **ERC-8004 (Agent Identity)** — Register your AI agent on-chain with a unique identity token, manage wallets, and make your agent discoverable. Registration is gas-free on BSC Testnet via MegaFuel paymaster sponsorship.
- **ERC-8183 Protocol (Agentic Commerce)** — A three-layer agentic commerce stack (AgenticCommerce kernel + EvaluatorRouter + OptimisticPolicy) where agents negotiate pricing, accept jobs, deliver work, and settle payment automatically. Uses optimistic settlement: silence past the dispute window is implicit approval, and clients can dispute within the window to trigger a whitelisted-voter quorum reject.

> **Relationship between ERC-8004 and ERC-8183**: These two capabilities are independent. ERC-8183 provider. ERC-8004 is recommended for agent discovery, but it is not a prerequisite for accepting and completing ERC-8183 jobs.

> ⚠️ This project is under active development and may introduce breaking changes. Please use it at your own risk.

## Installation

Install from [PyPI](https://pypi.org/project/bnbagent/):

```bash
pip install bnbagent
```

The base package includes ERC-8004 identity registration and the ERC-8183 client stack. Install optional extras for additional features:

```bash
# ERC-8183 server components (FastAPI + Uvicorn)
pip install "bnbagent[server]"

# IPFS storage (HTTP pinning service backend, e.g. Pinata)
pip install "bnbagent[ipfs]"

# All extras
pip install "bnbagent[server,ipfs]"
```

## Table of Contents

- [What is ERC-8004?](#what-is-erc-8004)
- [What is ERC-8183?](#what-is-erc-8183)
- [Quick Start: Register an Agent (ERC-8004)](#quick-start-register-an-agent-erc-8004)
- [Quick Start: Run an ERC-8183 Agent Server](#quick-start-run-an-erc-8183-agent-server)
- [Quick Start: Use `ERC8183Client` from a Client](#quick-start-use-erc8183client-from-a-client)
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

## What is ERC-8183?

**ERC-8183 (ERC-8183 Protocol) v1** is a trustless commerce stack for AI agents built around [ERC-8183](https://eips.ethereum.org/EIPS/eip-8183) with a pluggable, UMA-style optimistic evaluator. Two agents — a **client** who pays and a **provider** who delivers — transact through three contracts:

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
| **Service Price** | The provider's minimum acceptable budget. Configured via `ERC8183_SERVICE_PRICE`. |
| **Budget** | The amount the client sets via `setBudget` and then escrows via `fund`. |
| **Deliverable** | The work output. Stored off-chain via a `StorageProvider` (local file, IPFS, or custom backend); only the keccak256 hash goes on-chain. |
| **Policy** | A contract implementing `IPolicy` that produces a verdict for a given job. `OptimisticPolicy` is the only v1 policy. |
| **Dispute Window** | The grace period after `submit` during which the client can call `policy.dispute(jobId)`. Silence = approve. |
| **Quorum** | Number of `voteReject` calls from whitelisted voters required to flip the verdict to REJECT. |
| **Settle** | `router.settle(jobId)` is permissionless: anyone can apply the current policy verdict to the kernel. Operators are expected to run a separate settle script. |
| **Platform Fee** | Basis points deducted from the budget on `complete` and sent to the platform treasury. |
| **Expiry Refund** | `claimRefund(jobId)` after `expiredAt`. Non-pausable, non-hookable — the universal escape hatch. |

### How ERC-8183 Works

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
  │  settle(jobId) — permissionless, anyone can call:                       │
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
            name="ERC-8183",
            endpoint="https://my-agent.example.com/erc8183/status",
            version="0.1.0",
        ),
    ],
)

result = sdk.register_agent(agent_uri=agent_uri)
print(f"Agent registered! ID: {result['agentId']}, TX: {result['transactionHash']}")
```

---

## Quick Start: Run an ERC-8183 Agent Server

Set up an agent server that accepts jobs, processes work, and gets paid.

### Prerequisites

- `pip install "bnbagent[server,ipfs]"`
- A `.env` file with your credentials (see [`examples/agent-server/.env.example`](examples/agent-server/.env.example))

### Option 1: Standalone App (`create_erc8183_app`)

```python
# agent.py
from bnbagent.erc8183.server import create_erc8183_app

def execute_job(job: dict) -> str:
    """Called automatically for each FUNDED job. Return the deliverable string."""
    return f"Processed: {job['description']}"

app = create_erc8183_app(on_job=execute_job)
# Routes at /erc8183/negotiate, /erc8183/status, /erc8183/job/{id}, etc.
```

```bash
# .env
WALLET_PASSWORD=your-secure-password
PRIVATE_KEY=0x...                                # first run only; encrypted to ~/.bnbagent/wallets/
ERC8183_AGENT_URL=http://localhost:8003/erc8183  # required for LocalStorageProvider (default)
ERC8183_SERVICE_PRICE=1000000000000000000        # 1 token (18 decimals)
# To use IPFS instead, swap to IPFSStorageProvider in your service code and set:
# STORAGE_API_KEY=your-pinning-service-jwt
# Optional knobs (see env-var table below for full reference):
# ERC8183_FUNDED_POLL_INTERVAL=30      # default poll cadence (s)
# ERC8183_NEGOTIATE_RATE_LIMIT=120     # /negotiate per-IP request budget
# ERC8183_NEGOTIATE_RATE_WINDOW=60     # rate-limit window (s)
# ERC8183_MAX_RESPONSE_BYTES=5242880   # response_content cap (5 MB)
# ERC8183_MAX_METADATA_BYTES=262144    # metadata cap (256 KB)
```

```bash
uvicorn agent:app --port 8003
```

`create_erc8183_app()` handles: wallet keystore, periodic on-chain poll for newly FUNDED jobs assigned to this provider, on-chain verification, calling your handler, uploading the deliverable to storage, and submitting on-chain. Jobs with `budget < service_price` are rejected with HTTP 402. Settle is permissionless — run a separate operator script to call `router.settle(jobId)` once the dispute window elapses.

### Option 2: Mount on Existing App (sub-app)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.erc8183.server import create_erc8183_app

def execute_job(job: dict) -> str:
    return f"Processed: {job['description']}"

erc8183_app = create_erc8183_app(on_job=execute_job, prefix="")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await erc8183_app.state.startup()
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/erc8183", erc8183_app)
```

Starlette does not propagate lifespan events into mounted sub-apps; call `erc8183_app.state.startup()` from your parent lifespan to launch the funded-job poll loop.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/erc8183/negotiate` | Price negotiation (off-chain). Returns a structured quote. Rate-limited per client IP. |
| `GET`  | `/erc8183/job/{id}` | Job details from the Commerce kernel. |
| `GET`  | `/erc8183/job/{id}/response` | Stored deliverable for a submitted job. |
| `GET`  | `/erc8183/job/{id}/verify` | Verify a job is `FUNDED`, assigned to this provider, not expired, budget ok. |
| `GET`  | `/erc8183/status` | Agent wallet, contract addresses, service price, payment token, decimals. |
| `GET`  | `/erc8183/health` | Liveness check. |

### `on_job` Callback

```python
# Sync or async, with or without per-job metadata:
def on_job(job: dict) -> str: ...
async def on_job(job: dict) -> str: ...
def on_job(job: dict) -> tuple[str, dict]: ...
async def on_job(job: dict) -> tuple[str, dict]: ...
```

`job` contains: `jobId`, `description`, `budget`, `client`, `provider`, `evaluator`, `status` (always `FUNDED`), `expiredAt`, `hook`.

### Settle

`router.settle(jobId)` is permissionless — any party can finalise a submitted job once its dispute window elapses. The SDK does not run an in-server settle loop; operators are expected to run a separate script that polls verdicts and calls `ERC8183Client.settle(jobId)` when ready.

---

## Quick Start: Use `ERC8183Client` from a Client

`ERC8183Client` is the high-level facade over the ERC-8183 contract stack. Most callers only use the top-level methods; the sub-clients `erc8183.commerce`, `erc8183.router`, `erc8183.policy` are exposed for advanced use.

```python
from bnbagent.erc8183 import ERC8183Client, JobStatus
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key="0x...")
erc8183 = ERC8183Client(wallet, network="bsc-testnet")

# Token helpers (payment token is fetched dynamically from the kernel).
print("symbol:", erc8183.token_symbol())
print("decimals:", erc8183.token_decimals())
print("balance:", erc8183.token_balance())

# Happy-path lifecycle.
budget = 1 * (10 ** erc8183.token_decimals())
expired_at = int(time.time()) + 65 * 60

res = erc8183.create_job(provider=provider_addr, expired_at=expired_at, description="task")
job_id = res["jobId"]

erc8183.register_job(job_id)                    # bind default policy (OptimisticPolicy)
erc8183.set_budget(job_id, budget)
erc8183.fund(job_id, budget)                    # floor-based auto-approve (100 U default)

# ... provider submits ...

erc8183.settle(job_id)                          # permissionless; anyone can call
assert erc8183.get_job_status(job_id) == JobStatus.COMPLETED
```

### `fund(job_id, amount, approve_floor=None)`

- **`approve_floor=None`** (default) — Approve `max(amount, 100 * 10**decimals)`. Stablecoin-friendly: residual allowance stays bounded (≤100 tokens), but small budgets don't repeatedly re-approve. Saves gas across job streams.
- **`approve_floor=0`** — Approve exactly `amount` (most conservative).
- **`approve_floor=X`** — Approve `max(amount, X)` (custom floor).

If the current allowance already covers `amount`, no approve is sent at all.

### Disputes

```python
erc8183.dispute(job_id)        # client only; within dispute window
erc8183.vote_reject(job_id)    # whitelisted voter only; after dispute
erc8183.claim_refund(job_id)   # anyone, after expiredAt, no settlement reached
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
| `ERC8183_COMMERCE_ADDRESS` | No | Network default | `AgenticCommerce` proxy override. |
| `ERC8183_ROUTER_ADDRESS` | No | Network default | `EvaluatorRouter` proxy override. |
| `ERC8183_POLICY_ADDRESS` | No | Network default | Policy contract override (defaults to `OptimisticPolicy`). |
| `ERC8183_AGENT_URL` | If LocalStorageProvider | — | Agent's public base URL including `/erc8183`. Required when storage returns `file://` URLs; the SDK rewrites them to `{ERC8183_AGENT_URL}/job/{id}/response`. |
| `ERC8183_SERVICE_PRICE` | No | `1000000000000000000` (1 U) | Minimum acceptable budget, in raw units. |
| `ERC8183_FUNDED_POLL_INTERVAL` | No | `30` | Seconds between funded-job poll passes (agent-server). |
| `ERC8183_NEGOTIATE_RATE_LIMIT` | No | `120` | Max `/negotiate` requests per window per client IP. |
| `ERC8183_NEGOTIATE_RATE_WINDOW` | No | `60` | Sliding-window length for `/negotiate` rate limit, in seconds. |
| `ERC8183_MAX_RESPONSE_BYTES` | No | `5242880` (5 MB) | Cap on `response_content` size in `submit_result`. |
| `ERC8183_MAX_METADATA_BYTES` | No | `262144` (256 KB) | Cap on serialised metadata size in `submit_result`. |
| `ERC8004_REGISTRY_ADDRESS` | No | Network default | ERC-8004 Identity Registry override. |
| `STORAGE_API_KEY` | If IPFSStorageProvider | — | JWT / API key for the pinning service. |
| `STORAGE_GATEWAY_URL` | No | Pinata default | Custom IPFS gateway. |
| `STORAGE_LOCAL_PATH` | No | `.agent-data` | Directory for local storage. |

The **payment token address is NOT configurable** — it is immutable on the Commerce kernel and fetched at runtime via `ERC8183Client.payment_token`.

See [`.env.example`](.env.example) at the project root for the full surface with inline comments.

---

## Architecture & Components

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full code map, module system, invariants, and data flows. The ERC-8183 stack is split into:

- `bnbagent/erc8183/client.py` — `ERC8183Client` facade (most callers use this).
- `bnbagent/erc8183/commerce.py` — `CommerceClient` (low-level Commerce kernel).
- `bnbagent/erc8183/router.py` — `RouterClient` (low-level Router).
- `bnbagent/erc8183/policy.py` — `PolicyClient` (low-level OptimisticPolicy).
- `bnbagent/erc20/client.py` — `MinimalERC20Client` — payment-token helpers (decimals/balance/approve).
- `bnbagent/erc8183/server/` — FastAPI factory and async job ops with funded-job poll loop.

### Wallet Providers

Transaction signing is abstracted behind the `WalletProvider` ABC (`address`, `sign_transaction`, `sign_message`). All SDK clients and configs accept any `WalletProvider` instance — backends are pluggable without touching protocol code.

**Built-in: `EVMWalletProvider`**
- Keystore V3 encryption (scrypt + AES-128-CTR), interoperable with MetaMask / Geth.
- Persistent mode (`persist=True`, default) — keystore at `~/.bnbagent/wallets/`, auto-loads on subsequent runs; generates a new wallet if no key is supplied.
- In-memory mode (`persist=False`) — no disk I/O; used internally when configs auto-wrap a `private_key` + `wallet_password` pair.
- Auto-wrap — `BNBAgentConfig`/`ERC8183Config` accept `private_key=` directly and wrap it into `EVMWalletProvider(persist=False)` in `__post_init__`, immediately zeroing the plaintext field.
- Keystores written with `0o600` permissions (directory `0o700`).

**Extensibility** — subclass `WalletProvider` for HSMs, hardware wallets, multisig, MPC, or remote KMS backends. Inject via `wallet_provider=` on any config or client. `MPCWalletProvider` ships as a stub placeholder.

### Storage Providers

Deliverables live off-chain; only the `keccak256` hash is anchored on-chain. The `StorageProvider` ABC (`upload`, `download`, `exists`) is async and pluggable.

**Built-in providers** (default: `LocalStorageProvider`):
- `LocalStorageProvider` — JSON written to `STORAGE_LOCAL_PATH` (default `.agent-data/`); returns `file://` URLs that the SDK rewrites to `{ERC8183_AGENT_URL}/job/{id}/response` and serves via the agent's own ERC-8183 endpoint. Requires `ERC8183_AGENT_URL`.
- `IPFSStorageProvider` — JSON pinned via an HTTP pinning service (Pinata-compatible); returns `ipfs://CID` URLs resolved through the configured gateway. Requires `STORAGE_API_KEY`.

The choice is made in code (e.g. `examples/agent-server/src/service.py`); there is no `STORAGE_PROVIDER` env var.

**Extensibility** — subclass `StorageProvider` for S3, Arweave, database, or proprietary backends. Inject via `storage=` on `ERC8183Config`.

---

## Network & Contracts

### BSC Testnet (Chain ID 97) — active

| Contract | Address |
|----------|---------|
| Identity Registry (ERC-8004) | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| AgenticCommerce (APEX) | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` |
| EvaluatorRouter | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| OptimisticPolicy | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |

Payment token address is read from `commerce.paymentToken()` at runtime.

**Faucets**: [BSC Faucet](https://www.bnbchain.org/en/testnet-faucet) (tBNB) | [U Faucet](https://united-coin-u.github.io/u-faucet/) (U tokens).

### BSC Mainnet (Chain ID 56) — active

| Contract | Address |
|----------|---------|
| Identity Registry (ERC-8004) | `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` |
| AgenticCommerce (APEX) | `0xea4daa3100a767e86fded867729ae7446476eba6` |
| EvaluatorRouter | `0x51895229e12f9876011789b04f8698af06ccd6da` |
| OptimisticPolicy | `0x9c01845705b3078aa2e8cff7520a6376fd766de5` |

---

## Examples

| Example | Role | Description |
|---------|------|-------------|
| [`examples/client/`](examples/client/) | Client | Five stand-alone scripts for the canonical ERC-8183 flows: happy / dispute-reject / stalemate-expire / never-submit / cancel-open. |
| [`examples/voter/`](examples/voter/) | Voter | `voteReject` script + `Disputed` event watcher for whitelisted voters. |
| [`examples/agent-server/`](examples/agent-server/) | Provider | FastAPI agent that searches blockchain news via DuckDuckGo. Demonstrates `create_erc8183_app()`, the funded-job poll loop, and ERC-8004 registration. |

---

## Security

### Wallet & key handling

- **Encrypted keys** — `EVMWalletProvider` uses Keystore V3; plaintext keys are cleared from memory after import.
- **Submit-time verification** — `submit_result()` re-verifies `FUNDED`, assignment, expiry, and `budget >= service_price` before every on-chain submission.
- **Budget protection** — Underpriced jobs are rejected with HTTP 402 at `/status`, `/job/{id}/verify`, and at submit time inside `submit_result()`.
- **Permissionless settle** — `router.settle` is callable by anyone. The SDK does not gatekeep settlement; operators run their own settle script when ready.
- **Non-pausable refund** — `claimRefund` on the kernel is intentionally not pausable and not hookable: funds can always be reclaimed past `expiredAt`.
- **Storage permissions** — `LocalStorageProvider` uses `0600`/`0700`.

### EIP-712 typed-data signing (`SigningPolicy`)

`EVMWalletProvider.sign_typed_data` is **policy-gated by default**. Without
explicit configuration, the wallet only accepts EIP-3009
`TransferWithAuthorization` / `ReceiveWithAuthorization` against the registered
U-token deployments (BSC mainnet/testnet). All Permit variants
(ERC-2612 `Permit`, Permit2 `PermitSingle`/`PermitBatch`) are denylisted —
even if your own code mistakenly allowlists them, the denylist wins.

The threat: U token (and most ERC-20s) support EIP-2612 `Permit` on-chain.
Without `SigningPolicy`, an LLM agent receiving a 402 challenge from a
malicious server could be talked into signing a Permit that grants unbounded
allowance, draining the wallet over time. The default policy refuses
unconditionally; you opt in explicitly when you know what you're signing.

**Canonical example — direct SDK usage:**

```python
from bnbagent import EVMWalletProvider, X402Signer
from bnbagent.networks import get_address, BSC_MAINNET_CHAIN_ID

U = get_address(BSC_MAINNET_CHAIN_ID).payment_token

# Strict default applied automatically — zero config needed for U-token TWA.
wallet = EVMWalletProvider(password=os.environ["WALLET_PASSWORD"])

# Pass a scoped signer (not the wallet) to your @tool functions:
signer = X402Signer(
    wallet,
    max_value_per_call={U: 1_000_000},   # 1 USDC equivalent
    session_budget={U: 50_000_000},      # 50 USDC across this session
)

def pay_for_resource(challenge: dict, expected_to: str) -> dict:
    return signer.sign_payment(
        domain=challenge["domain"],
        types=challenge["types"],
        message=challenge["message"],
        expected_to=expected_to,   # caller MUST commit to the payee
    )
```

`X402Signer` enforces (a) byte-equal `expected_to == message['to']`
(case-insensitive), (b) `message['from'] == wallet.address` (so a tampered
challenge cannot authorize a payment "from" another account or burn the
session budget on a doomed sign), (c) per-call `max_value`, (d) cumulative
session budget. `expected_to` MUST come from a source independent of the 402
response (config / on-chain registry) — never from the challenge body itself.
The underlying `SigningPolicy` simultaneously enforces (chain_id,
verifyingContract) allowlist, primary-type allowlist/denylist, and
validity-window bounds (default ≤ 600s window / ≤ 900s future).

**Extending the policy for custom contracts:**

```python
from bnbagent import EVMWalletProvider, SigningPolicy
from bnbagent.networks import BSC_MAINNET_CHAIN_ID

extended = SigningPolicy.strict_default().extend(
    domain_allowlist={(BSC_MAINNET_CHAIN_ID, "0xMyCustomVerifyingContract")},
)
wallet = EVMWalletProvider(
    password=os.environ["WALLET_PASSWORD"],
    signing_policy=extended,
)
```

**Capability model:** registered agent tool functions must never close over
a raw `WalletProvider` — they should receive an `X402Signer` (or any other
scoped wrapper) instead.

**Tests-only escape:** `SigningPolicy.permissive()` disables all gates and
logs a WARNING; `EVMWalletProvider._DANGEROUS_sign_typed_data_no_policy()`
bypasses the gate per-call and logs the caller's filename+line. Both are
audit-friendly; production / agent-reachable code MUST NOT call them.

**Inspecting the current policy** at runtime:

```python
wallet = EVMWalletProvider(password=...)
print(wallet.signing_policy)
# SigningPolicy(
#   domain_allowlist (2 entries):
#     - chain_id=56 verifyingContract=0xcE24439F2D9C6a2289F741120FE202248B666666
#     - chain_id=97 verifyingContract=0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565
#   primary_type_allowlist=['ReceiveWithAuthorization', 'TransferWithAuthorization']
#   primary_type_denylist=['Permit', 'PermitBatch', 'PermitSingle']
#   validity: window<=600s, future<=900s, required_for=[...]
#   allow_unknown_domain=False
# )
```

`SigningPolicy.to_dict()` / `from_dict()` round-trip the policy through
plain dicts (JSON / TOML-friendly) so downstream tools (CLIs, deploy
manifests) can store and reload configurations declaratively.

### Decision tree — "do I need to configure anything?"

```
What are you signing?
│
├── EIP-3009 TransferWithAuthorization / ReceiveWithAuthorization
│   against U-token on BSC mainnet (56) or testnet (97)
│   → ✅ zero config — strict_default() already allows it
│
├── Same EIP-3009 type but a different token / chain
│   (e.g. USDC on Ethereum mainnet)
│   → 🟡 extend domain_allowlist with (chain_id, token_address)
│
├── A custom typed-data primary type
│   (e.g. "MyOrder" / "BondQuote" / "Auction")
│   → 🟡 extend primary_type_allowlist with the type name
│      AND extend domain_allowlist with the verifying contract
│
├── EIP-2612 Permit  /  Permit2 PermitSingle/PermitBatch
│   (unbounded allowance grants)
│   → ❌ refused unconditionally (denylist takes precedence)
│      Don't sign these in agent flows.
│
├── Permit2 PermitTransferFrom / PermitBatchTransferFrom
│   (single-use signature transfer — safer subset)
│   → 🟡 opt in by extending primary_type_allowlist;
│      witness validation stays caller-side unless / until the x402
│      ecosystem standardises around Permit2
│
└── A longer validity window (e.g. 30-minute authorizations)
    → 🟡 extend max_validity_window_seconds=1800
```

| Scenario | Extension snippet |
|---|---|
| Add a custom token on chain 56 | `extend(domain_allowlist={(56, "0xMyToken")})` |
| Add a custom primary type "MyOrder" on chain 56 / contract X | `extend(domain_allowlist={(56, X)}, primary_type_allowlist={"MyOrder"})` |
| Allow Ethereum-mainnet USDC | `extend(domain_allowlist={(1, "0xA0b8...eB48")})` |
| Opt into Permit2 SignatureTransfer | `extend(primary_type_allowlist={"PermitTransferFrom"})` |
| Widen validity to 30 min | `extend(max_validity_window_seconds=1800)` |

Examples: see `examples/security_e2e.py` (signing + recovery loop, 6 assertions)
and `examples/x402_buyer_demo.py` (complete buyer flow with mock 402 server).

Full design rationale and threat model: see ADR #30 in the
[bnbchain-studio](https://github.com/bnb-chain/bnbchain-studio) repo
(`docs/decisions.md`).

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
| `402 Budget below service price` | `budget < ERC8183_SERVICE_PRICE` | Client must create a job with a higher budget (visible at `GET /erc8183/status`). |
| `router.settle` reverts with `policy pending` | Dispute window hasn't elapsed and no dispute was raised | Wait until `policy.check(jobId)` returns a non-PENDING verdict, then retry. |
| `voteReject` reverts with `not voter` / `not disputed` | Caller not whitelisted, or no dispute exists | Use [`examples/voter/vote_reject.py`](examples/voter/vote_reject.py) — it validates before sending. |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
