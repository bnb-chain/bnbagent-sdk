# Blockchain News Agent (APEX v1)

A production-like APEX provider agent that searches for blockchain news using
DuckDuckGo. Demonstrates the full provider lifecycle under APEX v1:

```
client createJob → registerJob → setBudget → fund
      └── agent startup-scan picks up FUNDED jobs
          └── on_job(job) returns a report
              └── SDK uploads to storage, calls commerce.submit
                  └── auto-settle loop calls router.settle after the dispute window
```

No manual UMA assertion / bond step — APEX v1 uses the **OptimisticPolicy**:
silence approves after the dispute window, and any client-raised dispute must
reach a whitelisted-voter quorum to flip the verdict to REJECT.

## Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env: set WALLET_PASSWORD (required); optionally PRIVATE_KEY,
# STORAGE_API_KEY, APEX_COMMERCE_ADDRESS / APEX_ROUTER_ADDRESS / APEX_POLICY_ADDRESS
# to override the bsc-testnet defaults baked into the SDK.
```

## Usage

Two integration patterns:

### 1. Standalone app — `service.py`

```bash
uv run python scripts/register.py   # One-time ERC-8004 registration
uv run python src/service.py
```

```python
from bnbagent.apex.server import create_apex_app

app = create_apex_app(config=config, on_job=process_task)
```

### 2. Mount onto existing FastAPI app — `service_mount.py`

```bash
uv run python src/service_mount.py
```

```python
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

app = FastAPI(title="My Existing App")
apex_app = create_apex_app(config=config, on_job=process_task, prefix="")
app.mount("/apex", apex_app)
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
| POST | /apex/negotiate | Price negotiation |
| POST | /apex/submit | Submit result |
| GET  | /apex/job/{id} | Job details |
| GET  | /apex/job/{id}/response | Stored deliverable response |
| GET  | /apex/job/{id}/verify | Job verification |
| POST | /apex/job/{id}/settle | Manual permissionless `router.settle` |
| GET  | /apex/status | Agent status |
| POST | /apex/job/execute | Client-initiated job execution (requires `on_job`) |
| GET  | /apex/health | Health check |

## Auto-settle

`create_apex_app(..., auto_settle=True)` (default) spawns a background loop
that polls `policy.check(jobId)` for this agent's submitted jobs and calls
`router.settle(jobId)` once the dispute window elapses. Settle is
permissionless, so clients don't have to do anything — the agent just gets
paid automatically. Tune with `auto_settle_interval=<seconds>`.

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
