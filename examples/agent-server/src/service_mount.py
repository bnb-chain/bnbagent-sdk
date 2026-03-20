"""
Blockchain News Agent — Mount on an existing FastAPI app.

Demonstrates mounting the APEX sub-app onto an existing FastAPI application
that already has its own endpoints and lifespan.

Compare with service.py which also uses create_apex_app() mounted on /apex.

Usage:
    cd examples/agent-server
    python src/service_mount.py

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

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ddgs import DDGS

# Load .env from project root (one level up from src/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# SDK imports
from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server import create_apex_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("blockchain_news")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

config = APEXConfig.from_env()
PORT = int(os.getenv("PORT", "8003"))

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
# Existing FastAPI app — your own endpoints, middleware, lifespan, etc.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Blockchain News Agent",
    description="News search agent with APEX payment protocol",
)


# ---------------------------------------------------------------------------
# Mount APEX sub-app onto the existing app
# ---------------------------------------------------------------------------

apex_app = create_apex_app(config=config, on_job=process_task, prefix="")
app.mount("/apex", apex_app)


# ---------------------------------------------------------------------------
# Your own endpoints (independent of APEX)
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


@app.get("/")
async def root():
    return {
        "service": "Blockchain News Agent",
        "agent_address": apex_app.state.apex.job_ops.agent_address,
        "endpoints": {
            "search": "/search",
            "apex_status": "/apex/status",
            "apex_health": "/apex/health",
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
  Blockchain News Agent (APEX — Mount Mode)
{'='*55}
  Port:           {PORT}
  ERC-8183:       {config.effective_erc8183_address}
  Evaluator:      {config.effective_evaluator_address}
  Storage:        {type(config.storage).__name__ if config.storage else "local (default)"}
  Price:          {int(config.service_price) / 10**18} U tokens

  APEX endpoints (initialized):
    POST /apex/negotiate   — Negotiation
    POST /apex/submit      — Submit result
    GET  /apex/job/{{id}}    — Job details
    GET  /apex/status      — Agent status

  App endpoints:
    GET  /              — Service info
    POST /search          — Direct news search
    GET  /apex/health     — Health check
{'='*55}
""")

    uvicorn.run(app, host="0.0.0.0", port=PORT)
