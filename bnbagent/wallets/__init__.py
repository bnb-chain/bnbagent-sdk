"""
Wallet Providers Module

Abstract wallet provider interface and implementations.
Supports multiple wallet types (EVM, MPC) through a unified interface.
"""

from __future__ import annotations

from . import capabilities
from .errors import UnsupportedWalletOperation, WalletIdentityMismatch
from .evm_wallet_provider import EVMWalletProvider
from .factory import SUPPORTED_WALLET_KINDS, create_wallet_provider
from .intents import ExecutionContext, Intent, IntentExecutor
from .mpc_wallet_provider import MPCWalletProvider
from .protocols import MessageSigner, TypedDataSigner
from .twak_custody import materialize_twak_home
from .twak_provider import TWAK_CHAIN_FOR_NETWORK, TWAKProvider
from .wallet_provider import WalletProvider

__all__ = [
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    "TWAKProvider",
    "UnsupportedWalletOperation",
    "WalletIdentityMismatch",
    "materialize_twak_home",
    "MessageSigner",
    "TypedDataSigner",
    "capabilities",
    "Intent",
    "IntentExecutor",
    "ExecutionContext",
    "create_wallet_provider",
    "SUPPORTED_WALLET_KINDS",
    "TWAK_CHAIN_FOR_NETWORK",
]
