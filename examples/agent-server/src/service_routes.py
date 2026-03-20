"""
Blockchain News Agent — Full manual control with create_apex_routes().

Demonstrates manual wiring of APEX routes, middleware, and job loop.
Use this approach when you need full control over each layer — for example,
custom middleware ordering, shared state across routers, or a non-standard
job loop lifecycle.

Compare with:
  - service.py          → create_apex_app() (one-line setup)
  - service_mount.py    → APEX(...).mount()  (mount on existing app)

Usage:
    cd examples/agent-server
    python src/service_routes.py

Environment (agent-server/.env):
    RPC_URL, ERC8183_ADDRESS                   — Required (ERC-8183)
    PRIVATE_KEY                                — Recommended (imported on first run)
    APEX_EVALUATOR_ADDRESS                     — Required (evaluator)
    STORAGE_PROVIDER=ipfs, STORAGE_API_KEY     — Required (IPFS upload)
    SERVICE_PRICE=1000000000000000000           — Negotiation price (1 U)
    PAYMENT_TOKEN_ADDRESS                      — BEP20 payment token
    PORT=8003                                  — Server port
    POLL_INTERVAL=15                           — Job polling interval
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ddgs import DDGS

# Load .env from project root (one level up from src/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# SDK imports
from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server.routes import create_apex_routes, create_apex_state
from bnbagent.apex.server.middleware import APEXMiddleware, DEFAULT_SKIP_PATHS
from bnbagent.apex.server.job_ops import run_job_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("blockchain_news")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

config = APEXConfig.from_env()
state = create_apex_state(config)
PORT = int(os.getenv("PORT", "8003"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
PREFIX = "/apex"

# ---------------------------------------------------------------------------
# Core news search function
# ---------------------------------------------------------------------------


def search_news(query: str, max_results: int = 10) -> list[dict]:
    """Search news using DuckDuckGo."""
    ddgs = DDGS()

    results = list(ddgs.news(query, max_results=max_results))
    if not results:
        results = list(ddgs.text(query, max_results=max_results))

    return results


def format_news_results(query: str, raw_results: list[dict]) -> str:
    """Format news results into a readable report."""
    if not raw_results:
        return f"No news found for query: {query}"

    report = f"# Blockchain News Search Results\n\n"
    report += f"**Query:** {query}\n"
    report += f"**Results:** {len(raw_results)} items\n\n"
    report += "---\n\n"

    for i, r in enumerate(raw_results, 1):
        title = r.get("title", "No title")
        body = r.get("body", r.get("snippet", ""))
        url = r.get("url", r.get("href", ""))
        date = r.get("date", "")
        source = r.get("source", "")

        report += f"## {i}. {title}\n\n"
        if source or date:
            report += f"*{source}*"
            if date:
                report += f" | {date}"
            report += "\n\n"
        report += f"{body}\n\n"
        if url:
            report += f"[Read more]({url})\n\n"
        report += "---\n\n"

    return report


# ---------------------------------------------------------------------------
# APEX task handler — the ONLY function you need to write
# ---------------------------------------------------------------------------


def process_task(job: dict) -> tuple[str, dict]:
    """
    Process a funded APEX job and return the result.

    The SDK calls this for each funded job automatically.
    Receives the full job dict, returns (result_string, metadata).
    """
    query = job.get("description", "blockchain news")
    logger.info(f"Searching news for: {query[:80]}...")

    raw_results = search_news(query, max_results=10)
    logger.info(f"Found {len(raw_results)} news items")

    report = format_news_results(query, raw_results)
    return report, {"agent": "blockchain-news", "query": query}


# ---------------------------------------------------------------------------
# Manual wiring — you control each layer independently
# ---------------------------------------------------------------------------

# Job loop lifecycle — manage start/stop yourself
job_loop_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global job_loop_task
    job_loop_task = asyncio.create_task(
        run_job_loop(
            job_ops=state.job_ops,
            on_job=process_task,
            poll_interval=POLL_INTERVAL,
        )
    )
    logger.info(f"[JobLoop] Started: poll_interval={POLL_INTERVAL}s")
    yield
    if job_loop_task:
        job_loop_task.cancel()
        try:
            await job_loop_task
        except asyncio.CancelledError:
            pass
    logger.info("[JobLoop] Stopped")


app = FastAPI(
    title="Blockchain News Agent",
    description="News search agent with APEX payment protocol (manual routes mode)",
    lifespan=lifespan,
)

# 1. Routes — mount the APEX API router with your chosen prefix
router = create_apex_routes(state=state)
app.include_router(router, prefix=PREFIX)

# 2. Middleware — add APEXMiddleware with prefixed skip paths
#    DEFAULT_SKIP_PATHS contains bare paths like "/negotiate".
#    When using a prefix, you must also add the prefixed versions.
skip_paths = list(DEFAULT_SKIP_PATHS) + [f"{PREFIX}{p}" for p in DEFAULT_SKIP_PATHS]
app.add_middleware(APEXMiddleware, job_ops=state.job_ops, skip_paths=skip_paths)


# ---------------------------------------------------------------------------
# Pydantic models for direct /search endpoint
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    max_results: int = 10


class NewsItem(BaseModel):
    title: str
    body: str
    url: str
    date: str
    source: str


class SearchResponse(BaseModel):
    success: bool
    query: str
    results_count: int
    results: list[NewsItem]


# ---------------------------------------------------------------------------
# Your own endpoints (independent of APEX)
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    return {
        "service": "Blockchain News Agent",
        "mode": "manual routes (create_apex_routes)",
        "agent_address": state.job_ops.agent_address,
        "endpoints": {
            "search": "/search",
            "apex_status": f"{PREFIX}/status",
            "apex_health": f"{PREFIX}/health",
        },
    }


@app.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest):
    """
    Direct HTTP search endpoint (for testing).
    For production, use APEX protocol via /apex/* endpoints.
    """
    try:
        raw_results = search_news(request.query, request.max_results)

        results = []
        for r in raw_results:
            results.append(
                NewsItem(
                    title=r.get("title", ""),
                    body=r.get("body", r.get("snippet", "")),
                    url=r.get("url", r.get("href", "")),
                    date=r.get("date", ""),
                    source=r.get("source", ""),
                )
            )

        return SearchResponse(
            success=True,
            query=request.query,
            results_count=len(results),
            results=results,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(f"""
{'='*55}
  Blockchain News Agent (APEX — Manual Routes Mode)
{'='*55}
  Port:           {PORT}
  ERC-8183:       {config.effective_erc8183_address}
  Evaluator:      {config.effective_evaluator_address}
  Storage:        {type(config.storage).__name__ if config.storage else "local (default)"}
  Price:          {int(config.service_price) / 10**18} U tokens
  Poll interval:  {POLL_INTERVAL}s

  APEX endpoints (manually mounted):
    POST {PREFIX}/negotiate   — Negotiation
    POST {PREFIX}/submit      — Submit result
    GET  {PREFIX}/job/{{id}}    — Job details
    GET  {PREFIX}/status      — Agent status

  App endpoints:
    GET  /              — Service info
    POST /search          — Direct news search
    GET  {PREFIX}/health     — Health check
{'='*55}
""")

    uvicorn.run(app, host="0.0.0.0", port=PORT)
