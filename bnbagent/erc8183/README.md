# ERC-8183 Protocol (v1)

## Overview

The `erc8183` module implements **ERC-8183**, an agentic commerce stack built on
[ERC-8183](https://eips.ethereum.org/EIPS/eip-8183) with a pluggable,
UMA-style optimistic evaluator. It covers the full job lifecycle —
create → register → setBudget → fund → submit → settle — so a **client**
and a **provider** can transact trustlessly through three contracts:

1. **AgenticCommerce** (ERC-8183 kernel) — job state + escrow.
2. **EvaluatorRouter** — `jobId → policy` binding; doubles as `job.evaluator` and `job.hook`. `settle(jobId)` is permissionless.
3. **OptimisticPolicy** — reference policy: **silence past the dispute window approves**; a client-raised dispute triggers a whitelisted-voter `voteReject` quorum.

## Architecture

```
ERC8183Client (facade)  ──┬──►  CommerceClient  ──►  AgenticCommerceUpgradeable
                       ├──►  RouterClient    ──►  EvaluatorRouterUpgradeable
                       ├──►  PolicyClient    ──►  OptimisticPolicy
                       └──►  MinimalERC20    ──►  Payment token (immutable on kernel)
```

Most callers only use `ERC8183Client`. The sub-clients are exposed as
attributes for advanced workflows (direct `admin` calls, batch reads, etc.).

## Key Concepts

| Term | Meaning |
|------|---------|
| **Job lifecycle** | `OPEN → FUNDED → SUBMITTED → COMPLETED / REJECTED / EXPIRED`. |
| **Policy** | Contract implementing `IPolicy`. `OptimisticPolicy` is v1's only policy. |
| **Dispute window** | Grace period after `submit` in which the client can call `policy.dispute(jobId)`. Silence ⇒ approve. |
| **Voter** | Admin-whitelisted EOA that can cast `voteReject`. Reaching `voteQuorum` flips the verdict to REJECT. Voters cannot approve — approval is implicit by silence. |
| **Permissionless settle** | `router.settle(jobId)` pulls the current verdict from the policy and applies it. Anyone can call — clients, voters, or the provider's own operator script. |
| **Claim refund** | `commerce.claimRefund(jobId)` after `expiredAt` — non-pausable, non-hookable universal escape hatch. |
| **Platform fee** | Basis points deducted from the budget on `complete` and sent to the platform treasury (configured by the Commerce admin). No fees on `reject` or `claimRefund`. |
| **Negotiation** | Single-round HTTP exchange. The agreed terms are anchored on-chain in the job `description`. |

## Quick Start

### Client-side: drive a job with `ERC8183Client`

```python
import time
from bnbagent.erc8183 import ERC8183Client, JobStatus
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key="0x...")
erc8183 = ERC8183Client(wallet, network="bsc-testnet")

budget = 1 * (10 ** erc8183.token_decimals())
expired_at = int(time.time()) + 65 * 60

res = erc8183.create_job(provider=provider_addr, expired_at=expired_at, description="task")
job_id = res["jobId"]

erc8183.register_job(job_id)            # bind OptimisticPolicy
erc8183.set_budget(job_id, budget)
erc8183.fund(job_id, budget)            # floor-based auto-approval

# ... provider submits, dispute window elapses ...
erc8183.settle(job_id)
assert erc8183.get_job_status(job_id) == JobStatus.COMPLETED
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
from bnbagent.erc8183 import ERC8183Client

custom = replace(
    resolve_network("bsc-testnet"),
    rpc_url="https://my-private-node.example.com",
)
erc8183 = ERC8183Client(wallet, network=custom)
```

### Provider-side: FastAPI agent

```python
from bnbagent.erc8183.server import create_erc8183_app

def execute_job(job: dict) -> str:
    return f"Processed: {job['description']}"

app = create_erc8183_app(on_job=execute_job)
```

Built-in behaviour:

- **Funded-job poll loop** (default 30 s, override via `ERC8183_FUNDED_POLL_INTERVAL`): incrementally scans `jobCounter` and auto-processes every newly FUNDED job assigned to this provider — no external trigger required.
- **Deliverable size caps**: `submit_result` rejects oversized payloads before upload — `response_content` is capped at 5 MB and the `metadata` JSON at 256 KB. Override via `ERC8183_MAX_RESPONSE_BYTES` / `ERC8183_MAX_METADATA_BYTES`. Excess returns `error_code=413`.
- **Settle is delegated** to operator scripts. `router.settle(jobId)` is permissionless; operators run a separate process (or an ad-hoc script using `ERC8183Client.settle`) once the dispute window elapses or a verdict is finalised.

### Voter-side: `voteReject` and settle

```python
from bnbagent.erc8183 import ERC8183Client
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="your-password", private_key=voter_pk)
erc8183 = ERC8183Client(wallet, network="bsc-testnet")
if erc8183.policy.is_voter(erc8183.address) and erc8183.policy.disputed(job_id):
    erc8183.vote_reject(job_id)
    # once rejectVotes >= voteQuorum, anyone can settle:
    erc8183.settle(job_id)
```

`examples/voter/watch.py` automates the full loop: it polls `Disputed` and
`VoteCast` events, downloads the `DeliverableManifest` from IPFS, verifies the
hash, prompts the voter to `[r]eject / [s]kip`, and calls `router.settle`
automatically when `rejectVotes >= voteQuorum`.

See [`examples/voter/`](../../examples/voter/).

## API Reference

### HTTP Endpoints

All endpoints are mounted under a configurable prefix (default `/erc8183`).

#### `POST /negotiate`

Single-round price negotiation. Request body: `{"terms": {...}, "task_description": "..."}`. Returns either an accepted quote (with signed `negotiation_hash`) or a rejection with a reason code.

The combined `task_description` + `terms` must fit the on-chain `job.description` cap (`MAX_DESCRIPTION_BYTES = 4096`). Over-length requests are rejected at negotiation time with reason code `TASK_TOO_LONG` (`0x07`) — the description is **not** silently truncated, because truncating after signing would invalidate `negotiation_hash` / `provider_sig`.

The endpoint is rate-limited per client IP (defaults: 120 requests / 60 seconds, configurable via `ERC8183_NEGOTIATE_RATE_LIMIT` and `ERC8183_NEGOTIATE_RATE_WINDOW`); over-budget callers receive `429 Too Many Requests`. Quote TTL is bounded by `NegotiationHandler.MAX_QUOTE_TTL_SECONDS = 300` so leaked or replayed `provider_sig` values cannot accumulate value over time.

When deployed behind a reverse proxy (nginx, AWS ALB, k8s ingress), run uvicorn with `--forwarded-allow-ips='<proxy_cidr>'` so `request.client.host` resolves to the real client IP. Without this flag every request appears to originate from the proxy, defeating per-client throttling.

#### `GET /job/{id}` / `/response` / `/verify`

Job details from the kernel; stored deliverable; SDK-side preflight (status, provider, expiry, budget ≥ service_price).

#### `GET /status`

Returns `commerce_address`, `router_address`, `policy_address`, `service_price`, payment token, and decimals so clients know the minimum acceptable budget.

#### `GET /health`

Liveness probe.

---

### `ERC8183Client`

High-level facade. Most useful methods:

| Method | Purpose |
|--------|---------|
| `create_job(...)` | Create a job; defaults `evaluator` and `hook` to the Router. Returns `{jobId, transactionHash, receipt}`. |
| `register_job(job_id, policy=None)` | Bind the configured policy (or override) to a job on the Router. |
| `set_budget(job_id, amount)` | Client sets the escrow amount. |
| `fund(job_id, amount, *, approve_floor=None)` | Approves (if needed) and funds. See floor strategy above. |
| `submit(job_id, deliverable, opt_params)` | Provider submits 32-byte `deliverable` (`DeliverableManifest.manifest_hash()`, keccak256 of canonical manifest JSON); `opt_params` dict (must contain `deliverable_url`) is serialized to JSON and forwarded as `optParams`. |
| `cancel_open(job_id, reason=...)` | Client cancels while OPEN; no escrow moved. |
| `claim_refund(job_id)` | Refund via expiry. Non-pausable, non-hookable. |
| `settle(job_id, evidence=b"")` | Permissionless verdict-application. |
| `mark_expired(job_id)` | Permissionless reconciliation of the Router's in-flight counter for jobs that exited via `claim_refund`. |
| `dispute(job_id)` | Client raises a dispute (within window). |
| `vote_reject(job_id)` | Whitelisted voter casts a reject vote. |
| `get_job(job_id)` | Returns typed `Job` dataclass (incl. on-chain `deliverable` bytes32). |
| `get_job_status(job_id)` | Returns a `JobStatus` enum. |
| `get_verdict(job_id)` | Simulate `Policy.check` — returns `(Verdict, reason)`. |
| `inflight_job_count()` | Number of jobs the Router currently tracks as in-flight. |
| `dispute_quorum_snapshot(job_id)` | Reject-quorum snapshotted at `dispute()` time. |

Token helpers: `payment_token` (cached address), `token_decimals()`, `token_symbol()`, `token_balance(address=None)`, `token_allowance(owner, spender)`, `approve_payment_token(spender, amount)`.

Sub-clients: `erc8183.commerce`, `erc8183.router`, `erc8183.policy` (instances of `CommerceClient`, `RouterClient`, `PolicyClient`).

### `CommerceClient`

1:1 wrapper over `AgenticCommerceUpgradeable`: `create_job`, `set_provider`, `set_budget`, `fund`, `submit`, `complete`, `reject`, `claim_refund`, `get_job`, `payment_token`, `platform_fee_bp`, `platform_treasury`, `get_jobs_batch` (Multicall3), plus event helpers (`get_job_funded_events`, `get_job_created_events`, `get_deliverable_url`).

### `RouterClient`

Router surface: `register_job`, `settle`, `mark_expired`, `commerce`, `job_policy`, `policy_whitelist`, `paused`, `inflight_job_count`, `get_job_registered_events`, `get_job_settled_events`, `get_job_finalised_events`.

### `PolicyClient`

OptimisticPolicy surface:

- **Writes**: `dispute` (client), `vote_reject` (voter), admin methods `add_voter`, `remove_voter`, `set_quorum`.
- **Reads**: `check`, `submitted_at`, `disputed`, `reject_votes`, `has_voted`, `is_voter`, `dispute_window`, `vote_quorum`, `dispute_quorum_snapshot`, `active_voter_count`, `admin`, `commerce`, `router`.
- `get_deliverable_url(job_id, *, hint_block=None)` — reads `JobInitialised.optParams` to extract `deliverable_url`. Pass `hint_block` (e.g. the block number of the `Disputed` event) to keep the `eth_getLogs` window tight and avoid RPC block-range limits.

### `ERC8183JobOps`

Async wrapper over `ERC8183Client` used by `create_erc8183_app`. Key methods:
`submit_result`, `get_job`, `get_response`, `get_pending_jobs`, `verify_job`. Settle is permissionless on-chain and is the responsibility of operator scripts, not the agent server.

### `NegotiationHandler`

Single-round negotiation processor. `negotiate(request) → NegotiationResult`; `build_job_description(result)` produces a Schema v1 JSON anchor with `negotiation_hash` + `provider_sig`; `parse_job_description` recovers the structured form.

**Chain binding (recommended).** When `chain_id` and `verifying_contract` are passed to the handler, both fields are embedded in the signed JSON content so `provider_sig` cannot be replayed across EVM chains or commerce contracts. Use `NegotiationHandler.from_erc8183_client(client, service_price=..., wallet_provider=...)` to populate both automatically from the live `ERC8183Client` — this is what `create_erc8183_app()` does by default. Wallet-signing failures inside `negotiate()` now log at `WARNING` level so operators can detect wallet outages (the quote is still returned, but without `provider_sig`).

### Types (`erc8183.types`)

- `JobStatus` — `OPEN, FUNDED, SUBMITTED, COMPLETED, REJECTED, EXPIRED` (matches `IACP.JobStatus`).
- `Verdict` — `PENDING, APPROVE, REJECT` (matches `VERDICT_*`).
- `REASON_APPROVED`, `REASON_REJECTED` — `keccak256("OPTIMISTIC_APPROVED" / "OPTIMISTIC_REJECTED")`.
- `Job` — typed dataclass returned by `CommerceClient.get_job`. Fields: `id`, `client`, `provider`, `evaluator`, `description`, `budget`, `expired_at`, `status`, `hook`, `deliverable` (bytes32, set by `submit`; `b"\x00" * 32` until then).

### `ERC8183Config`

Unified dataclass consumed by `create_erc8183_app`. Primary API:
`wallet_provider`, `network` (str or `NetworkConfig`), `storage`,
`service_price`. Convenience API: `private_key + wallet_password` →
auto-wrapped into `EVMWalletProvider`; the plaintext key is zeroed
immediately after wrapping.

Contract-address overrides are **not** fields — pass either a
`NetworkConfig(...)` as `network=` for fully explicit control, or use the
`ERC8183_*` env vars below (applied lazily by `effective_network`).

`ERC8183Config.from_env()` reads:

| Variable | Required | Description |
|----------|----------|-------------|
| `PRIVATE_KEY` | Recommended | Imported to keystore on first run. |
| `WALLET_PASSWORD` | Yes | Keystore password. |
| `NETWORK` | No | `bsc-testnet` (default) / `bsc-mainnet`. |
| `RPC_URL` | No | Override RPC endpoint. |
| `ERC8183_COMMERCE_ADDRESS` | No | Override Commerce proxy. |
| `ERC8183_ROUTER_ADDRESS` | No | Override Router proxy. |
| `ERC8183_POLICY_ADDRESS` | No | Override policy. |
| `ERC8183_SERVICE_PRICE` | No | Minimum acceptable budget (default 1e18). |
| `STORAGE_PROVIDER` | No | `"local"` (default) or `"ipfs"`. |
| `STORAGE_API_KEY` | If IPFS | Pinning-service JWT. |

The payment token address is **not** configurable — it is fetched from
`commerce.paymentToken()` at runtime and cached.

## Related

- [`wallets`](../wallets/README.md) — wallet providers injected into `ERC8183Config`.
- [`storage`](../storage/README.md) — off-chain storage for deliverables.
- [`erc8004`](../erc8004/README.md) — agent identity registry.
- [`core`](../core/README.md) — nonce manager, contract mixin, Multicall3.
