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

import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env from this script's directory
load_dotenv(Path(__file__).resolve().parent / ".env")

from bnbagent.apex.server import create_apex_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# ---------------------------------------------------------------------------
# Task handler — replace this with your real AI logic
# ---------------------------------------------------------------------------

def process_task(job: dict) -> str:
    """
    Process a funded APEX job and return a result string.

    The SDK calls this automatically for each funded job.
    Replace this with your real AI logic.

    Args:
        job: Full job dict with keys: jobId, description, budget,
             client, provider, evaluator, status, expiredAt, etc.

    Returns:
        Result string to submit on-chain.
    """
    description = job.get("description", "")
    return (
        f"Getting Started Agent processed your request:\n\n"
        f"Task: {description}\n\n"
        f"Result: This is a demo response from the getting-started agent. "
        f"Replace process_task() with your real AI logic."
    )


# ---------------------------------------------------------------------------
# App — one call does everything: routes + polling + lifecycle
# ---------------------------------------------------------------------------

app = create_apex_app(
    on_job=process_task,
    task_metadata={"agent": "getting-started"},
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
