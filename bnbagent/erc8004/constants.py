"""ERC-8004 Identity Registry specific configuration.

Env surface (module-scoped, ``ERC8004_`` prefix):
    ERC8004_REGISTRY_ADDRESS — override registry_contract
"""

from __future__ import annotations

from typing import Any

from ..config import resolve_network
from ..core.config import get_env

ERC8004_ENV_PREFIX = "ERC8004_"


def get_erc8004_config(network: str = "bsc-testnet") -> dict[str, Any]:
    """Get ERC-8004 network configuration lazily.

    Applies ``ERC8004_REGISTRY_ADDRESS`` env override (when set) on top of
    the resolved network preset. Global ``RPC_URL`` overrides are handled
    inside ``resolve_network``.
    """
    nc = resolve_network(network)
    registry_override = get_env("REGISTRY_ADDRESS", prefix=ERC8004_ENV_PREFIX)
    return {
        "name": nc.name,
        "chain_id": nc.chain_id,
        "rpc_url": nc.rpc_url,
        "paymaster_url": nc.paymaster_url or "",
        "paymaster": nc.use_paymaster,
        "registry_contract": registry_override or nc.registry_contract,
    }
