"""
Step 3: Run Agent Server

Starts a minimal APEX agent server that:
  - Exposes APEX endpoints (negotiate, submit, job query)
  - Polls for funded jobs in the background
  - Automatically processes and submits results

Keep this running in Terminal 1, then open Terminal 2 for step4.

Prerequisites:
    - Completed step1 (wallet funded) and step2 (agent registered)

Usage:
    python step3_run_agent.py

Next: step4_create_job.py (in a separate terminal)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from this script's directory
load_dotenv(Path(__file__).resolve().parent / ".env")

from bnbagent.quickstart import APEXConfig, create_apex_state, create_apex_routes

from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("quickstart-agent")

# ---------------------------------------------------------------------------
# Configuration — single shared state for both routes and polling
# ---------------------------------------------------------------------------

config = APEXConfig.from_env()
state = create_apex_state(config)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))


# ---------------------------------------------------------------------------
# Task processing — replace this with your real AI logic
# ---------------------------------------------------------------------------

def process_task(description: str) -> str:
    """
    Process a task and return a result.

    This is where you put your AI logic. For this quickstart,
    we return a simple echo response.
    """
    return (
        f"Quickstart Agent processed your request:\n\n"
        f"Task: {description}\n\n"
        f"Result: This is a demo response from the quickstart agent. "
        f"Replace process_task() with your real AI logic."
    )


# ---------------------------------------------------------------------------
# Background polling — pick up funded jobs, process, submit
# ---------------------------------------------------------------------------

async def poll_funded_jobs():
    """Poll for FUNDED jobs assigned to this agent and auto-submit results."""
    logger.info(f"Polling for funded jobs every {POLL_INTERVAL}s...")
    my_address = state.job_ops.agent_address
    logger.info(f"Agent address: {my_address}")

    while True:
        try:
            result = await state.job_ops.get_pending_jobs()

            if not result.get("success"):
                logger.warning(f"get_pending_jobs error: {result.get('error')}")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            jobs = result.get("jobs", [])
            if jobs:
                logger.info(f"Found {len(jobs)} pending job(s)")

            for job in jobs:
                job_id = job["jobId"]
                description = job.get("description", "")
                logger.info(f"Processing job #{job_id}: {description[:60]}...")

                # Verify job before processing
                verification = await state.job_ops.verify_job(job_id)
                if not verification["valid"]:
                    logger.warning(f"Job #{job_id} verification failed: {verification.get('error')}")
                    continue

                # Process the task
                try:
                    response = process_task(description)
                except Exception as e:
                    logger.error(f"Task processing failed for job #{job_id}: {e}")
                    continue

                # Submit result on-chain
                logger.info(f"Submitting result for job #{job_id}...")
                submission = await state.job_ops.submit_result(
                    job_id=job_id,
                    response_content=response,
                    metadata={"agent": "quickstart"},
                )

                if submission.get("success"):
                    logger.info(f"Job #{job_id} submitted! TX: {submission['txHash']}")
                else:
                    logger.error(f"Job #{job_id} submission failed: {submission.get('error')}")

        except Exception as e:
            logger.error(f"Polling error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# App — use lifespan for background tasks, share single state with routes
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_funded_jobs())
    yield
    task.cancel()


app = FastAPI(title="Quickstart Agent", lifespan=lifespan)
app.include_router(create_apex_routes(config=config, state=state))


@app.post("/task")
async def handle_task(request: Request):
    """Direct task endpoint (for testing without APEX)."""
    body = await request.json()
    task = body.get("task", "")
    result = process_task(task)
    return {"status": "success", "result": result}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Quickstart Agent"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(f"""
{'='*55}
  Quickstart Agent (Step 3)
{'='*55}
  Agent Address:  {state.job_ops.agent_address}
  ERC-8183 Contract: {config.erc8183_address}
  Evaluator:      {config.apex_evaluator_address}
  Storage:        {config.storage_provider}
  Poll Interval:  {POLL_INTERVAL}s

  APEX endpoints:
    POST /negotiate        — Negotiation
    POST /submit           — Submit result
    GET  /job/{{id}}         — Job details
    GET  /job/{{id}}/verify  — Verify job
    GET  /status           — Agent status

  Other endpoints:
    POST /task             — Direct task (testing)
    GET  /health           — Health check

  Polling for funded jobs every {POLL_INTERVAL}s...
{'='*55}
""")

    uvicorn.run(app, host="0.0.0.0", port=8000)
