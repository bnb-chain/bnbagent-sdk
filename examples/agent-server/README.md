# Blockchain News Agent (ERC-8183)

A production-like ERC-8183 provider agent that searches for blockchain news using
DuckDuckGo and stores deliverables on IPFS via Pinata. Demonstrates the full
provider lifecycle under ERC-8183:

```
client createJob → registerJob → setBudget → fund
      └── agent's funded-job poll loop picks up FUNDED jobs
          └── on_job(job) returns a news report
              └── SDK builds DeliverableManifest, uploads to IPFS (Pinata),
                  pins as "erc8183-job-{id}", calls commerce.submit with keccak256 hash
      └── after the dispute window an operator (or any party) calls router.settle(jobId)
```

No manual UMA assertion / bond step — ERC-8183 uses the **OptimisticPolicy**:
silence approves after the dispute window, and any client-raised dispute must
reach a whitelisted-voter quorum to flip the verdict to REJECT.

## Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- A [Pinata](https://pinata.cloud) account with a JWT API key (only needed when using option (b) IPFS storage)

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
| `ERC8183_AGENT_URL` | Agent's public base URL including `/erc8183` (required for local storage, e.g. `http://localhost:8003/erc8183`) |
| `ERC8183_SERVICE_PRICE` | Minimum acceptable budget in raw units (e.g. `1000000000000000000` = 1 U) |

### Optional overrides

```
NETWORK=bsc-testnet             (default)
RPC_URL=                        custom RPC endpoint (recommended for rate-limit avoidance)
STORAGE_GATEWAY_URL=            IPFS gateway (default: https://gateway.pinata.cloud/ipfs/)
ERC8183_COMMERCE_ADDRESS=          override Commerce proxy
ERC8183_ROUTER_ADDRESS=            override Router proxy
ERC8183_POLICY_ADDRESS=            override OptimisticPolicy
ERC8183_FUNDED_POLL_INTERVAL=30    funded-job poll cadence (seconds)
ERC8183_NEGOTIATE_RATE_LIMIT=120   /negotiate per-IP request budget
ERC8183_NEGOTIATE_RATE_WINDOW=60   rate-limit window (seconds)
ERC8183_MAX_RESPONSE_BYTES=5242880 response_content cap (5 MB)
ERC8183_MAX_METADATA_BYTES=262144  metadata cap (256 KB)
```

### Storage backends

`src/service.py` (and `src/service_mount.py`) exposes two options as a
"pick ONE" comment block labelled **(a)** and **(b)**. The default is
option **(a)**; uncomment option **(b)** to switch.

| Option | Provider | On-chain `deliverable_url` | Required env vars |
|--------|----------|----------------------------|-------------------|
| **(a)** default | `LocalStorageProvider` | `{ERC8183_AGENT_URL}/job/{id}/response` | `ERC8183_AGENT_URL` |
| **(b)** | `IPFSStorageProvider` | `ipfs://CID` | `STORAGE_API_KEY` (Pinata JWT) |

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
  service.py             # create_erc8183_app() — ERC-8183 owns the app
  service_mount.py       # create_erc8183_app() + app.mount() — mount onto existing app
```

## ERC-8183 endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/erc8183/negotiate` | Price negotiation (rate-limited) |
| GET  | `/erc8183/job/{id}` | Job details |
| GET  | `/erc8183/job/{id}/response` | Stored deliverable response |
| GET  | `/erc8183/job/{id}/verify` | Job verification |
| GET  | `/erc8183/status` | Agent status (wallet, contracts, service price) |
| GET  | `/erc8183/health` | Health check |

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

- **(a) Local** — JSON written to `.agent-data/` (default). The SDK rewrites the
  `file://` URL to `{ERC8183_AGENT_URL}/job/{id}/response` before submitting;
  the agent serves the file via `GET /erc8183/job/{id}/response`.
- **(b) IPFS** — JSON pinned to IPFS via Pinata as `erc8183-job-{id}`. The `ipfs://CID`
  URL is stored on-chain and resolved via the configured gateway.

## Testing Without ERC-8183

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
