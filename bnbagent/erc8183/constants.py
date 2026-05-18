"""ERC-8183 protocol specific configuration.

Env surface (module-scoped, ``ERC8183_`` prefix):
    ERC8183_COMMERCE_ADDRESS — override commerce_contract
    ERC8183_ROUTER_ADDRESS   — override router_contract
    ERC8183_POLICY_ADDRESS   — override policy_contract
"""

from __future__ import annotations

from typing import Any

from ..config import resolve_network
from ..core.config import get_env

ERC8183_ENV_PREFIX = "ERC8183_"


def get_erc8183_config(network: str = "bsc-testnet") -> dict[str, Any]:
    """Get ERC-8183 network configuration lazily.

    Applies ``ERC8183_*_ADDRESS`` env overrides (when set) on top of the
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
            get_env("COMMERCE_ADDRESS", prefix=ERC8183_ENV_PREFIX) or nc.commerce_contract
        ),
        "router_contract": (
            get_env("ROUTER_ADDRESS", prefix=ERC8183_ENV_PREFIX) or nc.router_contract
        ),
        "policy_contract": (
            get_env("POLICY_ADDRESS", prefix=ERC8183_ENV_PREFIX) or nc.policy_contract
        ),
    }
