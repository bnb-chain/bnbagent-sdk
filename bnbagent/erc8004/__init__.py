"""ERC-8004 Identity Registry — on-chain agent registration & discovery."""

from __future__ import annotations

from .agent import ERC8004Agent
from .constants import get_erc8004_config
from .contract import ContractInterface
from .models import AgentEndpoint
from .module import ERC8004Module, create_module

__all__ = [
    "ERC8004Agent",
    "ContractInterface",
    "AgentEndpoint",
    "get_erc8004_config",
    "ERC8004Module",
    "create_module",
]
