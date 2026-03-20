# Blockchain News Agent

A production-like APEX agent that searches for blockchain news using DuckDuckGo.

## How It Works

1. Agent registers on ERC-8004 identity registry
2. Clients create funded APEX jobs with search queries
3. Agent scans for funded jobs on startup, accepts /job/execute requests, submits results to IPFS
4. APEX Evaluator handles settlement after liveness period

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add PRIVATE_KEY and STORAGE_API_KEY
```

## Usage

Two entry points demonstrate different integration patterns:

### Option 1: Standalone app (`service.py`) — `create_apex_app()`

Creates a complete FastAPI app with APEX built in. Simplest approach for a dedicated agent.

```bash
# One-time: Register agent on-chain
python scripts/register.py

# Run the agent server (either way works)
python scripts/run_agent.py
python src/service.py
```

```python
from bnbagent.apex.server.routes import create_apex_app

app = create_apex_app(config=config, on_job=process_task)
```

### Option 2: Mount onto existing app (`service_mount.py`) — `create_apex_app()` + `app.mount()`

Creates an APEX sub-application and mounts it onto an existing FastAPI app. Use this when adding APEX to an app that already does other things.

```bash
python scripts/run_agent_mount.py
python src/service_mount.py
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
