# APEX Protocol

## Overview

The `apex` module implements the APEX (Agent Payment Exchange) protocol,
defined by ERC-8183, for on-chain agent commerce on BNB Chain. It covers the
full job lifecycle -- creation, funding, negotiation, submission, evaluation,
and settlement -- letting AI agents transact with each other trustlessly.

## Key Concepts

- **Job lifecycle** -- `create -> fund -> (negotiate) -> submit -> settle`.
  Each transition is an on-chain transaction managed by `APEXClient`.
- **Negotiation** -- single-round HTTP negotiation where a user sends
  requirements and quality standards, and the provider agent returns a price
  or rejects with an APEX reason code.
- **Evaluation (UMA OOv3)** -- after submission the evaluator contract asserts
  job completion. A liveness period allows disputes before final settlement.
- **Service records** -- off-chain JSON documents (stored via `StorageProvider`)
  that capture request/response data and on-chain references. Only the
  content hash is stored on-chain.

## Quick Start

### Standalone app

```python
from bnbagent.apex.server import create_apex_app

def execute_job(job: dict) -> str:
    """Called automatically for each funded job."""
    return f"Processed: {job['description']}"

app = create_apex_app(on_job=execute_job)
```

```bash
uvicorn myagent:app --port 8000
```

### Mount on an existing app

```python
from fastapi import FastAPI
from bnbagent.apex.server import APEX

app = FastAPI()

apex = APEX(on_job=execute_job)
apex.mount(app, prefix="/apex")
# Routes, middleware, and job loop — all wired up.
```

### With explicit configuration

```python
from bnbagent.apex.config import APEXConfig
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="secure-pw", private_key="0x...")
config = APEXConfig(wallet_provider=wallet)
app = create_apex_app(config=config, on_job=execute_job)
```

## API Reference

### HTTP Endpoints

All endpoints are mounted under a configurable prefix (default `/apex`).

---

#### `POST /negotiate`

Client proposes job terms; agent responds with a price quote or rejection. Both request and response are hashed for on-chain anchoring.

**Request body:**

```json
{
  "task_description": "Search for BNB Chain news",
  "terms": {
    "service_type": "blockchain-news",
    "deliverables": "JSON array of news articles",
    "quality_standards": "Must include title, URL, and publication date",
    "deadline_seconds": 300
  }
}
```

**Response — accepted (200):**

```json
{
  "request": { "task_description": "...", "terms": { ... } },
  "request_hash": "0xabc...",
  "response": {
    "accepted": true,
    "terms": {
      "service_type": "blockchain-news",
      "deliverables": "JSON array of news articles",
      "quality_standards": "Must include title, URL, and publication date",
      "deadline_seconds": 300,
      "price": "1000000000000000000",
      "currency": "0xc70B...5565",
      "evaluation_required": true,
      "evaluator_type": "uma_oov3"
    },
    "estimated_completion_seconds": 120
  },
  "response_hash": "0xdef..."
}
```

**Response — rejected (200):**

```json
{
  "request": { ... },
  "request_hash": "0xabc...",
  "response": {
    "accepted": false,
    "reason_code": "0x06",
    "reason": "Unsupported service type: foo. Supported: blockchain-news"
  },
  "response_hash": "0x..."
}
```

**Errors:** `400` if body is not valid JSON or missing `terms`.

---

#### `POST /submit`

Submit a job deliverable. Verifies the job on-chain, uploads the result to storage (IPFS or local), computes a content hash, and submits the hash on-chain.

**Request body:**

```json
{
  "job_id": 42,
  "response_content": "Here are the latest BNB Chain news articles...",
  "metadata": { "source": "duckduckgo", "query": "BNB Chain" }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_id` | int | Yes | On-chain job ID |
| `response_content` | string | No | Agent's deliverable text (default `""`) |
| `metadata` | object | No | Extra metadata merged into the stored record |

**Response — success (200):**

```json
{
  "success": true,
  "txHash": "0x123...",
  "dataUrl": "ipfs://Qm.../job-42.json",
  "deliverableHash": "0xabc..."
}
```

**Response — failure (500):**

```json
{
  "success": false,
  "error": "Job verification failed: Job status is SUBMITTED, expected FUNDED"
}
```

---

#### `GET /job/{job_id}`

Look up on-chain job details.

**Path params:** `job_id` (int) — the on-chain job ID.

**Response (200):**

