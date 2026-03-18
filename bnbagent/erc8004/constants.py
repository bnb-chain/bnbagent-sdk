"""ERC-8004 Identity Registry specific configuration."""

from __future__ import annotations

from typing import Any

from ..config import resolve_network


def get_erc8004_config(network: str = "bsc-testnet") -> dict[str, Any]:
    """Get ERC-8004 network configuration lazily."""
    nc = resolve_network(network)
    return {
        "name": nc.name,
        "chain_id": nc.chain_id,
        "rpc_url": nc.rpc_url,
        "paymaster_url": nc.paymaster_url or "",
        "paymaster": nc.use_paymaster,
        "registry_contract": nc.registry_contract,
    }
