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
    A transaction was submitted, or its broadcast result is uncertain, and no
    receipt arrived within the timeout.

    This is NOT a failure: the transaction may still confirm on-chain. Callers
    must surface ``tx_hash`` and reconcile it before any resubmission, and
    must NOT treat this as a revert. Distinct from :class:`ContractError`,
    which means the transaction failed (reverted, rejected, or never broadcast).

    ``relay_status`` is ``"public_tx_pending"`` (wait, never re-send the
    nonce) or, for a relay/sponsored write whose hash is visible but never
    mined in time, ``"receipt_timeout_but_tx_visible"`` (likely stuck in the
    mempool: escalate rather than retry).
    """

    def __init__(
        self,
        tx_hash: str,
        timeout_seconds: int,
        message: str | None = None,
    ):
        self.tx_hash = tx_hash
        self.timeout_seconds = timeout_seconds
        self.relay_status = "public_tx_pending"
        super().__init__(
            message
            or (
                f"Transaction {tx_hash} not confirmed within "
                f"{timeout_seconds}s; reconcile this hash before any rebroadcast."
            )
        )


class RelaySubmissionUnverifiedError(BNBAgentError):
    """
    A relay (sponsored) write returned a transaction hash that no public RPC
    could observe before the receipt wait gave up.

    The transaction may never have been broadcast, or it may still appear:
    do not resubmit blindly. Reconcile the hash and the wallet nonce first.
    ``secondary_rpc_result`` records the multi-RPC check that ran before the
    error was raised: ``"confirmed-unseen"`` (independent RPCs agree it is
    absent), ``"inconclusive"``, or ``"not-checked"``.
    """

    def __init__(
        self,
        tx_hash: str,
        timeout_seconds: int,
        message: str | None = None,
    ):
        self.tx_hash = tx_hash
        self.timeout_seconds = timeout_seconds
        self.relay_status = "relay_submission_unverified"
        self.secondary_rpc_result = "not-checked"
        super().__init__(
            message
            or (
                f"Relay returned transaction {tx_hash}, but no public RPC observed it "
                f"within {timeout_seconds}s. Do not resubmit blindly; reconcile the "
                "relay transaction and wallet nonce first."
            )
        )


class ERC8004PartialRegistrationError(BNBAgentError):
    """
    An ERC-8004 agent was registered on-chain (``agent_id`` assigned) but the
    follow-up ``setAgentURI`` that populates the ``registrations[]`` field did
    not complete.

    The agent exists and is owned by the wallet; only URI completion is
    outstanding. Retry via ``setAgentURI`` / ``bag erc8004 update-endpoint``.
    ``tx_hash`` is set only when the completion tx was broadcast but left
    pending (the cause is a :class:`TransactionPendingError`).
    """

    def __init__(
        self,
        agent_id: int,
        agent_uri: str | None,
        cause: Exception,
        tx_hash: str | None = None,
        retryable: bool = True,
    ):
        self.agent_id = agent_id
        self.agent_uri = agent_uri
        self.cause = cause
        self.tx_hash = tx_hash
        self.retryable = retryable
        msg = (
            f"agent registered (agent_id={agent_id}) but agent_uri/registrations "
            f"completion failed: {cause}. "
            "Retry setAgentURI / `bag erc8004 update-endpoint`."
        )
        if tx_hash:
            msg += f" pending tx_hash={tx_hash}"
        super().__init__(msg)


class JobError(BNBAgentError):
    """
    Job operation failed.

    Raised when job-related operations fail, including:
    - Job not found
    - Invalid job state
    - Unauthorized access
    """

    pass


class JobPaymentTokenMismatchError(JobError):
    """The caller's expected token differs from the job's on-chain token."""

    def __init__(self, job_id: int, expected_token: str, actual_token: str):
        self.job_id = job_id
        self.expected_token = expected_token
        self.actual_token = actual_token
        super().__init__(
            f"job {job_id} payment token mismatch: expected {expected_token}, got {actual_token}"
        )


class NegotiationError(BNBAgentError):
    """
    Negotiation failed.

    Raised when negotiation operations fail, including:
    - Price validation errors
    - Invalid terms
    - Unsupported service types
    """

    pass
