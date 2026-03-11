"""
Quickstart module for rapid ACP agent deployment.

Provides a simple interface to create a fully configured FastAPI application
with EIP-8183 ACP endpoints, or just the routes to mount in an existing app.

Usage (minimal):
    from bnbagent.quickstart import create_acp_app
    app = create_acp_app()

Usage (custom config):
    from bnbagent.quickstart import create_acp_app, ACPConfig
    config = ACPConfig(
        rpc_url="https://...",
        acp_address="0x...",
        private_key="0x...",
    )
    app = create_acp_app(config=config)

Usage (mount routes to existing app):
    from fastapi import FastAPI
    from bnbagent.quickstart import create_acp_routes
    
    app = FastAPI(title="My Agent")
    app.include_router(create_acp_routes(), prefix="/acp")
"""

from .config import ACPConfig
from .app import create_acp_app, create_acp_routes, ACPState

__all__ = [
    "ACPConfig",
    "ACPState",
    "create_acp_app",
    "create_acp_routes",
]
