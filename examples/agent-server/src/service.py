"""
Blockchain News Agent — ERC-8183 Protocol Provider.

Built with bnbagent-sdk.

A news search agent that:
  1. Receives search queries from clients via ERC-8183
  2. Searches DuckDuckGo for blockchain news
  3. Returns formatted news results

Usage:
    cd agents
    uv run python -m agent_server.service

Environment (agent-server/.env):
    RPC_URL, NETWORK                           — Required (RPC + network key)
    PRIVATE_KEY                                — Recommended (imported on first run; auto-generates if omitted)
    WALLET_PASSWORD                            — Required (keystore password)
    ERC8183_COMMERCE_ADDRESS, ERC8183_ROUTER_ADDRESS, ERC8183_POLICY_ADDRESS — Optional overrides (defaults from NETWORK)
    STORAGE_API_KEY      — Required for IPFS upload (when swapping to IPFSStorageProvider)
    ERC8183_AGENT_URL=http://localhost:8003/erc8183  — Required for LocalStorageProvider
    ERC8183_SERVICE_PRICE=1000000000000000000      — Negotiation price (1 U)
    PORT=8003                                   — Server port
    ERC8183_FUNDED_POLL_INTERVAL=30                — Funded-job poll interval (seconds)
    ERC8183_NEGOTIATE_RATE_LIMIT=120               — /negotiate per-IP rate limit (requests)
    ERC8183_NEGOTIATE_RATE_WINDOW=60               — /negotiate rate-limit window (seconds)
    ERC8183_MAX_RESPONSE_BYTES=5242880             — submit_result response_content cap (5 MB)
    ERC8183_MAX_METADATA_BYTES=262144              — submit_result metadata cap (256 KB)
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
from bnbagent.erc8183.config import ERC8183Config
from bnbagent.erc8183.server import create_erc8183_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("blockchain_news")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Storage backend — pick ONE of the two options below by uncommenting it.

# (a) Local filesystem (default)
from bnbagent.storage import LocalStorageProvider
_storage = LocalStorageProvider.from_env()

# (b) IPFS via Pinata — set STORAGE_API_KEY (Pinata JWT) in .env first.
# from bnbagent.storage import IPFSStorageProvider
# _storage = IPFSStorageProvider.from_env()

config = ERC8183Config.from_env(storage=_storage)
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
# ERC-8183 task handler — the ONLY function you need to write
# ---------------------------------------------------------------------------


def process_task(job: dict) -> tuple[str, dict]:
    """
    Process a funded ERC-8183 job and return the result.

    The SDK calls this for each funded job automatically.
    Receives the full job dict, returns (result_string, metadata).
    """
    from bnbagent.erc8183 import JobDescription

    raw_description = job.get("description", "blockchain news")
    parsed = JobDescription.from_str(raw_description)
    query = parsed.task if parsed else raw_description
    logger.info(f"Searching news for: {query[:80]}...")

    raw_results = search_news(query, max_results=10)
    logger.info(f"Found {len(raw_results)} news items")

    report = format_news_results(query, raw_results)
    return report, {"agent": "blockchain-news", "query": query}


# ---------------------------------------------------------------------------
# App — create_erc8183_app handles routes, the funded-job poll loop, and lifecycle
# ---------------------------------------------------------------------------

app = create_erc8183_app(config=config, on_job=process_task)

# ---------------------------------------------------------------------------
# Startup banner — printed at import time so it shows regardless of how
# the server is launched (run_agent.py, uvicorn CLI, __main__, etc.)
# ---------------------------------------------------------------------------
_storage_info = type(_storage).__name__

print(f"""
{'='*55}
  Blockchain News Agent (ERC-8183 Provider)
{'='*55}
  Port:           {PORT}
  Commerce:       {config.effective_commerce_address}
  Router:         {config.effective_router_address}
  Policy:         {config.effective_policy_address}
  Storage:        {_storage_info}
  Price:          {int(config.service_price) / 10**18} U tokens

  ERC-8183 endpoints:
    POST /erc8183/negotiate          — Negotiation
    GET  /erc8183/job/{{id}}           — Job details
    GET  /erc8183/status             — Agent status

  Direct endpoints (testing):
    POST /search          — Direct news search
    GET  /erc8183/health     — Health check
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
# Direct HTTP endpoints (for testing without ERC-8183)
# ---------------------------------------------------------------------------


@app.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest):
    """
    Direct HTTP search endpoint (for testing).
    For production, use ERC-8183 protocol via /erc8183/* endpoints.
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
