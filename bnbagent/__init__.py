"""
ERC8004Agent SDK - Python SDK for ERC-8004 on-chain agent registration and management.

This SDK provides a simple interface for registering and managing AI agents on-chain
using the ERC-8004 Identity Registry contract.
"""

from .erc8004_agent import ERC8004Agent
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG
from .paymaster import Paymaster

__version__ = "0.1.0"
__all__ = [
    "ERC8004Agent",
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    "TESTNET_CONFIG",
    "AgentEndpoint",
    "Paymaster",
]