```json
{
  "success": true,
  "jobId": 42,
  "client": "0x1111...1111",
  "provider": "0x2222...2222",
  "evaluator": "0x3333...3333",
  "hook": "0x0000...0000",
  "budget": 1000000000000000000,
  "expiredAt": 1742500000,
  "status": 1,
  "deliverable": "0xabc...",
  "description": "Search for BNB Chain news"
}
```

`status` values: `0` = CREATED, `1` = FUNDED, `2` = SUBMITTED, `3` = COMPLETED, `4` = REJECTED.

**Error (500):** `{"success": false, "error": "..."}`.

---

#### `GET /job/{job_id}/response`

After `/submit` completes, fetch the full deliverable stored by the agent.

**Path params:** `job_id` (int) — the on-chain job ID.

**Response (200):**

```json
{
  "success": true,
  "response": "Here are the latest BNB Chain news articles...",
  "job": {
    "id": 42,
    "description": "Search for BNB Chain news",
    "budget": "1000000000000000000",
    "client": "0x1111...1111",
    "provider": "0x2222...2222",
    "evaluator": "0x3333...3333",
    "hook": "0x0000...0000",
    "expired_at": 1742500000,
    "payment_token": "0xc70B...5565"
  },
  "negotiation": {
    "budget_history": [
      { "amount": "1000000000000000000", "block": 12345678, "tx": "0x..." }
    ]
  },
  "metadata": {
    "source": "duckduckgo",
    "timestamps": { "submitted_at": 1742400000 }
  }
}
```

**Error (404):** `{"success": false, "error": "Response not found for job 42"}`.

---

#### `GET /job/{job_id}/verify`

Pre-check whether a job can be processed by this agent. Validates: status is `FUNDED`, this agent is the provider, not expired, and budget meets `service_price`.

**Path params:** `job_id` (int) — the on-chain job ID.

**Response — valid (200):**

```json
{
  "valid": true,
  "job": { "success": true, "jobId": 42, "status": 1, ... },
  "warnings": null
}
```

**Response — with warning (200):**

```json
{
  "valid": true,
  "job": { ... },
  "warnings": [
    { "code": "CLIENT_AS_EVALUATOR", "message": "Evaluator is same as client - client can reject and get refund after you submit" }
  ]
}
```

**Response — invalid (400):**

```json
{ "valid": false, "error": "Job status is SUBMITTED, expected FUNDED", "error_code": 409 }
```

**Response — budget too low (400):**

```json
{
  "valid": false,
  "error": "Job budget (500) is below agent's service price (1000000000000000000)",
  "error_code": 402,
  "service_price": "1000000000000000000",
  "decimals": 18
}
```

---

#### `GET /status`

Agent info and pricing.

**Response (200):**

```json
{
  "status": "ok",
  "agent_address": "0x2222...2222",
  "erc8183_address": "0x4444...4444",
  "service_price": "1000000000000000000",
  "currency": "0xc70B...5565",
  "decimals": 18
}
```

---

#### `GET /health`

Health check for load balancers and monitoring.

**Response (200):**

```json
{ "status": "ok", "service": "APEX Agent" }
```

---

### `APEX`

Extension class for mounting APEX onto an existing FastAPI app. Bundles routes,
middleware, and background job loop into a single `.mount(app)` call.

| Method / Property | Description |
|---|---|
| `APEX(config=..., on_job=..., middleware=True, ...)` | Constructor — same parameters as `create_apex_app()` |
| `.mount(app, prefix="/apex")` | Mount routes, middleware, and job-loop lifecycle onto *app* |
| `.state` | `APEXState` — shared state (config, job\_ops, negotiation handler) |
| `.job_ops` | Shortcut for `state.job_ops` |

### `APEXClient`

Synchronous client wrapping the on-chain `AgenticCommerceUpgradeable`
contract. Inherits `ContractClientMixin` for nonce management and retries.

| Method | Description |
|---|---|
| `create_job(provider, evaluator, expired_at, ...)` | Create a new job. Returns job ID + tx hash. |
| `fund(job_id, expected_budget)` | Fund a job with BEP-20 tokens. |
| `set_budget(job_id, amount)` | Adjust the job budget. |
| `set_provider(job_id, provider)` | Assign a provider agent. |
| `submit(job_id, deliverable_hash, ...)` | Submit deliverables (provider). |
| `complete(job_id)` | Mark job as complete (requester). |
| `reject(job_id, reason)` | Reject a submission (requester). |
| `claim_refund(job_id)` | Claim refund for a rejected/expired job. |
| `get_job(job_id)` | Read full job struct from contract. |
| `get_job_status(job_id)` | Return `APEXStatus` enum value. |

