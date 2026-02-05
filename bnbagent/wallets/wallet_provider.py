"""
Wallet Provider Abstract Base Class

Defines the interface that all wallet providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class WalletProvider(ABC):
    """
    Abstract base class for wallet providers.

    This interface defines the contract that all wallet providers must implement,
    allowing for easy swapping between different wallet implementations (EVM, MPC, etc.).
    """

    @property
    @abstractmethod
    def address(self) -> str:
        """
        Get the wallet address.

        Returns:
            str: The Ethereum address of the wallet
        """
        pass

    @abstractmethod
    def sign_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sign a transaction.

        Args:
            transaction: Transaction dictionary with fields like 'to', 'value', 'gas',
                        'gasPrice', 'nonce', 'data', 'chainId'

        Returns:
            dict: Signed transaction dictionary with 'rawTransaction', 'hash', 'r', 's', 'v'
        """
        pass

    @abstractmethod
    def sign_message(self, message: str) -> Dict[str, Any]:
        """
        Sign a message using EIP-191 personal sign.

        Args:
            message: Message string to sign

        Returns:
            dict: Signature dictionary with 'messageHash', 'r', 's', 'v', 'signature'
        """
        pass
