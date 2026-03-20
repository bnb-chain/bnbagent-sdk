"""
BNBAgent SDK — Python toolkit for building on-chain AI agents on BNB Chain.

Tier 1 (public API — available via ``from bnbagent import ...``):
    BNBAgent, BNBAgentConfig, NetworkConfig, BNBAgentError
    ERC8004Agent, AgentEndpoint
    WalletProvider, EVMWalletProvider
    APEXClient, APEXStatus
    StorageConfig

Tier 2 (import from subpackage):
    from bnbagent.apex import NegotiationHandler, APEXEvaluatorClient, ...
    from bnbagent.apex.server import create_apex_app, APEXJobOps
    from bnbagent.apex.config import APEXConfig
    from bnbagent.core import create_web3, load_erc20_abi
    from bnbagent.storage import LocalStorageProvider, IPFSStorageProvider
"""

from __future__ import annotations

# APEX — only essential public API
from .apex import APEXClient, APEXStatus

# Configuration
from .config import BNBAgentConfig, NetworkConfig

# ERC-8004 Identity Registry
from .erc8004 import AgentEndpoint, ERC8004Agent

# Exceptions
from .exceptions import BNBAgentError

# High-level facade
from .main import BNBAgent

# Storage
from .storage import StorageConfig

# Wallets
from .wallets import EVMWalletProvider, WalletProvider

__version__ = "0.2.0"
__all__ = [
    # Core
    "BNBAgent",
    "BNBAgentConfig",
    "NetworkConfig",
    "BNBAgentError",
    # ERC-8004
    "ERC8004Agent",
    "AgentEndpoint",
    # Wallets
    "WalletProvider",
    "EVMWalletProvider",
    # APEX
    "APEXClient",
    "APEXStatus",
    # Storage
    "StorageConfig",
]
