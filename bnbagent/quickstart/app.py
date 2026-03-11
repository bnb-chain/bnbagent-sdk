"""
FastAPI application factory for ACP agents.

Provides:
- create_acp_app(): Create a complete FastAPI app with ACP endpoints
- create_acp_routes(): Create an APIRouter to mount in existing apps
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import ACPConfig
from ..server.acp_job_ops import ACPJobOps
from ..storage import storage_provider_from_env, LocalStorageProvider
from ..negotiation import NegotiationHandler

logger = logging.getLogger(__name__)


@dataclass
class ACPState:
    """
    Shared state for ACP operations.
    
    Initialized once and shared across all route handlers.
    """
    config: ACPConfig
    job_ops: ACPJobOps
    negotiation_handler: NegotiationHandler
    
    def __repr__(self) -> str:
        """Safe repr that hides sensitive data."""
        return (
            f"ACPState("
            f"agent_address='{self.job_ops.agent_address}', "
            f"acp_address='{self.config.acp_address}')"
        )


def _create_storage_provider(config: ACPConfig):
    """Create storage provider based on config."""
    if config.storage_provider == "ipfs":
        if config.pinata_jwt:
            from ..storage import IPFSStorageProvider
            return IPFSStorageProvider(
                pinning_api_url="https://api.pinata.cloud/pinning/pinJSONToIPFS",
                pinning_api_key=config.pinata_jwt,
                gateway_url=config.pinata_gateway or "https://gateway.pinata.cloud/ipfs/",
            )
    return LocalStorageProvider(config.local_storage_path)


def _create_state(config: ACPConfig) -> ACPState:
    """Create ACPState with all necessary components."""
    storage = _create_storage_provider(config)
    
    job_ops = ACPJobOps(
        rpc_url=config.rpc_url,
        acp_address=config.acp_address,
        private_key=config.private_key,
        storage_provider=storage,
        chain_id=config.chain_id,
    )
    
    negotiation_handler = NegotiationHandler(
        base_price=config.agent_price,
        currency=config.payment_token_address or "",
    )
    
    return ACPState(
        config=config,
        job_ops=job_ops,
        negotiation_handler=negotiation_handler,
    )


def create_acp_routes(
    config: Optional[ACPConfig] = None,
    state: Optional[ACPState] = None,
    on_submit: Optional[Callable[[int, str, Dict], Any]] = None,
) -> APIRouter:
    """
    Create an APIRouter with ACP endpoints.
    
    Can be mounted to an existing FastAPI app:
    
        app.include_router(create_acp_routes(), prefix="/acp")
    
    Args:
        config: ACPConfig instance (default: loads from env)
        state: Pre-created ACPState (default: creates from config)
        on_submit: Optional callback after successful submit. 
                   Called with (job_id, response_content, metadata)
    
    Returns:
        APIRouter with /submit, /job/{id}, /job/{id}/verify, /negotiate endpoints
    """
    # Resolve config and state
    if state is None:
        if config is None:
            config = ACPConfig.from_env()
        state = _create_state(config)
    
    router = APIRouter(tags=["ACP"])
    
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
                logger.warning(f"[ACP] on_submit callback error: {e}")
        
        status_code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=status_code)
    
    @router.get("/job/{job_id}")
    async def get_job(job_id: int):
        """Get job details from chain."""
        result = await state.job_ops.get_job(job_id)
        if not result.get("success"):
            return JSONResponse(result, status_code=500)
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
            logger.error(f"[ACP] Negotiation failed: {e}")
            return JSONResponse({"error": f"Negotiation failed: {e}"}, status_code=500)
    
    @router.get("/status")
    async def status():
        """Agent status endpoint."""
        return {
            "status": "ok",
            "agent_address": state.job_ops.agent_address,
            "acp_address": state.config.acp_address,
        }
    
    return router


def create_acp_app(
    config: Optional[ACPConfig] = None,
    title: str = "ACP Agent",
    description: str = "EIP-8183 Agent Commerce Protocol Agent",
    prefix: str = "",
    on_submit: Optional[Callable[[int, str, Dict], Any]] = None,
) -> FastAPI:
    """
    Create a complete FastAPI application with ACP endpoints.
    
    The simplest way to deploy an ACP agent:
    
        app = create_acp_app()
        
        # Optional: add custom routes
        @app.post("/my-custom-endpoint")
        async def custom_handler():
            ...
    
    Run with: uvicorn myagent:app
    
    Args:
        config: ACPConfig instance (default: loads from env)
        title: FastAPI app title
        description: FastAPI app description
        prefix: URL prefix for ACP routes (default: no prefix)
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
        config = ACPConfig.from_env()
    
    # Create shared state
    state = _create_state(config)
    
    app = FastAPI(
        title=title,
        description=description,
    )
    
    # Include ACP routes
    router = create_acp_routes(config=config, state=state, on_submit=on_submit)
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
        f"[ACP] Agent created: address={state.job_ops.agent_address}, "
        f"acp={config.acp_address}"
    )
    
    return app
