"""
ERC8004Agent SDK - Python SDK for ERC-8004 on-chain agent registration and management.

This SDK provides a simple interface for registering and managing AI agents on-chain
using the ERC-8004 Identity Registry contract, and interacting with:
- EIP-8183 Agentic Commerce Protocol (AgenticCommerceUpgradeable)

Core components:
- ERC8004Agent: Agent registration and discovery
- ACPClient: Low-level EIP-8183 contract interaction
- OOv3EvaluatorClient: UMA OOv3 evaluator integration
- NegotiationHandler: Ready-to-use negotiation processing

Quickstart (import from bnbagent.quickstart):
- create_acp_app: Create a complete FastAPI app with ACP endpoints
- create_acp_routes: Create routes to mount in existing apps
- ACPConfig: Unified configuration (from_env() for env vars)

Server components (import from bnbagent.server):
- ACPJobOps: Simplified job lifecycle operations (EIP-8183)
- ACPMiddleware: FastAPI middleware for job verification (EIP-8183)

For settlement, use OOv3EvaluatorClient directly (settle_job, is_settleable, etc.)
"""

from .erc8004_agent import ERC8004Agent
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG
from .paymaster import Paymaster
from .acp_client import (
    ACPClient, ACPStatus,
    DEFAULT_LIVENESS_SECONDS, DVM_BUFFER_SECONDS, DEFAULT_JOB_EXPIRY_SECONDS,
    get_default_expiry,
)
from .oov3_evaluator_client import OOv3EvaluatorClient, AssertionInfo
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
from .storage import LocalStorageProvider, storage_provider_from_env
from .exceptions import (
    BNBAgentError,
    ContractError,
    StorageError,
    ConfigurationError,
    ABILoadError,
    NetworkError,
    JobError,
    NegotiationError,
)

__version__ = "0.1.0"
__all__ = [
    "ERC8004Agent",
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    "TESTNET_CONFIG",
    "AgentEndpoint",
    "Paymaster",
    # EIP-8183 Agentic Commerce Protocol
    "ACPClient",
    "ACPStatus",
    "DEFAULT_LIVENESS_SECONDS",
    "DVM_BUFFER_SECONDS",
    "DEFAULT_JOB_EXPIRY_SECONDS",
    "get_default_expiry",
    # OOv3 Evaluator
    "OOv3EvaluatorClient",
    "AssertionInfo",
    # Service Records
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
    "storage_provider_from_env",
    # Exceptions
    "BNBAgentError",
    "ContractError",
    "StorageError",
    "ConfigurationError",
    "ABILoadError",
    "NetworkError",
    "JobError",
    "NegotiationError",
]

# IPFSStorageProvider requires httpx (optional dependency)
try:
    from .storage import IPFSStorageProvider
    __all__.append("IPFSStorageProvider")
except ImportError:
    pass
