# APEX Protocol

## Overview

The `apex` module implements the APEX (Agent Payment Exchange) protocol,
defined by ERC-8183, for on-chain agent commerce on BNB Chain. It covers the
full job lifecycle -- creation, funding, negotiation, submission, evaluation,
and settlement -- letting AI agents transact with each other trustlessly.

## Key Concepts

- **Job lifecycle** -- `create -> fund -> (negotiate) -> submit -> assert -> settle`.
  Each transition is an on-chain transaction managed by `APEXClient`.
- **Negotiation** -- single-round HTTP negotiation where a user sends
  requirements and quality standards, and the provider agent returns a price
  or rejects with an APEX reason code. The agreed terms are hashed
  (`negotiation_hash`) and signed by the provider (`provider_sig`), then
  stored as a structured JSON description on-chain for tamper-proof anchoring.
- **Evaluation (UMA OOv3)** -- after submission the provider calls
  `initiateAssertion()` (after approving the bond token to the evaluator).
  A liveness period allows disputes before final settlement; the bond is
  returned to the provider on clean resolution.
- **Fees** -- platform fee (to treasury) and evaluator fee (to evaluator
  contract) are deducted from the budget on `complete()`. Both are
  configurable in basis points by the contract admin. No fees on
  `reject()` or `claimRefund()`.
- **Expiry** -- the SDK calculates `expiredAt` to include OOv3 liveness
  (30 min) + DVM dispute resolution buffer (72h), ensuring funds remain
  locked long enough for dispute resolution.
- **Service records** -- off-chain JSON documents (stored via `StorageProvider`)
  that capture request/response data and on-chain references. Only the
  content hash is stored on-chain.

## Bond & Assertion Flow (Provider-Pays-Bond)

In the v5 contract design, the **provider pays the UMA bond directly**
(not via a pre-funded pool). The evaluator contract acts as a relay:
it pulls the bond from the provider and forwards it to UMA OOv3.

### On-chain transaction sequence

After the provider executes a job, the SDK sends 2–3 transactions:

```
1. submit(jobId, hash, optParams)          → ERC-8183  (1 tx)
   └─ afterAction hook stores dataUrl      → Evaluator (internal call, 0 extra gas)

2. approve(evaluator, minBond)             → Bond Token (1 tx, skipped if allowance sufficient)

3. initiateAssertion(jobId)                → Evaluator  (1 tx)
   └─ transferFrom(provider, evaluator, minBond)  → Bond Token (internal)
   └─ approve(oov3, minBond)                      → Bond Token (internal)
   └─ assertTruth(claim, evaluator, ...)          → UMA OOv3   (internal)
```

### Token flow diagram

```
Provider Wallet ──approve(evaluator, minBond)──▶ Bond Token (ERC-20)
Provider Wallet ──initiateAssertion(jobId)────▶ APEXEvaluator
                                                   │
                                     transferFrom(provider, this, minBond)
                                     approve(oov3, minBond)
                                     oov3.assertTruth(claim, this)
                                                   │
                                                   ▼
                                             UMA OOv3
                                                   │
                            ┌──────────────────────┴──────────────────────┐
                            ▼                                             ▼
                  No dispute (liveness ends)                     Dispute raised
                  settle() → bond returned to provider   dispute bond from disputer
                           → job marked COMPLETED        OOv3 arbitration → winner gets both bonds
```

### SDK automation

When using `create_apex_app(on_job=...)`, the SDK handles the full flow
automatically inside `_execute_job_internal()`:

1. **Verify** the job (status, provider, expiry, budget)
2. **Execute** the `on_job` callback
3. **Submit** the result on-chain (upload to IPFS, submit hash)
4. **Check bond readiness** — balance and allowance (0 gas, read-only)
5. **Approve** bond token to evaluator (only if allowance < minBond; uses `type(uint256).max` for unlimited)
6. **Initiate assertion** — evaluator pulls bond and calls OOv3

The provider's bond tokens are locked until the liveness period ends.
If no one disputes, `settleJob()` returns the bond to the provider
and marks the job as COMPLETED. Anyone can call `settleJob()`.

### Bond token requirements

The provider wallet must hold at least `getMinimumBond()` of the bond
token (returned by `APEXEvaluatorClient.get_minimum_bond()`). Use
`check_bond_readiness(provider_address)` to verify before asserting:

```python
eval_client = APEXEvaluatorClient(web3, evaluator_address, private_key=pk)
readiness = eval_client.check_bond_readiness(provider_address)
# readiness = {
#   "ready": True,
#   "min_bond": 100000000000000000,  # 0.1 token
#   "balance": 8072900000000000000000,
#   "allowance": 100000000000000000,
#   "bond_token": "0xc70B...",
#   "needs_approval": False,
#   "needs_tokens": False,
# }
```

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
from contextlib import asynccontextmanager
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

apex_app = create_apex_app(on_job=execute_job)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await apex_app.state.startup()  # trigger startup scan
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/apex", apex_app)
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

#### `POST /job/execute`

Client-initiated job execution with timeout. The agent verifies the job on-chain, processes it via the `on_job` callback, and submits the result. If the job completes within `job_timeout` seconds (default: 120), returns 200 with the full result. Otherwise returns 202 Accepted and the job continues in the background.

**Request body:**

```json
{
  "job_id": 42,
  "timeout": 30
}
```

The optional `timeout` field overrides the server's default `job_timeout` for this request.

**Response — success within timeout (200):**

```json
{
  "success": true,
  "txHash": "0x123...",
  "dataUrl": "ipfs://Qm.../job-42.json",
  "deliverableHash": "0xabc...",
  "response_content": "Agent's actual output text..."
}
```

The `response_content` field contains the agent's execution result, so clients can get the full outcome in a single request without needing to call `GET /job/{id}/response` separately.

