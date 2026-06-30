"""
Custom exceptions for bnbagent SDK.

Provides a hierarchy of domain-specific exceptions for better error handling
and propagation throughout the SDK.
"""

from __future__ import annotations


class BNBAgentError(Exception):
    """Base exception for all bnbagent SDK errors."""

    pass


class ContractError(BNBAgentError):
    """
    Contract call or transaction failed.

    Raised when a smart contract interaction fails, including:
    - Transaction reverts
    - Gas estimation failures
    - Invalid function calls
    """

    pass


class StorageError(BNBAgentError):
    """
    Storage operation failed.

    Raised when file or IPFS storage operations fail, including:
    - File not found
    - Upload/download failures
    - Invalid data format
    """

    pass


class ConfigurationError(BNBAgentError):
    """
    Missing or invalid configuration.

    Raised when required configuration is missing or invalid, including:
    - Missing environment variables
    - Invalid contract addresses
    - Missing private keys
    """

    pass


class ABILoadError(BNBAgentError):
    """
    Failed to load contract ABI.

    Raised when ABI file loading fails, including:
    - File not found
    - Invalid JSON format
    """

    pass


class NetworkError(BNBAgentError):
    """
    Network or RPC communication failed.

    Raised when network operations fail, including:
    - RPC connection errors
    - Rate limiting (429)
    - Timeouts
    """

    pass


class RpcRangeLimitError(NetworkError):
    """
    An RPC log/range query was rejected by the node's rate or range limit
    (e.g. JSON-RPC ``-32005 limit exceeded``).

    Retryable: the queried data may exist but could not be fetched right
    now — callers must NOT treat this as "event not found".
    """

    pass


class TransactionPendingError(BNBAgentError):
    """
    A transaction was broadcast successfully but its receipt did not arrive
    within the timeout.

    This is NOT a failure: the transaction may still confirm on-chain. Callers
    must surface ``tx_hash`` so the user can check later or safely retry, and
    must NOT treat this as a revert. Distinct from :class:`ContractError`,
    which means the transaction failed (reverted, rejected, or never broadcast).
    """

    def __init__(
        self,
        tx_hash: str,
        timeout_seconds: int,
        message: str | None = None,
    ):
        self.tx_hash = tx_hash
        self.timeout_seconds = timeout_seconds
        super().__init__(
            message
            or (
                f"Transaction {tx_hash} broadcast but not confirmed within "
                f"{timeout_seconds}s; check later or retry safely."
            )
        )


class JobError(BNBAgentError):
    """
    Job operation failed.

    Raised when job-related operations fail, including:
    - Job not found
    - Invalid job state
    - Unauthorized access
    """

    pass


class NegotiationError(BNBAgentError):
    """
    Negotiation failed.

    Raised when negotiation operations fail, including:
    - Price validation errors
    - Invalid terms
    - Unsupported service types
    """

    pass
