"""
Quickstart module for rapid APEX agent deployment.

Provides a simple interface to create a fully configured FastAPI application
with APEX protocol endpoints, or just the routes to mount in an existing app.

Usage (minimal):
    from bnbagent.quickstart import create_apex_app
    app = create_apex_app()

Usage (custom config):
    from bnbagent.quickstart import create_apex_app, APEXConfig
    config = APEXConfig(
        rpc_url="https://...",
        erc8183_address="0x...",
        private_key="0x...",
    )
    app = create_apex_app(config=config)

Usage (mount routes to existing app):
    from fastapi import FastAPI
    from bnbagent.quickstart import create_apex_routes
    
    app = FastAPI(title="My Agent")
    app.include_router(create_apex_routes(), prefix="/apex")
"""

from .config import APEXConfig
from .app import create_apex_app, create_apex_routes, create_apex_state, APEXState

__all__ = [
    "APEXConfig",
    "APEXState",
    "create_apex_app",
    "create_apex_routes",
    "create_apex_state",
]
