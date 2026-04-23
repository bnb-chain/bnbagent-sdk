# APEX Protocol (v1)

## Overview

The `apex` module implements **APEX v1**, an agentic commerce stack built on
[ERC-8183](https://eips.ethereum.org/EIPS/eip-8183) with a pluggable,
UMA-style optimistic evaluator. It covers the full job lifecycle —
create → register → setBudget → fund → submit → settle — so a **client**
and a **provider** can transact trustlessly through three contracts:

1. **AgenticCommerce** (ERC-8183 kernel) — job state + escrow.
2. **EvaluatorRouter** — `jobId → policy` binding; doubles as `job.evaluator` and `job.hook`. `settle(jobId)` is permissionless.
3. **OptimisticPolicy** — reference policy: **silence past the dispute window approves**; a client-raised dispute triggers a whitelisted-voter `voteReject` quorum.

## Architecture

```
APEXClient (facade)  ──┬──►  CommerceClient  ──►  AgenticCommerceUpgradeable
                       ├──►  RouterClient    ──►  EvaluatorRouterUpgradeable
                       ├──►  PolicyClient    ──►  OptimisticPolicy
                       └──►  MinimalERC20    ──►  Payment token (immutable on kernel)
```

Most callers only use `APEXClient`. The sub-clients are exposed as
attributes for advanced workflows (direct `admin` calls, batch reads, etc.).

## Key Concepts

| Term | Meaning |
|------|---------|
| **Job lifecycle** | `OPEN → FUNDED → SUBMITTED → COMPLETED / REJECTED / EXPIRED`. |
| **Policy** | Contract implementing `IPolicy`. `OptimisticPolicy` is v1's only policy. |
| **Dispute window** | Grace period after `submit` in which the client can call `policy.dispute(jobId)`. Silence ⇒ approve. |
| **Voter** | Admin-whitelisted EOA that can cast `voteReject`. Reaching `voteQuorum` flips the verdict to REJECT. Voters cannot approve — approval is implicit by silence. |
| **Permissionless settle** | `router.settle(jobId)` pulls the current verdict from the policy and applies it. Anyone can call; the SDK's agent server auto-settles its own submissions. |
| **Claim refund** | `commerce.claimRefund(jobId)` after `expiredAt` — non-pausable, non-hookable universal escape hatch. |
| **Platform fee** | Basis points deducted from the budget on `complete` and sent to the platform treasury (configured by the Commerce admin). No fees on `reject` or `claimRefund`. |
| **Negotiation** | Single-round HTTP exchange. The agreed terms are anchored on-chain in the job `description`. |

## Quick Start

### Client-side: drive a job with `APEXClient`

```python
import time
from bnbagent.apex import APEXClient, JobStatus
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key="0x...")
apex = APEXClient(wallet, network="bsc-testnet")

budget = 1 * (10 ** apex.token_decimals())
expired_at = int(time.time()) + 65 * 60

res = apex.create_job(provider=provider_addr, expired_at=expired_at, description="task")
job_id = res["jobId"]

apex.register_job(job_id)            # bind OptimisticPolicy
apex.set_budget(job_id, budget)
apex.fund(job_id, budget)            # floor-based auto-approval

# ... provider submits, dispute window elapses ...
apex.settle(job_id)
assert apex.get_job_status(job_id) == JobStatus.COMPLETED
```

Fund approval strategy (`fund(..., approve_floor=...)`):

- `None` (default) → approve `max(amount, 100 * 10**decimals)` (stablecoin-friendly floor; residual allowance bounded).
- `0` → approve exactly `amount`.
- `X` → approve `max(amount, X)`.

If the existing allowance already covers `amount`, no approve is sent.

#### Custom networks / RPCs

`network=` also accepts a `NetworkConfig`, which is used verbatim (env vars
are ignored for that call). Handy for private RPCs, local forks, and
bespoke deployments:

```python
from dataclasses import replace
from bnbagent.config import resolve_network
from bnbagent.apex import APEXClient

custom = replace(
    resolve_network("bsc-testnet"),
    rpc_url="https://my-private-node.example.com",
)
apex = APEXClient(wallet, network=custom)
```

### Provider-side: FastAPI agent

```python
from bnbagent.apex.server import create_apex_app

def execute_job(job: dict) -> str:
    return f"Processed: {job['description']}"

app = create_apex_app(on_job=execute_job)
```

Built-in behaviour:

- One-time Multicall3 scan of `jobCounter` on boot; auto-processes all FUNDED jobs assigned to this provider.
- `POST /apex/job/execute` for client-triggered execution (200 fast-path, 202 Accepted with background continuation).
- **Auto-settle loop**: polls `policy.check(jobId)` for this agent's submitted jobs and calls `router.settle(jobId)` when the verdict is no longer PENDING.

### Voter-side: `voteReject`

```python
from bnbagent.apex import APEXClient
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key=voter_pk)
apex = APEXClient(wallet, network="bsc-testnet")
if apex.policy.is_voter(apex.address) and apex.policy.disputed(job_id):
    apex.vote_reject(job_id)
```

See [`examples/voter/`](../../examples/voter/).

## API Reference

### HTTP Endpoints

All endpoints are mounted under a configurable prefix (default `/apex`).

#### `POST /negotiate`

Single-round price negotiation. Request body: `{"terms": {...}, "task_description": "..."}`. Returns either an accepted quote (with signed `negotiation_hash`) or a rejection with a reason code.

#### `POST /submit`

Provider submits a deliverable. The SDK uploads the payload to storage, submits `keccak256(data_url)` on-chain, and tracks the job for auto-settle. Body: `{"job_id", "response_content", "metadata"?}`.

#### `POST /job/{id}/settle`

Permissionless `router.settle(jobId)`. Useful for operators; the auto-settle loop handles the common case.

#### `POST /job/execute`

Client-initiated synchronous execution. Requires `on_job` in `create_apex_app`.

#### `GET /job/{id}` / `/response` / `/verify`

Job details from the kernel; stored deliverable; SDK-side preflight (status, provider, expiry, budget ≥ service_price).

#### `GET /status`

Returns `commerce_address`, `router_address`, `policy_address`, `service_price`, payment token, and decimals so clients know the minimum acceptable budget.

#### `GET /health`

Liveness probe.

---

### `APEXClient`

High-level facade. Most useful methods:

| Method | Purpose |
|--------|---------|
| `create_job(...)` | Create a job; defaults `evaluator` and `hook` to the Router. Returns `{jobId, transactionHash, receipt}`. |
| `register_job(job_id, policy=None)` | Bind the configured policy (or override) to a job on the Router. |
| `set_budget(job_id, amount)` | Client sets the escrow amount. |
| `fund(job_id, amount, *, approve_floor=None)` | Approves (if needed) and funds. See floor strategy above. |
| `submit(job_id, deliverable, data_url=None)` | Provider submits 32-byte deliverable hash; `data_url` is stored as `optParams`. |
| `cancel_open(job_id, reason=...)` | Client cancels while OPEN; no escrow moved. |
| `claim_refund(job_id)` | Refund via expiry. Non-pausable, non-hookable. |
| `settle(job_id, evidence=b"")` | Permissionless verdict-application. |
| `dispute(job_id)` | Client raises a dispute (within window). |
| `vote_reject(job_id)` | Whitelisted voter casts a reject vote. |
| `get_job(job_id)` | Returns typed `Job` dataclass. |
| `get_job_status(job_id)` | Returns a `JobStatus` enum. |
| `get_verdict(job_id)` | Simulate `Policy.check` — returns `(Verdict, reason)`. |

Token helpers: `payment_token` (cached address), `token_decimals()`, `token_symbol()`, `token_balance(address=None)`, `token_allowance(owner, spender)`, `approve_payment_token(spender, amount)`.

Sub-clients: `apex.commerce`, `apex.router`, `apex.policy` (instances of `CommerceClient`, `RouterClient`, `PolicyClient`).

### `CommerceClient`

1:1 wrapper over `AgenticCommerceUpgradeable`: `create_job`, `set_provider`, `set_budget`, `fund`, `submit`, `complete`, `reject`, `claim_refund`, `get_job`, `payment_token`, `platform_fee_bp`, `platform_treasury`, `get_jobs_batch` (Multicall3), plus event helpers (`get_job_funded_events`, `get_job_created_events`, `get_submit_data_url`).

### `RouterClient`

Router surface: `register_job`, `settle`, `commerce`, `job_policy`, `policy_whitelist`, `paused`, `get_job_registered_events`, `get_job_settled_events`.

### `PolicyClient`

OptimisticPolicy surface:

- **Writes**: `dispute` (client), `vote_reject` (voter), admin methods `add_voter`, `remove_voter`, `set_quorum`.
- **Reads**: `check`, `submitted_at`, `disputed`, `reject_votes`, `has_voted`, `is_voter`, `dispute_window`, `vote_quorum`, `active_voter_count`, `admin`, `commerce`, `router`.

### `APEXJobOps`

Async wrapper over `APEXClient` used by `create_apex_app`. Key methods:
`submit_result`, `get_job`, `get_response`, `get_pending_jobs`, `verify_job`, `track_for_settle`, `auto_settle_once`. The companion coroutine `run_auto_settle_loop(ops, interval)` drives permissionless settle for provider-owned submissions.

### `NegotiationHandler`

Single-round negotiation processor. `negotiate(request) → NegotiationResult`; `build_job_description(result)` produces a Schema v1 JSON anchor with `negotiation_hash` + `provider_sig`; `parse_job_description` recovers the structured form.

### Types (`apex.types`)

- `JobStatus` — `OPEN, FUNDED, SUBMITTED, COMPLETED, REJECTED, EXPIRED` (matches `IACP.JobStatus`).
- `Verdict` — `PENDING, APPROVE, REJECT` (matches `VERDICT_*`).
- `REASON_APPROVED`, `REASON_REJECTED` — `keccak256("OPTIMISTIC_APPROVED" / "OPTIMISTIC_REJECTED")`.
- `Job` — typed dataclass returned by `CommerceClient.get_job`.

### `APEXConfig`

Unified dataclass consumed by `create_apex_app`. Primary API:
`wallet_provider`, `network` (str or `NetworkConfig`), `storage`,
`service_price`. Convenience API: `private_key + wallet_password` →
auto-wrapped into `EVMWalletProvider`; the plaintext key is zeroed
immediately after wrapping.

Contract-address overrides are **not** fields — pass either a
`NetworkConfig(...)` as `network=` for fully explicit control, or use the
`APEX_*` env vars below (applied lazily by `effective_network`).

`APEXConfig.from_env()` reads:

| Variable | Required | Description |
|----------|----------|-------------|
| `PRIVATE_KEY` | Recommended | Imported to keystore on first run. |
| `WALLET_PASSWORD` | Yes | Keystore password. |
| `NETWORK` | No | `bsc-testnet` (default) / `bsc-mainnet`. |
| `RPC_URL` | No | Override RPC endpoint. |
| `APEX_COMMERCE_ADDRESS` | No | Override Commerce proxy. |
| `APEX_ROUTER_ADDRESS` | No | Override Router proxy. |
| `APEX_POLICY_ADDRESS` | No | Override policy. |
| `APEX_SERVICE_PRICE` | No | Minimum acceptable budget (default 1e18). |
| `STORAGE_PROVIDER` | No | `"local"` (default) or `"ipfs"`. |
| `STORAGE_API_KEY` | If IPFS | Pinning-service JWT. |

The payment token address is **not** configurable — it is fetched from
`commerce.paymentToken()` at runtime and cached.

## Related

- [`wallets`](../wallets/README.md) — wallet providers injected into `APEXConfig`.
- [`storage`](../storage/README.md) — off-chain storage for deliverables.
- [`erc8004`](../erc8004/README.md) — agent identity registry.
- [`core`](../core/README.md) — nonce manager, contract mixin, Multicall3.
