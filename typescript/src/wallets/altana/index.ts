/**
 * Altana wallet provider surface.
 *
 * Note the deliberate omission: the internal SDK-module mirrors
 * (`AltanaSdkModule` / `AltanaSdkClient` and the execute-option shapes)
 * stay out of the barrel — they type the lazy `@altananetwork/sdk`
 * boundary, not the public API.
 */

export {
  AgentAuthorizationPolicy,
  DEFAULT_AGENT_AUTHORIZATION_ROLES,
  DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
  defaultAgentPermissions,
} from "./permissions.js";
export type {
  AgentAuthorizationPolicyOpts,
  AgentAuthorizationRole,
  AgentPermissionTargets,
  DefaultAgentPermissionsOpts,
  SpendCap,
  TokenSpendCap,
  StrictAgentCallPermission,
} from "./permissions.js";
export {
  ALTANA_NONCE_RETRY_DELAY_MS,
  ALTANA_NONCE_RETRY_TRIES,
  AltanaIntentExecutor,
  AltanaWalletProvider,
} from "./provider.js";
export type {
  AltanaAdminFromKeystoreOpts,
  AltanaGrantSessionProviderOpts,
  AltanaSessionFromEnvOpts,
  AltanaWalletProviderOptions,
} from "./provider.js";
export { DEFAULT_PERMIT2_LOG_CHUNK, findDelegationBlock } from "./permit2.js";
export type {
  AltanaListPermit2AllowancesOpts,
  AltanaPermit2Allowance,
  AltanaPermit2CleanupOpts,
  AltanaPermit2CleanupResult,
  AltanaPermit2Pair,
  AltanaPermit2ReapproveCleanupOpts,
  AltanaPermit2ResetResult,
  AltanaRevokeOpts,
  AltanaRevokeResult,
  AltanaSetPermit2AllowanceOpts,
  AltanaSetPermit2AllowanceResult,
} from "./permit2.js";
export {
  ALTANA_SDK_PACKAGE,
  ALTANA_SDK_TESTED_VERSION,
  setAltanaSdkImporter,
} from "./sdkLoader.js";
export type { AltanaSdkImporter } from "./sdkLoader.js";
export {
  ALTANA_SESSION_VERSION,
  deserializeSession,
  serializeSession,
} from "./session.js";
export {
  AltanaX402Payer,
  chainIdFromX402Network,
  X_PAYMENT_HEADER,
  X_PAYMENT_RESPONSE_HEADER,
} from "./x402.js";
export type { AltanaX402PayerOptions } from "./x402.js";
export type {
  AltanaBalancesResult,
  AltanaCall,
  AltanaCallPermission,
  AltanaExecuteResult,
  AltanaNetwork,
  AltanaNetworkConfig,
  AltanaRegisterSessionKeyResult,
  AltanaSession,
  AltanaSessionPermissions,
  AltanaSigner,
  AltanaSignerType,
  AltanaSpendPermission,
  AltanaTokenBalance,
  AltanaWallet,
} from "./types.js";
