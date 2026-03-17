"""ERC-8004 Identity Registry — on-chain agent registration & discovery."""
from .agent import ERC8004Agent
from .contract import ContractInterface
from .models import AgentEndpoint
from .constants import ERC8004_CONFIG
from .module import ERC8004Module, create_module

__all__ = [
    "ERC8004Agent",
    "ContractInterface",
    "AgentEndpoint",
    "ERC8004_CONFIG",
    "ERC8004Module",
    "create_module",
]
