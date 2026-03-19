"""APEX protocol specific configuration."""

from __future__ import annotations

from typing import Any

from ..config import resolve_network


def get_apex_config(network: str = "bsc-testnet") -> dict[str, Any]:
    """Get APEX network configuration lazily."""
    nc = resolve_network(network)
    return {
        "name": nc.name,
        "chain_id": nc.chain_id,
        "rpc_url": nc.rpc_url,
        "paymaster_url": nc.paymaster_url or "",
        "paymaster": nc.use_paymaster,
        "erc8183_contract": nc.erc8183_contract,
        "apex_evaluator": nc.apex_evaluator,
        "payment_token": nc.payment_token,
    }