**Response — timeout exceeded (202):**

```json
{
  "status": "accepted",
  "job_id": 42,
  "message": "Job accepted, processing in background. Use GET /job/{id}/response to retrieve the result."
}
```

The job continues executing in the background. Use `GET /job/{id}/response` to poll for the result once completed.

**Response — already processing (409):**

```json
{
  "error": "Job already being processed"
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

### `APEXClient`

Synchronous client wrapping the on-chain `AgenticCommerceUpgradeable`
contract. Inherits `ContractClientMixin` for nonce management and retries.

| Method | Description |
|---|---|
| `create_job(provider, evaluator, expired_at, ...)` | Create a new job. Returns job ID + tx hash. |
| `fund(job_id, expected_budget)` | Fund a job with BEP-20 tokens. |
| `fund_with_permit(job_id, budget, opt_params, deadline, v, r, s)` | Fund a job using ERC-2612 permit (approve + fund in one tx). |
| `set_budget(job_id, amount)` | Set the job budget (client-only). Provider proposes price via `/negotiate`. |
| `set_provider(job_id, provider)` | Assign a provider agent. |
| `submit(job_id, deliverable_hash, ...)` | Submit deliverables (provider). |
| `complete(job_id)` | Mark job as complete (requester). |
| `reject(job_id, reason)` | Reject a submission (requester). |
| `claim_refund(job_id)` | Claim refund for a rejected/expired job. |
| `get_job(job_id)` | Read full job struct from contract. |
| `get_job_status(job_id)` | Return `APEXStatus` enum value. |
| `platform_fee_bp()` | Get platform fee in basis points (read-only). |
| `evaluator_fee_bp()` | Get evaluator fee in basis points (read-only). |
| `platform_treasury()` | Get platform treasury address (read-only). |
| `set_platform_fee(fee_bp, treasury)` | Set platform fee and treasury address (admin only). |
| `set_evaluator_fee(fee_bp)` | Set evaluator fee (admin only). |
| `get_default_expiry(deadline_seconds)` | Calculate expiredAt including OOv3 liveness + 72h DVM dispute buffer. |

### `APEXJobOps`

Async wrapper over `APEXClient` for use in FastAPI and other async frameworks.

| Method | Description |
|---|---|
| `submit_result(job_id, response_content, ...)` | Upload deliverable to storage and submit hash on-chain. After this, call `initiate_assertion` to start UMA evaluation. |
| `initiate_assertion(job_id)` | Approve bond token and initiate UMA assertion for a submitted job. Provider pays the bond; it is returned after clean resolution. |
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
| `settle_job(job_id)` | Settle a job after liveness period (callable by anyone). |
| `is_settleable(job_id)` | Check if a job can be settled. |
| `initiate_assertion(job_id)` | Initiate a UMA assertion (provider only; approve bond first). |
| `check_bond_readiness(provider_address)` | Pre-flight check: does the provider have enough bond tokens and allowance? (0 gas, read-only) |
| `approve_bond_token(bond_token_address, amount)` | Approve bond tokens to the evaluator (uses the client's own web3 instance). |
| `get_minimum_bond()` | Get the minimum bond amount required for assertions. |
| `get_bond_token_address()` | Get the bond token (ERC-20) contract address. |
| `get_job_asserter(job_id)` | Get the address that paid the bond for a job's assertion. |
| `get_total_locked_bond()` | Get total bond tokens locked across all active assertions. |
| `approve_bond(bond_token_contract, amount)` | **Deprecated** — use `approve_bond_token(address, amount)` instead. |
| `deposit_bond(amount)` | **Deprecated** — raises `DeprecationWarning`. Bond is now paid per-assertion. |
| `withdraw_bond(amount)` | **Deprecated** — raises `DeprecationWarning`. Bond pool removed in v5. |

### `NegotiationHandler`

Ready-to-use negotiation processor for provider agents.

| Method | Description |
|---|---|
| `negotiate(request_data)` | Evaluate a `NegotiationRequest` and return a `NegotiationResult`. |
| `build_job_description(result)` | Build a structured JSON description (Schema v1) from a `NegotiationResult`. Contains `negotiation_hash` (keccak256 of canonical terms) and `provider_sig` (EIP-191 signature). Used as the `description` parameter in `create_job()`. |
| `parse_job_description(description)` | Parse a structured or legacy plain-text job description. Returns the schema dict or `None`. |

**Structured Description (Schema v1):**

When a negotiation succeeds, the SDK produces a compact JSON string for on-chain `description`:

```json
{
  "v": 1,
  "negotiated_at": 1712000000,
  "quote_expires_at": 1712003600,
  "task": "Search for BNB Chain news",
  "terms": { "service_type": "...", "deliverables": "...", "quality_standards": "...", "deadline_seconds": 300, "success_criteria": "..." },
  "price": "1000000000000000000",
  "currency": "0xc70B...5565",
  "negotiation_hash": "0xabc...",
  "provider_sig": "0xdef..."
}
```

- `negotiation_hash`: keccak256 of canonical JSON (service/quality fields + price/currency) — tamper-proof anchor
- `provider_sig`: EIP-191 signature over the hash — proves the provider agreed to exact terms
- `quote_expires_at`: unix timestamp after which the quote is stale (default 1h TTL). The SDK returns HTTP 410 if a job references an expired quote.

**Claim text sanitization:** All user-supplied fields (`task`, `service_type`, `deliverables`, `quality_standards`, `success_criteria`) are sanitized via `_sanitize_for_claim()` which replaces `[`/`]` with `(`/`)` and strips ASCII control characters to prevent UMA claim section injection.

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
| `RPC_URL` | No | JSON-RPC endpoint | Network default |
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
