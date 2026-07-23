/**
 * Custom exceptions for bnbagent SDK.
 *
 * Provides a hierarchy of domain-specific exceptions for better error handling
 * and propagation throughout the SDK.
 */

/**
 * Base exception for all bnbagent SDK errors.
 */
export class BNBAgentError extends Error {
  constructor(message?: string) {
    super(message);
    this.name = "BNBAgentError";
  }
}

/**
 * Contract call or transaction failed.
 *
 * Raised when a smart contract interaction fails, including:
 * - Transaction reverts
 * - Gas estimation failures
 * - Invalid function calls
 */
export class ContractError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "ContractError";
  }
}

/**
 * Storage operation failed.
 *
 * Raised when file or IPFS storage operations fail, including:
 * - File not found
 * - Upload/download failures
 * - Invalid data format
 */
export class StorageError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "StorageError";
  }
}

/**
 * Missing or invalid configuration.
 *
 * Raised when required configuration is missing or invalid, including:
 * - Missing environment variables
 * - Invalid contract addresses
 * - Missing private keys
 */
export class ConfigurationError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

/**
 * Failed to load contract ABI.
 *
 * Raised when ABI file loading fails, including:
 * - File not found
 * - Invalid JSON format
 */
export class ABILoadError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "ABILoadError";
  }
}

/**
 * Network or RPC communication failed.
 *
 * Raised when network operations fail, including:
 * - RPC connection errors
 * - Rate limiting (429)
 * - Timeouts
 */
export class NetworkError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "NetworkError";
  }
}

/**
 * An RPC log/range query was rejected by the node's rate or range limit
 * (e.g. JSON-RPC ``-32005 limit exceeded``).
 *
 * Retryable: the queried data may exist but could not be fetched right
 * now — callers must NOT treat this as "event not found".
 */
export class RpcRangeLimitError extends NetworkError {
  constructor(message?: string) {
    super(message);
    this.name = "RpcRangeLimitError";
  }
}

/**
 * Job operation failed.
 *
 * Raised when job-related operations fail, including:
 * - Job not found
 * - Invalid job state
 * - Unauthorized access
 */
export class JobError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "JobError";
  }
}

/**
 * Negotiation failed.
 *
 * Raised when negotiation operations fail, including:
 * - Price validation errors
 * - Invalid terms
 * - Unsupported service types
 */
export class NegotiationError extends BNBAgentError {
  constructor(message?: string) {
    super(message);
    this.name = "NegotiationError";
  }
}

/**
 * A transaction was broadcast successfully but its receipt did not arrive
 * within the timeout.
 *
 * This is NOT a failure: the transaction may still confirm on-chain. Callers
 * must surface ``txHash`` so the user can check later or safely retry, and
 * must NOT treat this as a revert. Distinct from {@link ContractError},
 * which means the transaction failed (reverted, rejected, or never broadcast).
 */
export class TransactionPendingError extends BNBAgentError {
  constructor(
    public readonly txHash: string,
    public readonly timeoutSeconds: number,
    message?: string,
  ) {
    super(
      message ||
        `Transaction ${txHash} broadcast but not confirmed within ${timeoutSeconds}s; check later or retry safely.`,
    );
    this.name = "TransactionPendingError";
  }
}

/**
 * A relay returned a transaction hash, but the configured chain RPC never
 * observed that transaction before the receipt timeout expired.
 *
 * Unlike {@link TransactionPendingError}, this does not prove that the
 * transaction reached the network. Callers must not persist the hash as a
 * known-pending transaction.
 */
export class RelaySubmissionUnverifiedError extends BNBAgentError {
  constructor(
    public readonly txHash: string,
    public readonly timeoutSeconds: number,
    message?: string,
  ) {
    super(
      message ||
        `Relay returned transaction ${txHash}, but the chain RPC never observed it within ${timeoutSeconds}s. This matches a known MegaFuel relay failure mode (a hash is returned but the tx is never broadcast, seen most often on testnet). To bypass sponsorship and self-pay gas, set BNBAGENT_USE_PAYMASTER=0 or pass resolveNetwork a NetworkConfig with usePaymaster: false.`,
    );
    this.name = "RelaySubmissionUnverifiedError";
  }
}

/**
 * The relay accepted a transaction but never broadcast it, AND the automatic
 * self-pay fallback could not be sent — the wallet has no BNB for gas, or the
 * self-pay broadcast failed for a non-nonce reason.
 *
 * Extends {@link RelaySubmissionUnverifiedError} so every existing
 * classification site (retryable=false, `tx_unverified` mapping) treats it
 * exactly like an unverified relay submission: the nonce state is ambiguous,
 * so blind resubmission is unsafe.
 */
export class RelayFallbackFailedError extends RelaySubmissionUnverifiedError {
  constructor(
    txHash: string,
    timeoutSeconds: number,
    public readonly fallbackReason: string,
    options?: { cause?: unknown },
  ) {
    super(
      txHash,
      timeoutSeconds,
      `Relay returned transaction ${txHash} but never broadcast it, and the self-pay fallback could not be sent: ${fallbackReason}. Fund the wallet (tBNB faucet on testnet) and retry, or wait for the relay to recover.`,
    );
    this.name = "RelayFallbackFailedError";
    if (options?.cause !== undefined) {
      this.cause = options.cause;
    }
  }
}

/**
 * An ERC-8004 agent was registered on-chain (``agent_id`` assigned) but the
 * follow-up ``setAgentURI`` that populates the ``registrations[]`` field did
 * not complete.
 *
 * The agent exists and is owned by the wallet; only URI completion is
 * outstanding. ``txHash`` is set only when the completion tx was broadcast
 * but left pending (the cause is a {@link TransactionPendingError}). A relay
 * hash that was never observed is retained on ``cause`` and sets
 * ``retryable=false`` so callers do not blindly resubmit it.
 */
export class ERC8004PartialRegistrationError extends BNBAgentError {
  constructor(
    public readonly agentId: number,
    public readonly agentUri: string | null,
    public readonly cause: unknown,
    public readonly txHash: string | null = null,
    public readonly retryable: boolean = true,
  ) {
    let msg = `agent registered (agent_id=${agentId}) but agent_uri/registrations completion failed: ${cause}.`;
    msg += retryable
      ? " Retry setAgentURI / `bag erc8004 update-endpoint`."
      : " Reconcile the relay transaction before retrying setAgentURI / `bag erc8004 update-endpoint`.";
    if (txHash) {
      msg += ` pending tx_hash=${txHash}`;
    }
    super(msg);
    this.name = "ERC8004PartialRegistrationError";
  }
}
