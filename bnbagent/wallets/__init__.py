"""
Wallet Providers Module

Abstract wallet provider interface and implementations.
Supports multiple wallet types (EVM, MPC) through a unified interface.
"""

from __future__ import annotations

from .evm_wallet_provider import EVMWalletProvider
from .factory import SUPPORTED_WALLET_KINDS, create_wallet_provider
from .intents import ExecutionContext, Intent, IntentExecutor
from .mpc_wallet_provider import MPCWalletProvider
from .twak_provider import TWAKProvider
from .wallet_provider import WalletProvider

__all__ = [
    "WalletProvider",
    "EVMWalletProvider",
    "MPCWalletProvider",
    "TWAKProvider",
    "Intent",
    "IntentExecutor",
    "ExecutionContext",
    "create_wallet_provider",
    "SUPPORTED_WALLET_KINDS",
]
