"""
MPC Wallet Provider Implementation

Placeholder for future MPC (Multi-Party Computation) wallet integration.
MPC wallets use distributed key generation and signing, providing
enhanced security without storing a single private key.
"""

from typing import Optional, Dict, Any

from ..utils.logger import get_logger
from .wallet_provider import WalletProvider


class MPCWalletProvider(WalletProvider):
    """
    MPC (Multi-Party Computation) wallet provider implementation.

    This is a placeholder for future MPC wallet integration.
    MPC wallets use distributed key generation and signing, providing
    enhanced security without storing a single private key.

    Note: This is a stub implementation. Full MPC support will be added in the future.
    """

    def __init__(
        self, mpc_config: Optional[Dict[str, Any]] = None, debug: bool = False
    ):
        """
        Initialize the MPC wallet provider.

        Args:
            mpc_config: Optional MPC configuration dictionary
            debug: Enable debug logging

        Raises:
            NotImplementedError: MPC wallet support is not yet implemented
        """
        self.debug = debug
        self._logger = get_logger(f"{__name__}.{self.__class__.__name__}", debug=debug)
        self.mpc_config = mpc_config or {}

        raise NotImplementedError(
            "MPC wallet support is not yet implemented. "
            "Please use EVMWalletProvider for now."
        )

    @property
    def address(self) -> str:
        """Get the wallet address."""
        raise NotImplementedError("MPC wallet not implemented")

    def sign_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Sign a transaction."""
        raise NotImplementedError("MPC wallet not implemented")

    def sign_message(self, message: str) -> Dict[str, Any]:
        """Sign a message."""
        raise NotImplementedError("MPC wallet not implemented")
