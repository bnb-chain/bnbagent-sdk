"""
BNBAgent SDK — Python toolkit for building on-chain AI agents on BNB Chain.

Tier 1 (public API — available via ``from bnbagent import ...``):
    BNBAgent, BNBAgentConfig, NetworkConfig, BNBAgentError
    ERC8004Agent, AgentEndpoint
    WalletProvider, EVMWalletProvider
    ERC8183Client, JobStatus, Verdict
    SigningPolicy, PolicyViolation
    X402Signer

Tier 2 (import from subpackage):
    from bnbagent.erc8183 import CommerceClient, RouterClient, PolicyClient, NegotiationHandler
    from bnbagent.erc8183.server import create_erc8183_app, ERC8183JobOps
    from bnbagent.erc8183.config import ERC8183Config
    from bnbagent.core import create_web3
    from bnbagent.erc20 import MinimalERC20Client, load_erc20_abi
    from bnbagent.storage import LocalStorageProvider, IPFSStorageProvider
    from bnbagent.networks import get_address, BNB_CHAIN_ADDRESSES
    from bnbagent.signing import check, EIP3009_TYPES, PERMIT_UNBOUNDED_TYPES
    from bnbagent.x402 import SessionBudgetTracker, X402SignerError
"""

from __future__ import annotations

# ERC-8183 — only essential public API
from .erc8183 import ERC8183Client, JobStatus, Verdict

# Configuration
from .config import BNBAgentConfig, NetworkConfig

# ERC-8004 Identity Registry
from .erc8004 import AgentEndpoint, ERC8004Agent

# Exceptions
from .exceptions import BNBAgentError

# High-level facade
from .main import BNBAgent

# Wallets
from .wallets import EVMWalletProvider, WalletProvider

# Signing policy
from .signing import PolicyViolation, SigningPolicy

# x402 payment signer
from .x402 import X402Signer

from ._version import __version__
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
    # ERC-8183
    "ERC8183Client",
    "JobStatus",
    "Verdict",
    # Signing policy
    "SigningPolicy",
    "PolicyViolation",
    # x402 payment signer
    "X402Signer",
]