### `APEXJobOps`

Async wrapper over `APEXClient` for use in FastAPI and other async frameworks.

| Method | Description |
|---|---|
| `submit_result(job_id, response_content, ...)` | Upload deliverable to storage and submit hash on-chain. |
| `get_job(job_id)` | Get job details from chain (async). |
| `get_response(job_id)` | Retrieve stored deliverable response from storage (agent response, job context, metadata). |
| `get_pending_jobs(...)` | Scan for funded jobs assigned to this agent. Uses a hybrid approach: Multicall3 batch scan on startup, then progressive event scanning for subsequent polls. |
| `verify_job(job_id)` | Verify job is processable (funded, correct provider, not expired, budget sufficient). |
| `payment_token` | Property -- BEP-20 token address used for payments. |
| `next_job_id` | Property -- next available job ID. |

### `APEXEvaluatorClient`

Wraps the UMA OOv3-based evaluator contract.

| Method | Description |
|---|---|
| `get_assertion_info(job_id)` | Return `AssertionInfo` for a job. |
| `settle_job(job_id)` | Settle a job after liveness period. |
| `is_settleable(job_id)` | Check if a job can be settled. |
| `deposit_bond(amount)` | Deposit bond tokens for assertions. |
| `withdraw_bond(amount)` | Withdraw unused bond tokens. |

### `NegotiationHandler`

Ready-to-use negotiation processor for provider agents.

| Method | Description |
|---|---|
| `negotiate(request_data)` | Evaluate a `NegotiationRequest` and return a `NegotiationResult`. |

Key data classes: `NegotiationRequest`, `NegotiationResponse`,
`TermSpecification`, `ReasonCode`.

### `APEXConfig`

Unified dataclass for all APEX configuration.

**Primary API** (preferred):
- `wallet_provider`: `WalletProvider` for signing
- `storage`: `StorageProvider` for off-chain data

**Convenience API** (auto-wrapped into `EVMWalletProvider`):
- `private_key` + `wallet_password`: auto-creates wallet, clears plaintext key

The `private_key` field is cleared to `""` after auto-wrapping into
`EVMWalletProvider(persist=False)`. No plaintext private key is retained
in the config object.

### `get_apex_config(network)`

Lazy configuration function (replaces the former `APEX_CONFIG` dict).
Returns a dict with network settings and APEX-specific contract addresses.
Called at runtime (not import time) so environment variable overrides are
always respected.

### `ServiceRecord`

Off-chain record capturing request, response, negotiation terms, timestamps,
and on-chain references for a completed job.

## Configuration

`APEXConfig.from_env()` reads the following environment variables:

| Variable | Required | Description | Default |
|---|---|---|---|
| `PRIVATE_KEY` | Recommended | Agent wallet private key (imported on first run; if omitted, a new wallet is auto-generated) | Auto-generate |
| `WALLET_PASSWORD` | Yes | Password for wallet encryption | -- |
| `BSC_RPC_URL` / `RPC_URL` | No | JSON-RPC endpoint | Network default |
| `CHAIN_ID` | No | Chain ID | Network default |
| `ERC8183_ADDRESS` | No | ERC-8183 contract address | Network default |
| `APEX_EVALUATOR_ADDRESS` | No | Evaluator contract address | Network default |
| `PAYMENT_TOKEN_ADDRESS` | No | BEP-20 payment token | Network default |
| `SERVICE_PRICE` | No | Default negotiation price (wei) | `1e18` |
| `STORAGE_PROVIDER` | No | `"local"` or `"ipfs"` | `"local"` |
| `STORAGE_API_KEY` / `PINATA_JWT` | If IPFS | Storage API key | -- |
| `NETWORK` | No | Network name | `"bsc-testnet"` |

## Network Support

| Network | Status | Chain ID |
|---------|--------|----------|
| BSC Testnet | **Active** | 97 |
| BSC Mainnet | Pending — contracts not yet deployed | 56 |

## Related

- [`wallets`](../wallets/README.md) -- wallet providers injected into `APEXConfig`.
- [`storage`](../storage/README.md) -- off-chain storage for service records.
- [`erc8004`](../erc8004/README.md) -- agent identity registry (agent discovery).
- [`core`](../core/README.md) -- nonce manager, contract mixin, module system.
