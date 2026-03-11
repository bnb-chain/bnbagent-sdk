"""
Full Agent - Complete ACP agent with error handling.

Features:
- Job verification with error handling
- Automatic IPFS upload
- Retry logic for network errors
- Security warning detection

Setup:
    1. Create .env file (see below)
    2. Run: uvicorn full_agent:app --port 8000

Environment:
    RPC_URL              - Blockchain RPC endpoint
    ACP_ADDRESS          - ACP contract address
    PRIVATE_KEY          - Agent wallet private key
    STORAGE_PROVIDER     - "local" or "ipfs" (optional)
    PINATA_JWT           - Pinata JWT for IPFS (if STORAGE_PROVIDER=ipfs)
    AGENT_PRICE          - Default price in wei (optional)
"""

import os
import asyncio
import logging
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from bnbagent.quickstart import ACPConfig, ACPState
from bnbagent.quickstart.app import _create_state, create_acp_routes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

config = ACPConfig.from_env_optional()
state: Optional[ACPState] = None

if config:
    state = _create_state(config)
    logger.info(f"ACP enabled: agent={state.job_ops.agent_address}")
else:
    logger.warning("ACP not configured - running without ACP support")

# ============================================================================
# Application
# ============================================================================

app = FastAPI(
    title="Full ACP Agent",
    description="Complete ACP agent with error handling",
)

# Mount ACP routes if configured
if state:
    app.include_router(create_acp_routes(state=state), prefix="/acp")


# ============================================================================
# Retry Helper
# ============================================================================

async def with_retry(fn, max_retries: int = 3, base_delay: float = 1.0):
    """Execute function with exponential backoff retry."""
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return await fn() if asyncio.iscoroutinefunction(fn) else fn()
        except ConnectionError as e:
            last_error = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), 30.0)
                logger.warning(f"Retry {attempt + 1}/{max_retries} in {delay}s: {e}")
                await asyncio.sleep(delay)
        except Exception:
            raise
    
    raise last_error


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health")
async def health():
    """Health check."""
    result = {"status": "ok"}
    if state:
        result["agent_address"] = state.job_ops.agent_address
        result["acp_enabled"] = True
    else:
        result["acp_enabled"] = False
    return result


@app.post("/execute")
async def execute_job(request: Request):
    """
    Execute a job with full error handling.
    
    Request body:
        {
            "job_id": 123,
            "task": "Your task description"
        }
    """
    if not state:
        raise HTTPException(status_code=503, detail="ACP not configured")
    
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    job_id = body.get("job_id")
    task = body.get("task", "")
    
    if job_id is None:
        raise HTTPException(status_code=400, detail="job_id is required")
    
    job_id = int(job_id)
    
    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Verify job with retry
    # ─────────────────────────────────────────────────────────────────────
    
    try:
        verification = await with_retry(
            lambda: state.job_ops.verify_job(job_id)
        )
    except ConnectionError as e:
        logger.error(f"Network error verifying job {job_id}: {e}")
        raise HTTPException(status_code=503, detail="Network error, please retry")
    
    if not verification["valid"]:
        error_code = verification.get("error_code", 400)
        error_msg = verification["error"]
        
        error_messages = {
            403: "Not assigned to this job",
            404: "Job not found",
            408: "Job expired",
            409: "Job not in FUNDED status",
            503: "Network error",
        }
        
        detail = error_messages.get(error_code, error_msg)
        raise HTTPException(status_code=error_code, detail=detail)
    
    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Check security warnings
    # ─────────────────────────────────────────────────────────────────────
    
    warnings = verification.get("warnings", [])
    warning_messages = []
    
    for w in warnings:
        logger.warning(f"Job {job_id} warning: {w['code']} - {w['message']}")
        warning_messages.append(w["message"])
        
        # Optionally reject risky jobs
        # if w["code"] == "CLIENT_AS_EVALUATOR":
        #     raise HTTPException(
        #         status_code=400,
        #         detail="Rejected: Client is evaluator (security risk)"
        #     )
    
    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Execute task
    # ─────────────────────────────────────────────────────────────────────
    
    logger.info(f"Processing job {job_id}: {task[:100]}...")
    
    try:
        result = await process_task(task)
    except Exception as e:
        logger.error(f"Task processing failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Task processing failed: {e}")
    
    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Submit result with retry
    # ─────────────────────────────────────────────────────────────────────
    
    logger.info(f"Submitting result for job {job_id}...")
    
    try:
        submission = await with_retry(
            lambda: state.job_ops.submit_result(
                job_id=job_id,
                response_content=result,
                metadata={
                    "task": task[:200],
                    "model": "example-model",
                },
            )
        )
    except ConnectionError as e:
        logger.error(f"Network error submitting job {job_id}: {e}")
        raise HTTPException(status_code=503, detail="Network error during submission")
    except Exception as e:
        logger.error(f"Submit failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Submit failed: {e}")
    
    if not submission["success"]:
        error = submission["error"]
        logger.error(f"Submission rejected for job {job_id}: {error}")
        
        if "InvalidStatus" in error:
            raise HTTPException(status_code=409, detail="Job status changed")
        elif "Unauthorized" in error:
            raise HTTPException(status_code=403, detail="Not authorized")
        else:
            raise HTTPException(status_code=500, detail=error)
    
    # ─────────────────────────────────────────────────────────────────────
    # Success!
    # ─────────────────────────────────────────────────────────────────────
    
    logger.info(f"Job {job_id} submitted successfully: {submission['txHash']}")
    
    response = {
        "status": "submitted",
        "job_id": job_id,
        "tx_hash": submission["txHash"],
        "result_preview": result[:200] + "..." if len(result) > 200 else result,
    }
    
    if submission.get("dataUrl"):
        response["ipfs_url"] = submission["dataUrl"]
    
    if warning_messages:
        response["warnings"] = warning_messages
    
    return response


async def process_task(task: str) -> str:
    """
    Your agent's task processing logic.
    
    Replace this with your actual AI implementation.
    """
    # Simulate processing
    await asyncio.sleep(0.1)
    
    return f"Processed task: {task}\n\nThis is the result from the agent."


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors gracefully."""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    
    print(f"""
Full ACP Agent
==============
Port: {port}
ACP Enabled: {state is not None}
""")
    
    if state:
        print(f"Agent Address: {state.job_ops.agent_address}")
        print(f"ACP Contract:  {state.config.acp_address}")
    
    print("""
Endpoints:
  GET  /health    - Health check
  POST /execute   - Execute job with full error handling
  POST /acp/*     - ACP protocol endpoints
""")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
