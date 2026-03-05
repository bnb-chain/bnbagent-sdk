"""
ERC8004Agent SDK - Python SDK for ERC-8004 on-chain agent registration and management.

This SDK provides a simple interface for registering and managing AI agents on-chain
using the ERC-8004 Identity Registry contract, and interacting with the Bazaar V1
job lifecycle (EscrowUpgradeable).
"""

from .erc8004_agent import ERC8004Agent
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG
from .paymaster import Paymaster
from .escrow_client import EscrowClient, JobPhase, SettlementType
from .service_record import (
    ServiceRecord, RequestData, ResponseData,
    NegotiationTerms, TimestampData, OnChainReferences,
)
from .negotiation import (
    NegotiationRequest, NegotiationResponse,
    TermSpecification, ReasonCode,
)
from .storage import LocalStorageProvider

__version__ = "0.1.0"
__all__ = [
    "ERC8004Agent",
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    "TESTNET_CONFIG",
    "AgentEndpoint",
    "Paymaster",
    "EscrowClient",
    "JobPhase",
    "SettlementType",
    "ServiceRecord",
    "RequestData",
    "ResponseData",
    "NegotiationTerms",
    "TimestampData",
    "OnChainReferences",
    "NegotiationRequest",
    "NegotiationResponse",
    "TermSpecification",
    "ReasonCode",
    "LocalStorageProvider",
]

# IPFSStorageProvider requires httpx (optional dependency)
try:
    from .storage import IPFSStorageProvider
    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
