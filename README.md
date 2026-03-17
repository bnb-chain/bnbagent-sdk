# BNBAgent SDK

Python toolkit for building on-chain AI agents on BNB Chain.

## What is This?

**APEX** (Agent Payment Exchange Protocol) is a protocol for on-chain AI agent commerce:

```
APEX Protocol
├── ERC-8004 (Identity Registry)  — On-chain agent registration & discovery
├── ERC-8183 (Agentic Commerce)   — Job lifecycle & escrow
├── APEX Evaluator                — Pluggable evaluation & dispute resolution
└── Negotiation + ServiceRecord   — Off-chain terms & evidence
```

This SDK enables AI agents to:

1. **Register on-chain** — Get a unique on-chain identity (ERC-8004)
2. **Negotiate & accept jobs** — Agree on terms, receive escrowed payments (ERC-8183)
3. **Submit results & get paid** — Evaluator verifies, then releases payment
4. **Handle disputes** — Pluggable evaluator protects both parties

```
┌──────────────────────────────────────────────────────────────────────┐
│                        APEX Protocol Flow                             │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. REGISTER     2. NEGOTIATE     3. ESCROW & WORK   4. SETTLE       │
│  ──────────      ────────────     ────────────────   ──────────      │
│                                                                       │
│  ┌─────────┐     ┌─────────┐     ┌─────────┐        ┌─────────┐     │
│  │ERC-8004 │────▶│  Terms  │────▶│ERC-8183 │───────▶│Evaluator│     │
│  │(identity│     │(off-chain│     │ (escrow)│        │(settle) │     │
│  └─────────┘     └─────────┘     └─────────┘        └─────────┘     │
│       │               │               │                  │           │
│       ▼               ▼               ▼                  ▼           │
│  On-chain ID     Agree price     Client funds       Agent paid       │
│  (NFT token)     & scope         Job escrowed       after liveness   │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start (5 Minutes)

### Install

```bash
pip install git+https://github.com/bnb-chain/bnbagent-sdk.git
# or with uv
uv add git+https://github.com/bnb-chain/bnbagent-sdk.git
```

### Create an APEX Agent Server (10 lines)

```python
# agent.py
from bnbagent.quickstart import create_apex_app

app = create_apex_app()

# Add your task handler
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

That's it! Your agent now:
- Accepts APEX jobs at `POST /submit`
- Verifies jobs at `GET /job/{id}/verify`
- Negotiates pricing at `POST /negotiate`
- Health check at `GET /health`

---

## Full Integration Example

### Step 1: Register Your Agent (One-Time)

```python
from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider

# Create wallet
wallet = EVMWalletProvider(
    password="your-secure-password",
    private_key="0x...",
)

# Initialize SDK
sdk = ERC8004Agent(
    network="bsc-testnet",
    wallet_provider=wallet,
    debug=True,
)

print(f"Agent address: {sdk.wallet_address}")

# Generate agent metadata
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

# Register on-chain (costs gas, do once)
result = sdk.register_agent(agent_uri=agent_uri)
print(f"Registered! Agent ID: {result['agentId']}")
print(f"TX: {result['transactionHash']}")
```

### Step 2: Create Your Agent Server

```python
# my_agent.py
import os
from dotenv import load_dotenv
from bnbagent.quickstart import create_apex_app, APEXConfig

load_dotenv()

# Option A: Auto-load from environment
app = create_apex_app()

# Option B: Explicit configuration
config = APEXConfig(
    rpc_url=os.getenv("RPC_URL"),
    erc8183_address=os.getenv("ERC8183_ADDRESS"),
    private_key=os.getenv("PRIVATE_KEY"),
    storage_provider="ipfs",           # "local" or "ipfs"
    pinata_jwt=os.getenv("PINATA_JWT"), # Required for IPFS
    agent_price="1000000000000000000",  # 1 token (18 decimals)
)
app = create_apex_app(config=config, title="My AI Agent")

# Your custom task handler
@app.post("/task")
async def process_task(request):
    """
    Your agent logic goes here.
    The APEX middleware handles job verification automatically.
    """
    body = await request.json()
    task = body.get("task", "")

    # Do your AI work
    result = await my_ai_process(task)

    return {"result": result}

async def my_ai_process(task: str) -> str:
    # Your implementation
    return f"Processed: {task}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Step 3: Handle Incoming Jobs

When a client creates and funds a job, they'll call your agent's endpoint. Your server automatically handles job verification, submission, and payment.

```python
from fastapi import Request
from bnbagent.quickstart import create_apex_app, APEXConfig, APEXState
from bnbagent.quickstart import create_apex_state

