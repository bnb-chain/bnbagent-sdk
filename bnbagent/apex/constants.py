"""APEX protocol specific configuration.

Env surface (module-scoped, ``APEX_`` prefix):
    APEX_COMMERCE_ADDRESS — override commerce_contract
    APEX_ROUTER_ADDRESS   — override router_contract
    APEX_POLICY_ADDRESS   — override policy_contract
"""

from __future__ import annotations

from typing import Any

from ..config import resolve_network
from ..core.config import get_env

APEX_ENV_PREFIX = "APEX_"


def get_apex_config(network: str = "bsc-testnet") -> dict[str, Any]:
    """Get APEX network configuration lazily.

    Applies ``APEX_*_ADDRESS`` env overrides (when set) on top of the
    resolved network preset. Global ``RPC_URL`` overrides are handled
    inside ``resolve_network``.
    """
    nc = resolve_network(network)
    return {
        "name": nc.name,
        "chain_id": nc.chain_id,
        "rpc_url": nc.rpc_url,
        "paymaster_url": nc.paymaster_url or "",
        "paymaster": nc.use_paymaster,
        "commerce_contract": (
            get_env("COMMERCE_ADDRESS", prefix=APEX_ENV_PREFIX) or nc.commerce_contract
        ),
        "router_contract": (
            get_env("ROUTER_ADDRESS", prefix=APEX_ENV_PREFIX) or nc.router_contract
        ),
        "policy_contract": (
            get_env("POLICY_ADDRESS", prefix=APEX_ENV_PREFIX) or nc.policy_contract
        ),
    }
