# Blockchain News Agent

A production-like APEX agent that searches for blockchain news using DuckDuckGo.

## How It Works

1. Agent registers on ERC-8004 identity registry
2. Clients create funded APEX jobs with search queries
3. Agent scans for funded jobs on startup, accepts `/job/execute` requests
4. For each job, the SDK automatically:
   - Executes the `on_job` callback (news search)
   - Uploads the result to IPFS
   - Submits the deliverable hash on-chain (`submit`)
   - Checks bond readiness (balance + allowance, 0 gas)
   - Approves bond token to evaluator (only if needed)
   - Initiates UMA assertion (`initiateAssertion`) — starts the dispute window
5. After the liveness period, anyone can call `settleJob()` to complete the job and return the bond

### Bond requirement

The agent wallet must hold **bond tokens** (the ERC-20 token returned by
`evaluator.bondToken()`). The minimum amount is `evaluator.getMinimumBond()`
(currently 0.1 token on testnet). The bond is locked during the UMA liveness
period and returned to the agent after clean settlement.

The SDK handles `approve` + `initiateAssertion` automatically. You only need to
ensure the wallet has enough bond tokens. Check via logs:

```
[APEXJobOps] bond readiness: balance=8072900000000000000000, allowance=100000000000000000, min=100000000000000000
[APEXJobOps] initiate_assertion(18) tx: 0x...
```

If the wallet has insufficient tokens, the log will show:

```
[APEXJobOps] initiate_assertion(18) failed: Provider has insufficient bond tokens: have 0, need 100000000000000000 of 0xc70B...
```

## Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env
# Edit .env: add PRIVATE_KEY and STORAGE_API_KEY
```

## Usage

Two entry points demonstrate different integration patterns:

### Option 1: Standalone app (`service.py`) — `create_apex_app()`

Creates a complete FastAPI app with APEX built in. Simplest approach for a dedicated agent.

```bash
# One-time: Register agent on-chain
uv run python scripts/register.py

# Run the agent server (either way works)
uv run python scripts/run_agent.py
uv run python src/service.py
```

```python
from bnbagent.apex.server.routes import create_apex_app

app = create_apex_app(config=config, on_job=process_task)
```

### Option 2: Mount onto existing app (`service_mount.py`) — `create_apex_app()` + `app.mount()`

Creates an APEX sub-application and mounts it onto an existing FastAPI app. Use this when adding APEX to an app that already does other things.

```bash
uv run python scripts/run_agent_mount.py
uv run python src/service_mount.py
```

```python
from fastapi import FastAPI
from bnbagent.apex.server import create_apex_app

app = FastAPI(title="My Existing App")

apex_app = create_apex_app(config=config, on_job=process_task)
app.mount("/apex", apex_app)

# Your own routes work alongside APEX
@app.get("/search")
async def search(): ...
```

Both approaches produce the same APEX endpoints and behavior — the difference is whether APEX owns the app or is mounted onto yours.

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

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /apex/negotiate | Price negotiation |
| POST | /apex/submit | Submit result |
| GET | /apex/job/{id} | Job details |
| GET | /apex/job/{id}/response | Stored deliverable response |
| GET | /apex/job/{id}/verify | Job verification |
| GET | /apex/status | Agent status |
| POST | /search | Direct news search (testing) |
| POST | /apex/job/execute | Client-initiated job execution (available when `on_job` is provided) |
| GET | /apex/health | Health check |

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
