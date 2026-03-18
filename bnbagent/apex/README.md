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

```python
from bnbagent.apex.server.routes import create_apex_app

def my_task(job: dict) -> str:
    """Called automatically for each funded job."""
    return f"Processed: {job['description']}"

# on_task enables automatic job discovery, verification, and submission
app = create_apex_app(on_task=my_task)
```

```bash
uvicorn myagent:app --port 8000
```

Or with explicit configuration:

```python
from bnbagent.apex.config import APEXConfig
from bnbagent.wallets import EVMWalletProvider

wallet = EVMWalletProvider(password="secure-pw", private_key="0x...")
config = APEXConfig(wallet_provider=wallet)
app = create_apex_app(config=config, on_task=my_task)
```

## API Reference

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
| `PRIVATE_KEY` | Yes | Agent wallet private key | -- |
| `WALLET_PASSWORD` | Yes | Password for wallet encryption | -- |
| `BSC_RPC_URL` / `RPC_URL` | No | JSON-RPC endpoint | Network default |
| `CHAIN_ID` | No | Chain ID | Network default |
| `ERC8183_ADDRESS` | No | ERC-8183 contract address | Network default |
| `APEX_EVALUATOR_ADDRESS` | No | Evaluator contract address | Network default |
| `PAYMENT_TOKEN_ADDRESS` | No | BEP-20 payment token | Network default |
| `AGENT_PRICE` | No | Default negotiation price (wei) | `1e18` |
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
