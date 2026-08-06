"""Start the Blockchain News Agent server with IPFS storage.

Usage (from examples/agent-server/):
    uv run python scripts/run_agent.py
    uv run python scripts/run_agent.py --env .env.qa

Storage:
    Set STORAGE_PROVIDER=ipfs + STORAGE_API_KEY in .env to upload
    deliverable manifests to Pinata IPFS on every job submit.
    Defaults to local file storage if not configured.
"""

import argparse
import os
import sys
from pathlib import Path

# ensure print() output is not buffered so the startup banner appears immediately
os.environ.setdefault("PYTHONUNBUFFERED", "1")

# src/ on the import path so "from service import app" works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env", help="env file name (relative to agent-server/)")
    args = parser.parse_args()

    # pass env file choice to service.py via env var before it loads dotenv
    os.environ.setdefault("ENV_FILE", args.env)

    from service import app, PORT

    uvicorn.run(app, host="0.0.0.0", port=PORT)
