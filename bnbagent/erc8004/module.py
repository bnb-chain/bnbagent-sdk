"""ERC-8004 Identity Registry module."""
from __future__ import annotations
from typing import Any, Dict, Sequence
from ..core.module import BNBAgentModule, ModuleInfo


class ERC8004Module(BNBAgentModule):
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="erc8004",
            version="0.1.0",
            description="ERC-8004 Identity Registry — on-chain agent registration & discovery",
        )

    def default_config(self) -> Dict[str, Any]:
        return {
            "registry_contract": "0x8004A818BFB912233c491871b3d84c89A494BD9e",
        }


def create_module() -> ERC8004Module:
    return ERC8004Module()
