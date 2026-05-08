# Blockchain News Agent (APEX v1)

A production-like APEX provider agent that searches for blockchain news using
DuckDuckGo and stores deliverables on IPFS via Pinata. Demonstrates the full
provider lifecycle under APEX v1:

```
client createJob → registerJob → setBudget → fund
      └── agent's funded-job poll loop picks up FUNDED jobs
          └── on_job(job) returns a news report
              └── SDK builds DeliverableManifest, uploads to IPFS (Pinata),
                  pins as "apex-job-{id}", calls commerce.submit with keccak256 hash
      └── after the dispute window an operator (or any party) calls router.settle(jobId)
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
| `STORAGE_PROVIDER` | `local` (default) or `ipfs` — read by the startup script; see below |
| `STORAGE_API_KEY` | Pinata JWT token (required when `STORAGE_PROVIDER=ipfs`) |
| `APEX_AGENT_URL` | Agent's public base URL including `/apex` (required when `STORAGE_PROVIDER=local`, e.g. `http://localhost:8003/apex`) |
| `APEX_SERVICE_PRICE` | Minimum acceptable budget in raw units (e.g. `1000000000000000000` = 1 U) |

### Optional overrides

```
NETWORK=bsc-testnet             (default)
RPC_URL=                        custom RPC endpoint (recommended for rate-limit avoidance)
STORAGE_GATEWAY_URL=            IPFS gateway (default: https://gateway.pinata.cloud/ipfs/)
APEX_COMMERCE_ADDRESS=          override Commerce proxy
APEX_ROUTER_ADDRESS=            override Router proxy
APEX_POLICY_ADDRESS=            override OptimisticPolicy
APEX_FUNDED_POLL_INTERVAL=30    funded-job poll cadence (seconds)
APEX_NEGOTIATE_RATE_LIMIT=120   /negotiate per-IP request budget
APEX_NEGOTIATE_RATE_WINDOW=60   rate-limit window (seconds)
APEX_MAX_RESPONSE_BYTES=5242880 response_content cap (5 MB)
APEX_MAX_METADATA_BYTES=262144  metadata cap (256 KB)
```

### Storage backends

`STORAGE_PROVIDER` is read by `service.py` / `service_mount.py` at startup and
determines which `StorageProvider` is injected into the SDK:

| `STORAGE_PROVIDER` | Provider used | On-chain `deliverable_url` |
|-|-|-|
| `local` (default) | `LocalStorageProvider` — writes JSON to disk | `{APEX_AGENT_URL}/job/{id}/response` (agent serves via HTTP) |
| `ipfs` | `IPFSStorageProvider` — pins to Pinata | `ipfs://CID` |

For a custom storage backend (MySQL, S3, your own CDN, etc.), instantiate your
`StorageProvider` subclass in `service.py` and pass it to `APEXConfig.from_env(storage=...)`
directly — `STORAGE_PROVIDER` is not required.

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
  settle.py              # Operator settle for a SUBMITTED job (post-verdict)
src/
  service.py             # create_apex_app() — APEX owns the app
  service_mount.py       # create_apex_app() + app.mount() — mount onto existing app
```

## APEX endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/apex/negotiate` | Price negotiation (rate-limited) |
| GET  | `/apex/job/{id}` | Job details |
| GET  | `/apex/job/{id}/response` | Stored deliverable response |
| GET  | `/apex/job/{id}/verify` | Job verification |
| GET  | `/apex/status` | Agent status (wallet, contracts, service price) |
| GET  | `/apex/health` | Health check |

## Settle

`router.settle(jobId)` is permissionless — any wallet can finalise a
SUBMITTED job and pay the gas. The agent server does not auto-settle, so
the typical operator action after the dispute window elapses without
dispute is to run the v1 helper script once per job:

```bash
uv run python scripts/settle.py 42
```

The helper checks that the job is `SUBMITTED` and the verdict is no
longer `PENDING` before sending the transaction. If the loaded wallet is
not `job.provider` it prints a warning but still proceeds, since settle
is permissionless. (A future `bnbagent` CLI will subsume this script.)

## Deliverable Storage

The agent builds a `DeliverableManifest` (job metadata + response content) for
each job, uploads it via the configured `StorageProvider`, and stores the URL
on-chain in `optParams.deliverable_url` so voters and clients can download and
verify the manifest independently.

- **`local`** — JSON written to `.agent-data/` (default). The SDK rewrites the
  `file://` URL to `{APEX_AGENT_URL}/job/{id}/response` before submitting;
  the agent serves the file via `GET /apex/job/{id}/response`.
- **`ipfs`** — JSON pinned to IPFS via Pinata as `apex-job-{id}`. The `ipfs://CID`
  URL is stored on-chain and resolved via the configured gateway.

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
