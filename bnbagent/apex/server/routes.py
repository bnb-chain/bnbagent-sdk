"""
FastAPI application factory for APEX agents.

Provides:
- create_apex_app(): Create a complete FastAPI app with APEX endpoints
- create_apex_routes(): Create an APIRouter to mount in existing apps
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import APEXConfig
from .job_ops import APEXJobOps
from ...storage import storage_provider_from_env, LocalStorageProvider
from ..negotiation import NegotiationHandler

logger = logging.getLogger(__name__)


@dataclass
class APEXState:
    """
    Shared state for APEX operations.
    
    Initialized once and shared across all route handlers.
    """
    config: APEXConfig
    job_ops: APEXJobOps
    negotiation_handler: NegotiationHandler
    
    def __repr__(self) -> str:
        """Safe repr that hides sensitive data."""
        return (
            f"APEXState("
            f"agent_address='{self.job_ops.agent_address}', "
            f"erc8183_address='{self.config.erc8183_address}')"
        )


def _create_storage_provider(config: APEXConfig):
    """Create storage provider based on config."""
    if config.storage_provider == "ipfs":
        if config.pinata_jwt:
            from ...storage import IPFSStorageProvider
            return IPFSStorageProvider(
                pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
                pinning_api_key=config.pinata_jwt,
                gateway_url=config.pinata_gateway or "https://gateway.pinata.cloud/ipfs/",
            )
    return LocalStorageProvider(config.local_storage_path)


def create_apex_state(config: APEXConfig) -> APEXState:
    """Create APEXState with all necessary components.

    Use this when you need an APEXState for custom app setups
    (e.g., mounting routes on an existing FastAPI app with polling).

    Args:
        config: APEXConfig instance (use APEXConfig.from_env() for env-based config)

    Returns:
        APEXState with job_ops and negotiation_handler initialized.
    """
    storage = _create_storage_provider(config)
    
    job_ops = APEXJobOps(
        rpc_url=config.rpc_url,
        erc8183_address=config.erc8183_address,
        private_key=config.private_key,
        storage_provider=storage,
        chain_id=config.chain_id,
    )
    
    negotiation_handler = NegotiationHandler(
        base_price=config.agent_price,
        currency=config.payment_token_address or "",
    )
    
    return APEXState(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
    )



def create_apex_routes(
    config: Optional[APEXConfig] = None,
    state: Optional[APEXState] = None,
    on_submit: Optional[Callable[[int, str, Dict], Any]] = None,
) -> APIRouter:
    """
    Create an APIRouter with APEX endpoints.
    
    Can be mounted to an existing FastAPI app:
    
        app.include_router(create_apex_routes(), prefix="/apex")
    
    Args:
        config: APEXConfig instance (default: loads from env)
        state: Pre-created APEXState (default: creates from config)
        on_submit: Optional callback after successful submit. 
                   Called with (job_id, response_content, metadata)
    
    Returns:
        APIRouter with /submit, /job/{id}, /job/{id}/verify, /negotiate endpoints
    """
    # Resolve config and state
    if state is None:
        if config is None:
            config = APEXConfig.from_env()
        state = create_apex_state(config)
    
    router = APIRouter(tags=["APEX"])
    
    @router.post("/submit")
    async def submit_result(request: Request):
        """
        Submit job result on-chain.
        
        Request body:
            {
                "job_id": int,
                "response_content": string,
                "metadata": object (optional)
            }
        
        Returns:
            {
                "success": bool,
                "txHash": string (if successful),
                "dataUrl": string (if storage configured),
                "error": string (if failed)
            }
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        
        job_id = body.get("job_id")
        response_content = body.get("response_content", "")
        metadata = body.get("metadata")
        
        if job_id is None:
            return JSONResponse({"error": "job_id is required"}, status_code=400)
        
        result = await state.job_ops.submit_result(
            job_id=int(job_id),
            response_content=response_content,
            metadata=metadata,
        )
        
        # Call callback if provided and successful
        if result.get("success") and on_submit:
            try:
                on_submit(int(job_id), response_content, metadata or {})
            except Exception as e:
                logger.warning(f"[APEX] on_submit callback error: {e}")
        
        status_code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=status_code)
    
    @router.get("/job/{job_id}")
    async def get_job(job_id: int):
        """Get job details from chain."""
        result = await state.job_ops.get_job(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
        # Convert non-JSON-serializable fields
        if "deliverable" in result and isinstance(result["deliverable"], bytes):
            result["deliverable"] = "0x" + result["deliverable"].hex()
        if "description" in result and isinstance(result["description"], bytes):
            result["description"] = result["description"].decode("utf-8", errors="replace")
        if "status" in result and hasattr(result["status"], "value"):
            result["status"] = result["status"].value  # Convert enum to int
        return JSONResponse(result)
    
    @router.get("/job/{job_id}/verify")
    async def verify_job(job_id: int):
        """
        Verify if a job can be processed by this agent.
        
        Returns:
            {
                "valid": bool,
                "job": object (if valid),
                "warnings": array (if any security concerns),
                "error": string (if invalid),
                "error_code": int (if invalid)
            }
        """
        result = await state.job_ops.verify_job(job_id)
        status_code = 200 if result.get("valid") else 400
        return JSONResponse(result, status_code=status_code)
    
    @router.post("/negotiate")
    async def negotiate(request: Request):
        """
        Process negotiation request.
        
        Request body:
            {
                "task_description": string (optional),
                "terms": {
                    "service_type": string,
                    "deliverables": string,
                    "quality_standards": string,
                    ...
                }
            }
        
        Returns:
            {
                "request": object,
                "request_hash": string,
                "response": object,
                "response_hash": string
            }
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        
        if not isinstance(body, dict) or "terms" not in body:
            return JSONResponse(
                {"error": "Request must include 'terms' with service_type, deliverables, quality_standards"},
                status_code=400,
            )
        
        try:
            result = state.negotiation_handler.negotiate(body)
            return JSONResponse(result.to_dict())
        except Exception as e:
            logger.error(f"[APEX] Negotiation failed: {e}")
            return JSONResponse({"error": "Negotiation failed"}, status_code=500)
    
    @router.get("/status")
    async def status():
        """Agent status endpoint."""
        return {
            "status": "ok",
            "agent_address": state.job_ops.agent_address,
            "erc8183_address": state.config.erc8183_address,
        }
    
    return router



def create_apex_app(
    config: Optional[APEXConfig] = None,
    title: str = "APEX Agent",
    description: str = "APEX (Agent Payment Exchange Protocol) Agent",
    prefix: str = "",
    on_submit: Optional[Callable[[int, str, Dict], Any]] = None,
) -> FastAPI:
    """
    Create a complete FastAPI application with APEX endpoints.
    
    The simplest way to deploy an APEX agent:
    
        app = create_apex_app()
        
        # Optional: add custom routes
        @app.post("/my-custom-endpoint")
        async def custom_handler():
            ...
    
    Run with: uvicorn myagent:app
    
    Args:
        config: APEXConfig instance (default: loads from env)
        title: FastAPI app title
        description: FastAPI app description
        prefix: URL prefix for APEX routes (default: no prefix)
        on_submit: Optional callback after successful submit
    
    Returns:
        FastAPI application instance
    
    Endpoints created:
        POST {prefix}/submit - Submit job result
        GET  {prefix}/job/{id} - Get job details
        GET  {prefix}/job/{id}/verify - Verify job
        POST {prefix}/negotiate - Process negotiation
        GET  {prefix}/status - Agent status
        GET  /health - Health check
    """
    # Load config if not provided
    if config is None:
        config = APEXConfig.from_env()
    
    # Create shared state
    state = create_apex_state(config)
    
    app = FastAPI(
        title=title,
        description=description,
    )
    
    # Include APEX routes
    router = create_apex_routes(config=config, state=state, on_submit=on_submit)
    app.include_router(router, prefix=prefix if prefix else "")
    
    # Health endpoint at root
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": title}
    
    # Root info endpoint
    @app.get("/")
    async def root():
        return {
            "service": title,
            "agent_address": state.job_ops.agent_address,
            "endpoints": {
                "submit": f"{prefix}/submit",
                "job": f"{prefix}/job/{{job_id}}",
                "verify": f"{prefix}/job/{{job_id}}/verify",
                "negotiate": f"{prefix}/negotiate",
                "status": f"{prefix}/status",
                "health": "/health",
            },
        }
    
    logger.info(
        f"[APEX] Agent created: address={state.job_ops.agent_address}, "
        f"erc8183={config.erc8183_address}"
    )
    
    return app
