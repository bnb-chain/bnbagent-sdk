"""ERC-8183 protocol module — ERC-8183 Protocol."""

from __future__ import annotations

from typing import Any

from ..core.module import BNBAgentModule, ModuleInfo


class ERC8183Module(BNBAgentModule):
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="erc8183",
            version="0.1.0",
            description=(
                "ERC-8183 Protocol — job lifecycle, escrow, negotiation, evaluation & settlement"
            ),
            dependencies=("erc8004",),
        )

    def default_config(self) -> dict[str, Any]:
        from ..config import resolve_network

        nc = resolve_network()
        return {
            "commerce_contract": nc.commerce_contract,
            "router_contract": nc.router_contract,
            "policy_contract": nc.policy_contract,
        }


def create_module() -> ERC8183Module:
    return ERC8183Module()
