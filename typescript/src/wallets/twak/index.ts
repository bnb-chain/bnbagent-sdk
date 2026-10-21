/**
 * TWAK (Trust Wallet Agent Kit) wallet surface. The internal subprocess
 * seam (`_setTwakExecForTests`) deliberately stays out of the barrel.
 */

export {
  TwakPaymentNotSentError,
  TwakPaymentStateUnknownError,
  TwakPaymentTermsMismatchError,
} from "./confirm.js";
export type { TwakPaymentTerms } from "./confirm.js";
export { materializeTwakHome } from "./custody.js";
export type { MaterializeTwakHomeOpts } from "./custody.js";
export {
  DEFAULT_TWAK_TIMEOUT_MS,
  resolveTwakBin,
  TWAK_CHAIN_FOR_NETWORK,
  TWAK_X402_VERIFIED_CLI,
  TWAKProvider,
} from "./provider.js";
export type { TWAKProviderOptions } from "./provider.js";
export { DEFAULT_MAX_TIMEOUT_SECONDS, TwakX402Payer } from "./x402.js";
export type { TwakX402PayerOptions } from "./x402.js";
