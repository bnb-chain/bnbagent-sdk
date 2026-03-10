"""
ERC8004Agent SDK - Python SDK for ERC-8004 on-chain agent registration and management.

This SDK provides a simple interface for registering and managing AI agents on-chain
using the ERC-8004 Identity Registry contract, and interacting with the APEX Protocol
(ApexUpgradeable) job lifecycle.

Core components:
- ERC8004Agent: Agent registration and discovery
- ApexClient: Low-level APEX contract interaction
- NegotiationHandler: Ready-to-use negotiation processing
- JobVerifier: Job state verification

Server components (import from bnbagent.server):
- ApexJobOps: Simplified job lifecycle operations
- ApexMiddleware: FastAPI middleware for job verification
"""

from .erc8004_agent import ERC8004Agent
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG
from .paymaster import Paymaster
from .apex_client import ApexClient, JobPhase, SettlementType
from .job_verifier import JobVerifier, JobVerificationResult, parse_agent_routes
from .service_record import (
    ServiceRecord, RequestData, ResponseData,
    NegotiationTerms, TimestampData, OnChainReferences,
)
from .negotiation import (
    NegotiationRequest, NegotiationResponse,
    TermSpecification, ReasonCode,
    NegotiationHandler, NegotiationResult,
    PriceTooLowError,
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
    "ApexClient",
    "JobPhase",
    "SettlementType",
    "JobVerifier",
    "JobVerificationResult",
    "parse_agent_routes",
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
    "NegotiationHandler",
    "NegotiationResult",
    "PriceTooLowError",
    "LocalStorageProvider",
]

# IPFSStorageProvider requires httpx (optional dependency)
try:
    from .storage import IPFSStorageProvider
    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
