"""
MPC Wallet Provider Implementation

Stub-by-design slot for an MPC (Multi-Party Computation) signing path.

The SDK intentionally does **not** ship an in-process MPC implementation:
high-value production agent flows should integrate an external MPC
provider (Coinbase CDP, Fireblocks, Web3Auth, etc.) which already
manages threshold-key custody, audit trails, and policy enforcement at
the enclave level. The stub here exists so the abstract
``WalletProvider`` interface stays satisfiable by ``isinstance`` checks
and so a project that selects ``wallet.kind = 'mpc'`` in configuration
gets a clear NotImplementedError rather than a silent fall-through.

If you need MPC, build a thin ``WalletProvider`` subclass in your own
project that adapts your provider's signing API to this interface.
"""

from __future__ import annotations

import logging
from typing import Any

from .wallet_provider import WalletProvider

logger = logging.getLogger(__name__)


class MPCWalletProvider(WalletProvider):
    """
    MPC (Multi-Party Computation) wallet provider implementation.

    This is a placeholder for future MPC wallet integration.
    MPC wallets use distributed key generation and signing, providing
    enhanced security without storing a single private key.

    Note: This is a stub implementation. Full MPC support will be added in the future.
    """

    def __init__(self, mpc_config: dict[str, Any] | None = None):
        """
        Initialize the MPC wallet provider.

        Args:
            mpc_config: Optional MPC configuration dictionary

        Raises:
            NotImplementedError: MPC wallet support is not yet implemented
        """
        self.mpc_config = mpc_config or {}

        raise NotImplementedError(
            "MPC wallet support is not yet implemented. Please use EVMWalletProvider for now."
        )

    @property
    def address(self) -> str:
        """Get the wallet address."""
        raise NotImplementedError("MPC wallet not implemented")

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        """Sign a transaction."""
        raise NotImplementedError("MPC wallet not implemented")

    def sign_message(self, message: str) -> dict[str, Any]:
        """Sign a message."""
        raise NotImplementedError("MPC wallet not implemented")

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Sign EIP-712 typed data."""
        raise NotImplementedError("MPC wallet not implemented")
