"""
Blockchain News Agent — APEX Protocol Provider.

Built with bnbagent-sdk.

A news search agent that:
  1. Receives search queries from clients via APEX
  2. Searches DuckDuckGo for blockchain news
  3. Returns formatted news results

Usage:
    cd agents
    uv run python -m agent_server.service

Environment (agent-server/.env):
    RPC_URL, NETWORK                           — Required (RPC + network key)
    PRIVATE_KEY                                — Recommended (imported on first run; auto-generates if omitted)
    WALLET_PASSWORD                            — Required (keystore password)
    APEX_COMMERCE_ADDRESS, APEX_ROUTER_ADDRESS, APEX_POLICY_ADDRESS — Optional overrides (defaults from NETWORK)
    STORAGE_PROVIDER=ipfs, STORAGE_API_KEY      — Required for IPFS upload
    APEX_SERVICE_PRICE=1000000000000000000      — Negotiation price (1 U)
    PORT=8003                                   — Server port
    APEX_EXEC_TIMEOUT=120                       — /job/execute callback timeout (seconds)
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import BaseModel
from ddgs import DDGS

# Load .env from project root (one level up from src/)
env_file = os.path.basename(os.environ.get("ENV_FILE", ".env"))
load_dotenv(Path(__file__).resolve().parent.parent / env_file)

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

    try:
        results = list(ddgs.news(query, max_results=max_results))
        if not results:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        logger.warning(f"DDGS search failed: {e}")
        return []


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
    from bnbagent.apex import JobDescription

    raw_description = job.get("description", "blockchain news")
    parsed = JobDescription.from_str(raw_description)
    query = parsed.task if parsed else raw_description
    logger.info(f"Searching news for: {query[:80]}...")

    raw_results = search_news(query, max_results=10)
    logger.info(f"Found {len(raw_results)} news items")

    report = format_news_results(query, raw_results)
    return report, {"agent": "blockchain-news", "query": query}


# ---------------------------------------------------------------------------
# App — create_apex_app handles routes, startup scan, and lifecycle
# ---------------------------------------------------------------------------

app = create_apex_app(config=config, on_job=process_task)

# ---------------------------------------------------------------------------
# Startup banner — printed at import time so it shows regardless of how
# the server is launched (run_agent.py, uvicorn CLI, __main__, etc.)
# ---------------------------------------------------------------------------
from bnbagent.storage.ipfs_provider import IPFSStorageProvider as _IPFS

_storage_info = "local (default)"
if isinstance(config.storage, _IPFS):
    _storage_info = f"IPFS via Pinata  (gateway: {config.storage._gateway})"
elif config.storage:
    _storage_info = type(config.storage).__name__

print(f"""
{'='*55}
  Blockchain News Agent (APEX Provider)
{'='*55}
  Port:           {PORT}
  Commerce:       {config.effective_commerce_address}
  Router:         {config.effective_router_address}
  Policy:         {config.effective_policy_address}
  Storage:        {_storage_info}
  Price:          {int(config.service_price) / 10**18} U tokens

  APEX endpoints:
    POST /apex/negotiate          — Negotiation
    POST /apex/submit             — Submit result
    GET  /apex/job/{{id}}           — Job details
    POST /apex/job/{{id}}/settle    — Manual settle
    GET  /apex/status             — Agent status

  Direct endpoints (testing):
    POST /search          — Direct news search
    GET  /apex/health     — Health check
{'='*55}
""")


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
# Direct HTTP endpoints (for testing without APEX)
# ---------------------------------------------------------------------------


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

    uvicorn.run(app, host="0.0.0.0", port=PORT)
