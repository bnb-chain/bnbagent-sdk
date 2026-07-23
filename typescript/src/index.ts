/**
 * BNBAgent SDK — TypeScript toolkit for building on-chain AI agents on BNB Chain.
 *
 * Tier 1 (public API — available via `import { ... } from "@bnbagent/sdk"`):
 *     NetworkConfig, NETWORKS, resolveNetwork
 *     BNBAgentError, ContractError, StorageError, ConfigurationError,
 *     ABILoadError, NetworkError, RpcRangeLimitError, JobError,
 *     NegotiationError, TransactionPendingError, RelaySubmissionUnverifiedError,
 *     ERC8004PartialRegistrationError
 *     ERC8004Agent, AgentEndpoint
 *     WalletProvider, EVMWalletProvider, AltanaWalletProvider
 *     ERC8183Client, JobStatus, Verdict
 *     SigningPolicy, PolicyViolation
 *     X402Signer
 *     loadEnv
 *     setMinGasPriceWei, minGasPriceWei, setDefaultReceiptTimeout, getDefaultReceiptTimeout
 *     Paymaster, NonceManager, SCAN_API_URL
 *
 * Tier 2 (import from subpath — full package surface):
 *     import { CommerceClient, RouterClient, PolicyClient, ERC8183JobOps, fundedJobWatcher } from "@bnbagent/sdk/erc8183";
 *     import { AgentURIGenerator, ContractInterface, getErc8004Config } from "@bnbagent/sdk/erc8004";
 *     import { SessionBudgetTracker, X402SignerError } from "@bnbagent/sdk/x402";
 *     import { LocalStorageProvider, IPFSStorageProvider } from "@bnbagent/sdk/storage";
 *     import { LocalExecutor, UnsupportedWalletOperation } from "@bnbagent/sdk/wallets";
 *     import { check, EIP3009_TYPES } from "@bnbagent/sdk/signing";
 *     import { getAddress, BNB_CHAIN_ADDRESSES } from "@bnbagent/sdk/networks";
 *     import { SlidingWindowLimiter, RateLimitExceeded } from "@bnbagent/sdk/utils";
 *
 * Mirrors `python/bnbagent/__init__.py`'s Tier 1 surface; see
 * `ARCHITECTURE.md` for the full protocol/module reference.
 */

// Configuration
export { type NetworkConfig, NETWORKS, resolveNetwork } from "./config.js";

// Exceptions
export {
  BNBAgentError,
  ContractError,
  StorageError,
  ConfigurationError,
  ABILoadError,
  NetworkError,
  RpcRangeLimitError,
  JobError,
  NegotiationError,
  TransactionPendingError,
  RelaySubmissionUnverifiedError,
  RelayFallbackFailedError,
  ERC8004PartialRegistrationError,
} from "./errors.js";

// ERC-8004 Identity Registry
export { ERC8004Agent } from "./erc8004/agent.js";
export { AgentEndpoint } from "./erc8004/models.js";

// Wallets
export { WalletProvider } from "./wallets/walletProvider.js";
export { EVMWalletProvider } from "./wallets/evmWalletProvider.js";
// Self-broadcasting Altana (EIP-7702 session-key) wallet. The backing
// @altananetwork/sdk peer is optional and loaded lazily on first use.
export { AltanaWalletProvider } from "./wallets/altana/index.js";
// Self-broadcasting TWAK (Trust Wallet Agent Kit CLI) wallet — twak is an
// npm CLI; install @trustwallet/cli and the provider finds the local bin.
export { TWAKProvider } from "./wallets/twak/index.js";

// ERC-8183 — only essential public API
export { ERC8183Client } from "./erc8183/client.js";
export { JobStatus, Verdict } from "./erc8183/types.js";

// Signing policy
export { SigningPolicy } from "./signing/policy.js";
export { PolicyViolation } from "./signing/errors.js";

// x402 payment signer
export { X402Signer } from "./x402/signer.js";

// Opt-in .env loading (never called at import time — applications opt in)
export { loadEnv } from "./core/env.js";

// Transaction tuning (gas-price floor + receipt timeout) — public knobs that
// replace any downstream monkey-patching of SDK internals.
export {
  setMinGasPriceWei,
  minGasPriceWei,
  setDefaultReceiptTimeout,
  getDefaultReceiptTimeout,
} from "./core/txConfig.js";

// Paymaster + nonce management
export { Paymaster } from "./core/paymaster.js";
export { NonceManager } from "./core/nonceManager.js";

// Shared constants
export { SCAN_API_URL } from "./constants.js";
