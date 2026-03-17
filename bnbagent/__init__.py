"""
BNBAgent SDK — Python toolkit for building on-chain AI agents on BNB Chain.

Modules:
- core: Shared infrastructure (exceptions, nonce manager, paymaster, module system)
- erc8004: ERC-8004 Identity Registry — on-chain agent registration & discovery
- apex: APEX Protocol (ERC-8183) — job lifecycle, escrow, evaluation, negotiation
- wallets: Pluggable wallet providers (EVM, MPC)
- storage: Pluggable storage providers (local, IPFS)

Module system (import from bnbagent.core):
- BNBAgentModule: Base class for protocol modules
- ModuleRegistry: Module discovery and lifecycle management
- BNBAgentConfig: Unified SDK configuration
- BNBAgentSDK: High-level facade

APEX server components (import from bnbagent.apex.server):
- APEXJobOps: Simplified async job lifecycle operations
- APEXMiddleware: FastAPI middleware for job verification
- create_apex_app / create_apex_routes: FastAPI application factories
- APEXConfig: APEX-specific configuration (from bnbagent.apex.config)
"""

# ERC-8004 Identity Registry
from .erc8004 import ERC8004Agent, AgentEndpoint

# Wallets
from .wallets import WalletProvider, EVMWalletProvider, MPCWalletProvider

# Core infrastructure
from .core import (
    Paymaster,
    NonceManager,
    load_erc20_abi,
    BNBAgentError,
    ContractError,
    StorageError,
    ConfigurationError,
    ABILoadError,
    NetworkError,
    JobError,
    NegotiationError,
)

# Module system
from .core import BNBAgentModule, ModuleInfo, ModuleRegistry, BNBAgentConfig, NetworkConfig, BNBAgentSDK

# APEX Protocol — ERC-8183 (Agentic Commerce)
from .apex import (
    APEXClient, APEXStatus,
    DEFAULT_LIVENESS_SECONDS, DVM_BUFFER_SECONDS, DEFAULT_JOB_EXPIRY_SECONDS,
    get_default_expiry,
    APEXEvaluatorClient, AssertionInfo,
    NegotiationRequest, NegotiationResponse,
    TermSpecification, ReasonCode,
    NegotiationHandler, NegotiationResult,
    PriceTooLowError,
    ServiceRecord, RequestData, ResponseData,
    NegotiationTerms, TimestampData, OnChainReferences,
)

# Storage
from .storage import LocalStorageProvider, storage_provider_from_env

# Construct unified TESTNET_CONFIG from module configs
from .erc8004.constants import ERC8004_CONFIG
from .apex.constants import APEX_CONFIG
from .core.constants import _SHARED_TESTNET

TESTNET_CONFIG = {
    **_SHARED_TESTNET,
    "registry_contract": ERC8004_CONFIG["registry_contract"],
    "erc8183_contract": APEX_CONFIG["erc8183_contract"],
    "apex_evaluator": APEX_CONFIG["apex_evaluator"],
    "payment_token": APEX_CONFIG["payment_token"],
}

__version__ = "0.1.0"
__all__ = [
    # ERC-8004
    "ERC8004Agent",
    "AgentEndpoint",
    # Wallets
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    # Core
    "Paymaster",
    "NonceManager",
    "load_erc20_abi",
    "TESTNET_CONFIG",
    # Module system
    "BNBAgentModule",
    "ModuleInfo",
    "ModuleRegistry",
    "BNBAgentConfig",
    "NetworkConfig",
    "BNBAgentSDK",
    # APEX Protocol
    "APEXClient",
    "APEXStatus",
    "DEFAULT_LIVENESS_SECONDS",
    "DVM_BUFFER_SECONDS",
    "DEFAULT_JOB_EXPIRY_SECONDS",
    "get_default_expiry",
    "APEXEvaluatorClient",
    "AssertionInfo",
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
    # Storage
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
