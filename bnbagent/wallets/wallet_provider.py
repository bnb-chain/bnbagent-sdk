"""
Wallet Provider Abstract Base Class

Defines the interface that all wallet providers must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
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
    def sign_message(self, message: str) -> dict[str, Any]:
        """
        Sign a message using EIP-191 personal sign.

        Args:
            message: Message string to sign

        Returns:
            dict: Signature dictionary with 'messageHash', 'r', 's', 'v',
                  'signature'. ``messageHash`` is the **EIP-191 personal-sign
                  digest** (``keccak256("\\x19Ethereum Signed Message:\\n" || len ||
                  message)``) — *not* interchangeable with the digest returned by
                  :meth:`sign_typed_data`.
        """
        pass

    @abstractmethod
    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Sign typed structured data per EIP-712, gated by a SigningPolicy.

        Used for protocols requiring signed structured payloads — EIP-3009
        transferWithAuthorization (x402 micropay), ERC-8183 negotiate quotes,
        permit2, etc. The signing key never leaves the wallet implementation.

        Implementations MUST invoke their configured ``SigningPolicy.check()``
        on ``(domain, types, message)`` *before* producing a signature, and
        propagate :class:`bnbagent.signing.PolicyViolation` on rejection. This
        is the SDK's first-line defense against blind-sign attacks via a
        malicious EIP-712 payload (unknown verifyingContract, unbounded
        Permit, open-ended validBefore, etc.). To intentionally bypass the
        policy (tests / migrations only), call the implementation-private
        ``_DANGEROUS_sign_typed_data_no_policy`` method, which logs a WARN
        with the caller module for auditability.

        Args:
            domain: EIP-712 domain separator, e.g.
                    ``{"name": "United Stables", "version": "1",
                       "chainId": 56, "verifyingContract": "0x..."}``.
            types: Dict mapping each EIP-712 struct name to a list of
                   ``{"name": str, "type": str}`` field descriptors. Must include
                   the ``EIP712Domain`` entry alongside the message struct(s).
            message: The struct values keyed by field name. The primary type is
                     inferred as the only struct in ``types`` that is not
                     ``EIP712Domain``.

        Returns:
            dict: Signature dictionary with 'messageHash', 'r', 's', 'v',
                  'signature'. Same shape as :meth:`sign_message`, but
                  ``messageHash`` here is the **EIP-712 typed-data digest**
                  (``keccak256("\\x19\\x01" || domainSeparator ||
                  hashStruct(message))``) — *not* the EIP-191 digest returned by
                  :meth:`sign_message`. This is the value that on-chain
                  ``ecrecover`` will use against this signature.

        Raises:
            bnbagent.signing.PolicyViolation: If the configured SigningPolicy
                refuses the request (unknown domain, denylisted primary
                type, validity window too wide, etc.).
            NotImplementedError: For wallet kinds that do not implement
                EIP-712 signing in this SDK (e.g. ``MPCWalletProvider``, an
                interface stub meant to be subclassed against an external
                MPC provider).
        """
        pass
