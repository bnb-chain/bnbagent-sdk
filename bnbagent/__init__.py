"""
BNBAgent SDK — Python toolkit for building on-chain AI agents on BNB Chain.

APEX (Agent Payment Exchange Protocol) orchestrates the full agent commerce flow:
- ERC-8004 (Identity Registry): On-chain agent registration & discovery
- ERC-8183 (Agentic Commerce): Job lifecycle & escrow
- APEX Evaluator: Pluggable evaluation & dispute resolution
- Negotiation + ServiceRecord: Off-chain terms & evidence

Core components:
- ERC8004Agent: Agent registration and discovery (ERC-8004)
- APEXClient: ERC-8183 contract interaction (job lifecycle)
- APEXEvaluatorClient: Evaluator integration (currently UMA OOv3, pluggable)
- NegotiationHandler: Ready-to-use negotiation processing

Quickstart (import from bnbagent.quickstart):
- create_apex_app: Create a complete FastAPI app with APEX endpoints
- create_apex_routes: Create routes to mount in existing apps
- APEXConfig: Unified configuration (from_env() for env vars)

Server components (import from bnbagent.server):
- APEXJobOps: Simplified async job lifecycle operations
- APEXMiddleware: FastAPI middleware for job verification

For settlement, use APEXEvaluatorClient directly (settle_job, is_settleable, etc.)
"""

from .erc8004_agent import ERC8004Agent
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider
from .models import AgentEndpoint
from .constants import TESTNET_CONFIG
from .paymaster import Paymaster
from .apex_client import (
    APEXClient, APEXStatus,
    DEFAULT_LIVENESS_SECONDS, DVM_BUFFER_SECONDS, DEFAULT_JOB_EXPIRY_SECONDS,
    get_default_expiry,
)
from .apex_evaluator_client import APEXEvaluatorClient, AssertionInfo
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
from .nonce_manager import NonceManager
from .abi_loader import load_erc20_abi
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
    # APEX Protocol — ERC-8183 (Agentic Commerce)
    "APEXClient",
    "APEXStatus",
    "DEFAULT_LIVENESS_SECONDS",
    "DVM_BUFFER_SECONDS",
    "DEFAULT_JOB_EXPIRY_SECONDS",
    "get_default_expiry",
    # APEX Evaluator
    "APEXEvaluatorClient",
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
    # Nonce management
    "NonceManager",
    # ABI loaders
    "load_erc20_abi",
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