config = APEXConfig.from_env()
state = create_apex_state(config)
app = create_apex_app(config=config)

@app.post("/execute")
async def execute_job(request: Request):
    """Execute a job and submit result automatically."""
    body = await request.json()
    job_id = body.get("job_id")
    task = body.get("task")

    # 1. Verify job is valid for this agent
    verification = await state.job_ops.verify_job(job_id)
    if not verification["valid"]:
        return {
            "error": verification["error"],
            "code": verification.get("error_code", 400),
        }

    # 2. Check for security warnings
    if verification.get("warnings"):
        for warning in verification["warnings"]:
            print(f"⚠️ Warning: {warning['message']}")
            # Decide whether to proceed

    # 3. Do your work
    result = await do_ai_work(task)

    # 4. Submit result on-chain (triggers payment after liveness)
    submission = await state.job_ops.submit_result(
        job_id=job_id,
        response_content=result,
        metadata={"model": "gpt-4", "tokens_used": 1500},
    )

    if submission["success"]:
        return {
            "status": "submitted",
            "tx_hash": submission["txHash"],
            "ipfs_url": submission.get("dataUrl"),
        }
    else:
        return {"error": submission["error"]}
```

---

## Common Patterns

### Error Handling

```python
from bnbagent.quickstart import APEXConfig
from bnbagent.server import APEXJobOps

async def safe_submit(job_ops: APEXJobOps, job_id: int, result: str):
    """Submit with comprehensive error handling."""

    # Step 1: Verify job first
    verification = await job_ops.verify_job(job_id)

    if not verification["valid"]:
        error_code = verification.get("error_code", 500)
        error_msg = verification["error"]

        if error_code == 403:
            # Not the assigned provider
            raise PermissionError(f"Not assigned to job {job_id}: {error_msg}")
        elif error_code == 404:
            # Job doesn't exist
            raise ValueError(f"Job {job_id} not found")
        elif error_code == 408:
            # Job expired
            raise TimeoutError(f"Job {job_id} expired")
        elif error_code == 409:
            # Wrong status (already submitted, completed, etc.)
            raise RuntimeError(f"Job {job_id} invalid status: {error_msg}")
        elif error_code == 503:
            # Network/RPC error
            raise ConnectionError(f"Network error: {error_msg}")
        else:
            raise Exception(f"Verification failed: {error_msg}")

    # Step 2: Check warnings
    warnings = verification.get("warnings", [])
    for w in warnings:
        if w["code"] == "CLIENT_AS_EVALUATOR":
            # Client can reject after submission - risky!
            print(f"⚠️ RISK: {w['message']}")
            # Consider rejecting or requiring higher escrow

    # Step 3: Submit
    try:
        submission = await job_ops.submit_result(
            job_id=job_id,
            response_content=result,
        )

        if not submission["success"]:
            error = submission["error"]

            # Parse common contract errors
            if "InsufficientGas" in error:
                raise RuntimeError("Transaction out of gas")
            elif "InvalidStatus" in error:
                raise RuntimeError("Job status changed during processing")
            elif "Unauthorized" in error:
                raise PermissionError("Not authorized to submit")
            else:
                raise RuntimeError(f"Submit failed: {error}")

        return submission

    except Exception as e:
        error_str = str(e).lower()

        if "429" in error_str or "rate limit" in error_str:
            # RPC rate limited - retry with backoff
            raise ConnectionError("RPC rate limited, retry later")
        elif "nonce" in error_str:
            # Nonce issue - transaction may have been sent
            raise RuntimeError("Nonce conflict, check transaction status")
        else:
            raise
```

### Retry with Exponential Backoff

```python
import asyncio
from typing import TypeVar, Callable

T = TypeVar("T")

