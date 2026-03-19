# Blockchain News Agent

A production-like APEX agent that searches for blockchain news using DuckDuckGo.

## How It Works

1. Agent registers on ERC-8004 identity registry
2. Clients create funded APEX jobs with search queries
3. Agent polls for funded jobs, searches news, submits results to IPFS
4. APEX Evaluator handles settlement after liveness period

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add PRIVATE_KEY and STORAGE_API_KEY
```

## Usage

Three entry points demonstrate different integration patterns — from one-line setup to full manual control:

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

### Option 2: Mount on existing app (`service_mount.py`) — `APEX(...).mount(app)`

Mounts APEX onto an existing FastAPI app that has its own routes and lifecycle. Use this when adding APEX to an app that already does other things.

```bash
python scripts/run_agent_mount.py
python src/service_mount.py
```

```python
from fastapi import FastAPI
from bnbagent.apex.server import APEX

app = FastAPI(title="My Existing App")

apex = APEX(config=config, on_job=process_task)
apex.mount(app, prefix="/apex")

# Your own routes work alongside APEX
@app.get("/search")
async def search(): ...
```

### Option 3: Full manual control (`service_routes.py`) — `create_apex_routes()`

Wires routes, middleware, and job loop manually. Use this when you need full control over each layer — for example, custom middleware ordering, shared state across routers, or a non-standard job loop lifecycle.

```bash
python scripts/run_agent_routes.py
python src/service_routes.py
```

```python
from fastapi import FastAPI
from bnbagent.apex.server.routes import create_apex_routes, create_apex_state
from bnbagent.apex.server.middleware import APEXMiddleware, DEFAULT_SKIP_PATHS
from bnbagent.apex.server.job_ops import run_job_loop

state = create_apex_state(config)
app = FastAPI()

# 1. Routes
router = create_apex_routes(state=state)
app.include_router(router, prefix="/apex")

# 2. Middleware (must add prefixed skip paths manually)
skip_paths = list(DEFAULT_SKIP_PATHS) + [f"/apex{p}" for p in DEFAULT_SKIP_PATHS]
app.add_middleware(APEXMiddleware, job_ops=state.job_ops, skip_paths=skip_paths)

# 3. Job loop (manage lifecycle yourself)
@app.on_event("startup")
async def start():
    asyncio.create_task(run_job_loop(job_ops=state.job_ops, on_job=process_task))
```

All three approaches produce the same APEX endpoints and behavior — the difference is how much the SDK manages for you.

### File structure

```
scripts/
  register.py            # One-time ERC-8004 registration
  run_agent.py           # Run standalone app (service.py)
  run_agent_mount.py     # Run mount mode (service_mount.py)
  run_agent_routes.py    # Run manual routes mode (service_routes.py)
src/
  service.py             # create_apex_app() — APEX owns the app
  service_mount.py       # APEX(...).mount(app) — mount onto existing app
  service_routes.py      # create_apex_routes() — full manual control
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
| GET | /apex/health | Health check |

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
