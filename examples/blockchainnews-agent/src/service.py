"""
Blockchain News Agent — APEX (ERC-8183) Provider.

Built with bnbagent-sdk.

A news search agent that:
  1. Receives search queries from clients via APEX
  2. Searches DuckDuckGo for blockchain news
  3. Returns formatted news results

Usage:
    cd agents
    uv run python -m blockchain_news.service

Environment (blockchain_news/.env):
    RPC_URL, ERC8183_ADDRESS, PRIVATE_KEY      — Required (ERC-8183)
    APEX_EVALUATOR_ADDRESS                     — Required (evaluator)
    STORAGE_PROVIDER=ipfs, PINATA_JWT          — Required (IPFS upload)
    AGENT_PRICE=1000000000000000000            — Negotiation price (1 U)
    PAYMENT_TOKEN_ADDRESS                      — BEP20 payment token
    PORT=8003                                  — Server port
    POLL_INTERVAL=15                           — Job polling interval
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ddgs import DDGS

# Load .env from project root (one level up from src/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# SDK imports — same pattern as bnbagent-sdk/examples/full_agent.py
from bnbagent.quickstart import APEXConfig, APEXState, create_apex_state, create_apex_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("blockchain_news")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

config = APEXConfig.from_env()
state: APEXState = create_apex_state(config)

PORT = int(os.getenv("PORT", "8003"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

logger.info(f"APEX enabled: agent={state.job_ops.agent_address}")

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
# App — SDK quickstart routes mounted on FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Blockchain News Agent",
    description="News search service with ERC-8183 APEX protocol support",
)
app.include_router(create_apex_routes(state=state), prefix="/apex")


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
# Direct HTTP endpoint (for testing without APEX)
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


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "blockchain-news",
        "apex_enabled": True,
        "agent_address": state.job_ops.agent_address,
    }


# ---------------------------------------------------------------------------
# APEX Task Processing
# ---------------------------------------------------------------------------


async def process_task(query: str) -> str:
    """Process a news search task from APEX."""
    logger.info(f"Searching news for: {query[:80]}...")

    raw_results = search_news(query, max_results=10)
    logger.info(f"Found {len(raw_results)} news items")

    report = format_news_results(query, raw_results)
    return report


# ---------------------------------------------------------------------------
# Background polling loop — verify → process → submit
# ---------------------------------------------------------------------------


async def poll_funded_jobs():
    """Poll for FUNDED jobs assigned to this agent and process them."""
    logger.info(f"Polling for funded jobs every {POLL_INTERVAL}s...")
    my_address = state.job_ops.agent_address
    logger.info(f"My address: {my_address}")

    while True:
        try:
            logger.info("Polling for funded jobs...")
            result = await state.job_ops.get_pending_jobs()

            if not result.get("success"):
                logger.warning(f"get_pending_jobs error: {result.get('error')}")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            jobs = result.get("jobs", [])
            logger.info(f"Found {len(jobs)} pending job(s)")

            for job in jobs:
                job_id = job["jobId"]
                description = job.get("description", "")
                logger.info(f"Found FUNDED job #{job_id}: {description[:60]}...")

                verification = await state.job_ops.verify_job(job_id)
                if not verification["valid"]:
                    logger.warning(
                        f"Job #{job_id} verification failed: {verification.get('error')}"
                    )
                    continue

                try:
                    news_report = await process_task(description)
                except Exception as e:
                    logger.error(f"Task processing failed for job #{job_id}: {e}")
                    continue

                logger.info(f"Submitting result for job #{job_id}...")
                submission = await state.job_ops.submit_result(
                    job_id=job_id,
                    response_content=news_report,
                    metadata={
                        "agent": "blockchain-news",
                        "query": description,
                    },
                )

                if submission.get("success"):
                    logger.info(f"Job #{job_id} submitted! TX: {submission['txHash']}")
                    if submission.get("dataUrl"):
                        logger.info(f"  IPFS: {submission['dataUrl']}")
                else:
                    logger.error(
                        f"Job #{job_id} submission failed: {submission.get('error')}"
                    )

        except Exception as e:
            logger.error(f"Polling error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_funded_jobs())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(f"""
{'='*55}
  Blockchain News Agent (APEX Provider)
{'='*55}
  Port:           {PORT}
  Agent Address:  {state.job_ops.agent_address}
  ERC-8183:       {config.erc8183_address}
  Evaluator:      {config.apex_evaluator_address}
  Storage:        {config.storage_provider}
  Price:          {int(config.agent_price) / 10**18} U tokens

  APEX endpoints:
    POST /apex/negotiate   — Negotiation
    POST /apex/submit      — Submit result
    GET  /apex/job/{{id}}    — Job details
    GET  /apex/status      — Agent status

  Direct endpoints (testing):
    POST /search          — Direct news search
    GET  /health          — Health check

  Polling for funded jobs every {POLL_INTERVAL}s...
{'='*55}
""")

    uvicorn.run(app, host="0.0.0.0", port=PORT)
