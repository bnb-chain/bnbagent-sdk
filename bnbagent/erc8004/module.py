"""ERC-8004 Identity Registry module."""

from __future__ import annotations

from typing import Any

from ..core.module import BNBAgentModule, ModuleInfo


class ERC8004Module(BNBAgentModule):
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="erc8004",
            version="0.1.0",
            description="ERC-8004 Identity Registry — on-chain agent registration & discovery",
        )

    def default_config(self) -> dict[str, Any]:
        from ..config import resolve_network

        nc = resolve_network()
        return {"registry_contract": nc.registry_contract}


def create_module() -> ERC8004Module:
    return ERC8004Module()
