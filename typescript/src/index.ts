/**
 * BNBAgent SDK — TypeScript toolkit for building on-chain AI agents on BNB Chain.
 *
 * Tier 1 (public API — available via `import { ... } from "@bnb-chain/bnbagent"`):
 *     NetworkConfig, NETWORKS, resolveNetwork
 *     BNBAgentError, ContractError, StorageError, ConfigurationError,
 *     ABILoadError, NetworkError, RpcRangeLimitError, JobError,
 *     NegotiationError, TransactionPendingError, ERC8004PartialRegistrationError
 *     ERC8004Agent, AgentEndpoint
 *     WalletProvider, EVMWalletProvider
 *     ERC8183Client, JobStatus, Verdict
 *     SigningPolicy, PolicyViolation
 *     X402Signer
 *     loadEnv
 *     setMinGasPriceWei, minGasPriceWei, setDefaultReceiptTimeout, getDefaultReceiptTimeout
 *     Paymaster, NonceManager, SCAN_API_URL
 *
 * Tier 2 (import from subpath — full package surface):
 *     import { CommerceClient, RouterClient, PolicyClient, ERC8183JobOps, fundedJobWatcher } from "@bnb-chain/bnbagent/erc8183";
 *     import { AgentURIGenerator, ContractInterface, getErc8004Config } from "@bnb-chain/bnbagent/erc8004";
 *     import { SessionBudgetTracker, X402SignerError } from "@bnb-chain/bnbagent/x402";
 *     import { LocalStorageProvider, IPFSStorageProvider } from "@bnb-chain/bnbagent/storage";
 *     import { LocalExecutor, UnsupportedWalletOperation } from "@bnb-chain/bnbagent/wallets";
 *     import { check, EIP3009_TYPES } from "@bnb-chain/bnbagent/signing";
 *     import { getAddress, BNB_CHAIN_ADDRESSES } from "@bnb-chain/bnbagent/networks";
 *     import { SlidingWindowLimiter, RateLimitExceeded } from "@bnb-chain/bnbagent/utils";
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
  ERC8004PartialRegistrationError,
} from "./errors.js";

// ERC-8004 Identity Registry
export { ERC8004Agent } from "./erc8004/agent.js";
export { AgentEndpoint } from "./erc8004/models.js";

// Wallets
export { WalletProvider } from "./wallets/walletProvider.js";
export { EVMWalletProvider } from "./wallets/evmWalletProvider.js";

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