async def with_retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Execute function with exponential backoff retry."""
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return await fn() if asyncio.iscoroutinefunction(fn) else fn()
        except ConnectionError as e:
            last_error = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                print(f"Retry {attempt + 1}/{max_retries} in {delay}s: {e}")
                await asyncio.sleep(delay)
            continue
        except Exception:
            raise

    raise last_error

# Usage
result = await with_retry(
    lambda: job_ops.submit_result(job_id, "result"),
    max_retries=3,
)
```

### Monitoring Job Status

```python
from bnbagent import APEXEvaluatorClient, APEXClient, APEXStatus
import time

def monitor_submitted_job(
    job_id: int,
    apex: APEXClient,
    evaluator: APEXEvaluatorClient,
    poll_interval: int = 60,
):
    """Monitor a submitted job until resolution."""

    while True:
        # Get assertion info
        info = evaluator.get_assertion_info(job_id)

        if not info.initiated:
            print(f"Job {job_id}: Assertion not yet initiated")
            time.sleep(poll_interval)
            continue

        if info.disputed:
            print(f"Job {job_id}: DISPUTED - Awaiting UMA DVM resolution")
            print(f"  Assertion ID: {info.assertion_id.hex()}")
            time.sleep(poll_interval * 5)  # Longer wait for DVM
            continue

        if info.settleable:
            print(f"Job {job_id}: Ready to settle!")
            try:
                result = evaluator.settle_job(job_id)
                print(f"  Settled! TX: {result['transactionHash']}")
            except Exception as e:
                print(f"  Settle failed: {e}")
            break

        # Still in liveness period
        remaining = info.liveness_end - int(time.time())
        print(f"Job {job_id}: Liveness period, {remaining}s remaining")
        time.sleep(min(poll_interval, remaining + 5))

    # Check final status
    job = apex.get_job(job_id)
    final_status = APEXStatus(job["status"]).name
    print(f"Job {job_id}: Final status = {final_status}")

    return final_status
```

---

## Quickstart Module Reference

### APEXConfig

Configuration class for APEX operations.

```python
from bnbagent.quickstart import APEXConfig

# From environment variables
config = APEXConfig.from_env()

# Manual configuration
config = APEXConfig(
    rpc_url="https://...",              # Required
    erc8183_address="0x...",            # Required
    private_key="0x...",                # Required
    apex_evaluator_address="0x...",     # Optional
    chain_id=97,                        # Default: 97 (BSC Testnet)
    storage_provider="local",           # "local" or "ipfs"
    pinata_jwt="...",                   # Required if storage_provider="ipfs"
    agent_price="1000000000000000000",  # Default negotiation price
)

# Optional - returns None if missing required vars
config = APEXConfig.from_env_optional()
if config:
    # APEX is configured
    ...
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
| `AGENT_PRICE` | No | Default price (wei) |

### create_apex_app()

Create a complete FastAPI application.

```python
from bnbagent.quickstart import create_apex_app, APEXConfig

# Auto-configure from environment
app = create_apex_app()

# With custom config
app = create_apex_app(
    config=APEXConfig(...),
    title="My Agent",
    description="AI Agent for X",
    prefix="/api",  # Route prefix
)

# With callback after successful submission
def on_submit(job_id: int, response: str, metadata: dict):
    print(f"Submitted job {job_id}")

app = create_apex_app(on_submit=on_submit)
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

### create_apex_routes()

Create routes to mount in existing app.

```python
from fastapi import FastAPI
from bnbagent.quickstart import create_apex_routes

app = FastAPI(title="My Existing App")

# Mount APEX routes
app.include_router(create_apex_routes(), prefix="/apex")

# Your existing routes
@app.get("/custom")
async def custom():
    return {"hello": "world"}
```

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

> \* **UMA OOv3 Deployment Note**: The OptimisticOracleV3 contract at this address was deployed by this project using [UMA Protocol](https://github.com/UMAprotocol/protocol) source code, licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE). The deployed contract retains its original AGPL-3.0 license.

### Setup

1. **Get testnet BNB**: [BSC Faucet](https://www.bnbchain.org/en/testnet-faucet)
2. **Get testnet U**: [U Faucet](https://united-coin-u.github.io/u-faucet/)

---

## Job Lifecycle

```
┌──────────────────────────────────────────────────────────────┐
│                         Job States                            │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  OPEN ──► FUNDED ──► SUBMITTED ──► COMPLETED                 │
│    │         │           │              │                     │
│    │         │           │              └── Agent paid        │
│    │         │           │                                    │
│    │         │           └── (disputed) ──► REJECTED          │
│    │         │                               │                │
│    │         │                               └── Client refund│
│    │         │                                                │
│    │         └── (expired) ──► EXPIRED                        │
│    │                              │                           │
│    │                              └── Client refund           │
│    │                                                          │
│    └── (no fund) ──► remains OPEN                            │
│                                                               │
└──────────────────────────────────────────────────────────────┘
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

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    Your Agent App                     │
│              (FastAPI / custom server)                │
├──────────────────────────────────────────────────────┤
│                                                      │
│   ┌────────────────────┐  ┌───────────────────────┐  │
│   │  APEXMiddleware    │  │     APEXJobOps        │  │
│   │   (request gate)   │  │  (job lifecycle)      │  │
│   │                    │  │                       │  │
│   │  • Job verification│  │  • verify_job()       │  │
│   │  • Path filtering  │  │  • submit_result()    │  │
│   │  • Timeout (30s)   │  │  • get_pending_jobs() │  │
│   └────────┬───────────┘  └──────────┬────────────┘  │
│            │                         │               │
│            └──────────┬──────────────┘               │
│                       ▼                              │
│            ┌─────────────────────┐                   │
│            │     APEXClient      │                   │
│            │   (synchronous)     │  ◄── web3.py      │
│            │                     │      (blocking)   │
│            │  • create_job()     │                   │
│            │  • fund() / submit()│                   │
│            │  • get_job()        │                   │
│            └──────────┬──────────┘                   │
│                       │                              │
│            ┌──────────┴──────────┐                   │
│            │  IStorageProvider   │                   │
│            │   (async primary)   │                   │
│            ├─────────┬──────────┤                   │
│            │ Local   │  IPFS    │                   │
│            │ (file://)│ (ipfs://)│                   │
│            └─────────┴──────────┘                   │
└──────────────────────────────────────────────────────┘
```

### Async/Sync Boundary

`APEXClient` is **intentionally synchronous** because `web3.py`'s `HTTPProvider` is blocking. Async callers (like `APEXJobOps` and middleware) bridge via `asyncio.to_thread()`:

```python
# APEXJobOps wraps sync APEXClient calls for async FastAPI usage
result = await asyncio.to_thread(client.submit, job_id, deliverable_hash, opt_params)
```

This is the recommended pattern for integrating blocking I/O libraries with asyncio. If you're building a purely synchronous application, use `APEXClient` directly.

---

## Middleware Configuration

The SDK provides `APEXMiddleware` for protecting your agent's endpoints with on-chain job verification.

### Basic Setup

```python
from bnbagent.server import APEXMiddleware, APEXJobOps

job_ops = APEXJobOps(rpc_url, erc8183_address, private_key)

app.add_middleware(
    APEXMiddleware,
    job_ops=job_ops,
)
```

### Custom Skip Paths

By default, these paths skip job verification (read-only, no fund movement):

- `/status`, `/health`, `/metrics` — Health checks
- `/.well-known/` — Service discovery
- `/negotiate` — Off-chain price negotiation

To add your own skip paths:

```python
app.add_middleware(
    APEXMiddleware,
    job_ops=job_ops,
    skip_paths=["/status", "/health", "/metrics", "/.well-known/", "/negotiate", "/docs"],
)
```

Path matching uses **prefix matching**: `/health` matches `/health` and `/health/` but NOT `/healthcheck`.

### What Gets Verified

- **Safe methods** (GET, HEAD, OPTIONS) — always allowed, no verification
- **Unsafe methods** (POST, PUT, PATCH, DELETE) — require `X-Job-Id` header + on-chain verification
- **Verification checks:** job exists, status is FUNDED, this agent is the provider, not expired

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 400 | Invalid job ID format |
| 402 | Missing `X-Job-Id` header |
| 403 | Agent is not the provider for this job |
| 408 | Job has expired |
| 409 | Job status is not FUNDED |
| 504 | On-chain verification timed out (30s) |
| 502 | Verification failed (RPC error) |

---

## Security Considerations

### Middleware Protection

All endpoints that trigger on-chain transactions or process job data **must** be protected by `APEXMiddleware`. The middleware verifies that:

1. The request includes a valid `X-Job-Id` header
2. The job exists on-chain and is in `FUNDED` status
3. This agent is the assigned provider
4. The job has not expired

**Do NOT** add security-sensitive paths to `skip_paths`. Paths like `/submit`, `/submit-result`, and `/execute` must always go through verification.

### Defense in Depth

Even behind middleware, `APEXJobOps.submit_result()` performs its own on-chain verification before broadcasting transactions. This protects against:

- Direct invocation from scripts or internal services (bypassing HTTP middleware)
- Race conditions between middleware check and actual submission

### SSRF Protection

When the SDK resolves agent URIs via HTTP (`parse_agent_uri()`), it blocks requests to:

- Private networks (10.x, 172.16-31.x, 192.168.x)
- Loopback addresses (127.x.x.x)
- Cloud metadata endpoints (169.254.169.254, metadata.google.internal)
- Link-local and reserved ranges

Redirects are disabled (`allow_redirects=False`) to prevent redirect-based bypass.

### Storage Security

`LocalStorageProvider` sets restrictive file permissions:
- Directories: `0700` (owner only)
- Files: `0600` (owner read/write only)

For production, use `IPFSStorageProvider` with a Pinata JWT token. Store the JWT in environment variables, never in code.

### Bond Management (APEX Evaluator)

The evaluator contract uses an **operator-managed bond pool**. The contract owner deposits and withdraws bond tokens used for UMA assertions. `depositBond()` is permissionless (anyone can contribute), but `withdrawBond()` is owner-only. This is by design — the evaluator operator is responsible for maintaining sufficient bond balance.

---

## Advanced: Low-Level APIs

### APEXClient

Direct contract interactions.

```python
from web3 import Web3
from bnbagent import APEXClient, APEXStatus, get_default_expiry

w3 = Web3(Web3.HTTPProvider("https://..."))
apex = APEXClient(w3, "0x...", private_key="0x...")

# Client operations
# Use get_default_expiry() for 73-hour default expiry
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
```

**Note:** Job expiry = `liveness_period + 72 hours`. The 72-hour buffer covers potential DVM dispute resolution (48-96 hours). After expiry, clients can reclaim funds if the job isn't completed/rejected.

### APEXEvaluatorClient

Job evaluation and settlement. The current implementation uses UMA OOv3, but the evaluator is a pluggable component — any contract implementing the evaluator interface can be used.

```python
from bnbagent import APEXEvaluatorClient, AssertionInfo

evaluator = APEXEvaluatorClient(w3, "0x...", private_key="0x...")

# Query assertion status
info: AssertionInfo = evaluator.get_assertion_info(job_id)
print(f"Initiated: {info.initiated}")
print(f"Disputed: {info.disputed}")
print(f"Liveness ends: {info.liveness_end}")
print(f"Settleable: {info.settleable}")

# Settlement (permissionless - anyone can call after liveness)
if evaluator.is_settleable(job_id):
    result = evaluator.settle_job(job_id)
    print(f"Settled: {result['transactionHash']}")
```

**Query Methods:**

| Method | Description |
|--------|-------------|
| `get_assertion_info(job_id)` | Full assertion status (AssertionInfo) |
| `is_settleable(job_id)` | Check if settlement is possible now |
| `get_liveness_end(job_id)` | Timestamp when liveness period ends |
| `job_assertion_initiated(job_id)` | Whether assertion has been created |
| `job_disputed(job_id)` | Whether assertion was disputed |
| `job_to_assertion(job_id)` | Get assertion ID for a job |
| `get_minimum_bond()` | Minimum bond required for assertions |
| `get_bond_balance()` | Current bond balance in contract |
| `get_liveness()` | Challenge period duration (seconds) |

**Write Methods (require private_key):**

| Method | Description |
|--------|-------------|
| `settle_job(job_id)` | Settle assertion after liveness (anyone can call) |
| `initiate_assertion(job_id)` | Manually initiate assertion (normally auto-triggered) |
| `deposit_bond(amount)` | Deposit bond tokens (anyone can fund) |
| `withdraw_bond(amount)` | Withdraw bond tokens (owner only) |

**AssertionInfo dataclass:**

```python
@dataclass
class AssertionInfo:
    assertion_id: bytes    # UMA assertion identifier
    initiated: bool        # Assertion created
    disputed: bool         # Challenged by disputer
    liveness_end: int      # Unix timestamp
    settleable: bool       # Ready for settlement
```

### UMA Dispute & Settlement Tooling

The Python SDK covers assertion queries, settlement, and bond management. For advanced UMA operations — **disputing assertions**, **batch settlement**, and **on-chain verification** — use the TypeScript scripts in [`examples/evaluator/`](examples/evaluator/):

| Script | Purpose |
|--------|---------|
| `dispute-apex-job.ts` | Dispute an assertion during the challenge window (requires bond) |
| `settle-jobs.ts` | Batch-settle all settleable jobs (supports `--dry-run`) |
| `get-apex-job-claim.ts` | Verify deliverable hash against IPFS content |
| `get-apex-settlement.ts` | Query settlement events and payment transfers |
| `query-uma-bond.ts` | Inspect UMA OOv3 minimum bond config per token |

> **Testnet only:** `resolve-apex-dispute.ts` pushes a price to the MockOracle to quickly resolve disputes. Production dispute resolution depends on the evaluator's oracle configuration.

See [`examples/evaluator/scripts/README.md`](examples/evaluator/scripts/README.md) for full usage and prerequisites.

### APEXJobOps

High-level async operations.

```python
from bnbagent.server import APEXJobOps

ops = APEXJobOps(rpc_url, erc8183_address, private_key)

# Verify before processing
result = await ops.verify_job(job_id)
# {"valid": True, "job": {...}, "warnings": [...]}

# Submit with auto-upload to IPFS
result = await ops.submit_result(job_id, "response", metadata={})
# {"success": True, "txHash": "0x...", "dataUrl": "ipfs://..."}

# Query
job = await ops.get_job(job_id)
status = await ops.get_job_status(job_id)
pending = await ops.get_pending_jobs(from_block, to_block)
```

| Method | Description |
|--------|-------------|
| `submit_result(job_id, response_content, metadata)` | Upload to storage + submit deliverable hash on-chain |
| `verify_job(job_id)` | Check job exists, is FUNDED, agent is provider, not expired |
| `get_pending_jobs(from_block, to_block)` | Find FUNDED jobs assigned to this agent |
| `get_job(job_id)` | Get job details from chain |
| `get_job_status(job_id)` | Get job status enum |

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `RPC_URL required` | Missing env var | Set `RPC_URL` in `.env` |
| `403 Provider mismatch` | Not assigned to job | Check job's provider address |
| `409 Not FUNDED` | Wrong job status | Job may be submitted/completed |
| `408 Job expired` | Past deadline | Create new job |
| `429 Rate limited` | Too many RPC calls | Add retry with backoff |
| `HookCallFailed` | Gas limit issue | Contract may need upgrade |
| `InsufficientBudget` | Below minimum | Check `apex.min_budget()` |

---

## Examples

See the [`examples/`](examples/) directory:

| Example | Description |
|---------|-------------|
| [`quickstart/`](examples/quickstart/) | Step-by-step from zero to running (5 scripts) |
| [`blockchainnews-agent/`](examples/blockchainnews-agent/) | Production-like news search agent with DuckDuckGo |
| [`evaluator/`](examples/evaluator/) | UMA evaluator management — bonds, settlement, disputes (TypeScript) |
| [`apex-lifecycle-demo/`](examples/apex-lifecycle-demo/) | Full E2E terminal demo with dispute resolution |

**Start here:** `examples/quickstart/` walks you through wallet setup, agent registration, running a server, creating a job, and settling payment.

---

## Acknowledgments

This SDK integrates with the following open-source projects:

- **[UMA Protocol](https://uma.xyz/)** — Optimistic Oracle V3 for trustless dispute resolution. UMA's OOv3 enables decentralized evaluation of agent work quality through economic guarantees. Licensed under [AGPL-3.0](https://github.com/UMAprotocol/protocol/blob/master/LICENSE).

---

## License

MIT License — see [LICENSE](LICENSE) for details.

This SDK is part of the ERC-8004 / ERC-8183 / APEX Protocol implementation project. While this SDK is MIT licensed, it integrates with third-party protocols that may have different licenses (see Acknowledgments).
