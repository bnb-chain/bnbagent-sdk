# Blockchain News Agent (APEX v1)

A production-like APEX provider agent that searches for blockchain news using
DuckDuckGo and stores deliverables on IPFS via Pinata. Demonstrates the full
provider lifecycle under APEX v1:

```
client createJob → registerJob → setBudget → fund
      └── agent startup-scan picks up FUNDED jobs
          └── on_job(job) returns a news report
              └── SDK builds DeliverableManifest, uploads to IPFS (Pinata),
                  pins as "apex-job-{id}", calls commerce.submit with keccak256 hash
                  └── auto-settle loop calls router.settle after the dispute window
```

No manual UMA assertion / bond step — APEX v1 uses the **OptimisticPolicy**:
silence approves after the dispute window, and any client-raised dispute must
reach a whitelisted-voter quorum to flip the verdict to REJECT.

## Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- A [Pinata](https://pinata.cloud) account with a JWT API key (for IPFS storage)

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env — see required variables below
```

### Required `.env` variables

| Variable | Description |
|----------|-------------|
| `WALLET_PASSWORD` | Keystore encryption password |
| `PRIVATE_KEY` | Agent wallet private key (first run only; encrypted to `~/.bnbagent/wallets/`) |
| `STORAGE_PROVIDER` | `ipfs` to use Pinata |
| `STORAGE_API_KEY` | Pinata JWT token |
| `APEX_SERVICE_PRICE` | Minimum acceptable budget in raw units (e.g. `1000000000000000000` = 1 U) |

### Optional overrides

```
NETWORK=bsc-testnet          (default)
RPC_URL=                     custom RPC endpoint (recommended for rate-limit avoidance)
STORAGE_GATEWAY_URL=         IPFS gateway (default: https://gateway.pinata.cloud/ipfs/)
APEX_COMMERCE_ADDRESS=       override Commerce proxy
APEX_ROUTER_ADDRESS=         override Router proxy
APEX_POLICY_ADDRESS=         override OptimisticPolicy
```

## Usage

### Run via `run_agent.py` (recommended)

```bash
uv run python scripts/run_agent.py
```

Starts `service.py` with `PYTHONUNBUFFERED=1` so the startup banner appears
immediately. The banner shows wallet address, contract addresses, service price,
and storage backend (e.g. `Storage: IPFS via Pinata`).

### Alternative: direct Uvicorn

```bash
uv run python src/service.py
```

### One-time ERC-8004 registration

```bash
uv run python scripts/register.py
```

### File structure

```
scripts/
  register.py            # One-time ERC-8004 registration
  run_agent.py           # Run standalone app (service.py)
  run_agent_mount.py     # Run mount mode (service_mount.py)
src/
  service.py             # create_apex_app() — APEX owns the app
  service_mount.py       # create_apex_app() + app.mount() — mount onto existing app
```

## APEX endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/apex/negotiate` | Price negotiation |
| POST | `/apex/submit` | Submit result |
| GET  | `/apex/job/{id}` | Job details |
| GET  | `/apex/job/{id}/response` | Stored deliverable response |
| GET  | `/apex/job/{id}/verify` | Job verification |
| POST | `/apex/job/{id}/settle` | Manual permissionless `router.settle` |
| POST | `/apex/job/execute` | Client-initiated job execution (requires `on_job`) |
| GET  | `/apex/status` | Agent status (wallet, contracts, service price) |
| GET  | `/apex/health` | Health check |

## Auto-settle

`create_apex_app(..., auto_settle=True)` (default) spawns a background loop
that polls `policy.check(jobId)` for this agent's submitted jobs and calls
`router.settle(jobId)` once the dispute window elapses. Settle is
permissionless, so clients don't have to do anything — the agent just gets
paid automatically. Tune with `auto_settle_interval=<seconds>`.

## IPFS Storage

Deliverables are pinned to IPFS via Pinata as JSON files named `apex-job-{id}`.
Each pin contains the full `DeliverableManifest` (job metadata + response
content). The `ipfs://CID` URL is stored on-chain in `optParams` so voters
and clients can download and verify the manifest independently.

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
