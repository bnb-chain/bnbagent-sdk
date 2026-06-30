"""
BNBAgent SDK — Python toolkit for building on-chain AI agents on BNB Chain.

Tier 1 (public API — available via ``from bnbagent import ...``):
    NetworkConfig, BNBAgentError
    ERC8004Agent, AgentEndpoint
    WalletProvider, EVMWalletProvider
    ERC8183Client, JobStatus, Verdict
    SigningPolicy, PolicyViolation
    X402Signer
    load_env
    set_default_receipt_timeout, get_default_receipt_timeout, set_min_gas_price_wei

Tier 2 (import from subpackage):
    from bnbagent.erc8183 import CommerceClient, RouterClient, PolicyClient, NegotiationHandler
    from bnbagent.erc8183 import ERC8183JobOps, funded_job_watcher
    from bnbagent.erc8183.config import ERC8183Config
    from bnbagent.utils import SlidingWindowLimiter, RateLimitExceeded
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
from .config import NetworkConfig

# ERC-8004 Identity Registry
from .erc8004 import AgentEndpoint, ERC8004Agent

# Exceptions
from .exceptions import (
    BNBAgentError,
    TransactionPendingError,
)

# Opt-in .env loading (never called at import time — applications opt in)
from .core.env import load_env

# Transaction tuning (gas-price floor + receipt timeout) — public knobs that
# replace any downstream monkey-patching of SDK internals.
from .core.contract_mixin import (
    get_default_receipt_timeout,
    set_default_receipt_timeout,
    set_min_gas_price_wei,
)

# Wallets
from .wallets import EVMWalletProvider, WalletProvider

# Signing policy
from .signing import PolicyViolation, SigningPolicy

# x402 payment signer
from .x402 import X402Signer

from ._version import __version__
__all__ = [
    # Core
    "NetworkConfig",
    "BNBAgentError",
    "TransactionPendingError",
    "load_env",
    # Transaction tuning
    "set_default_receipt_timeout",
    "get_default_receipt_timeout",
    "set_min_gas_price_wei",
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
