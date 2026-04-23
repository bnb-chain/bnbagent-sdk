"""APEX protocol module — Agent Payment Exchange Protocol."""

from __future__ import annotations

from typing import Any

from ..core.module import BNBAgentModule, ModuleInfo


class APEXModule(BNBAgentModule):
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="apex",
            version="0.1.0",
            description=(
                "APEX Protocol — job lifecycle, escrow, negotiation, evaluation & settlement"
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


def create_module() -> APEXModule:
    return APEXModule()
