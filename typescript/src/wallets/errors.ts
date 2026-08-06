/**
 * Wallet-layer exceptions.
 *
 * `UnsupportedWalletOperation` is the descriptive error raised when a wallet
 * backend cannot service an operation (e.g. a fixed-command-menu wallet
 * asked for arbitrary signing).
 *
 * `WalletIdentityMismatch` is raised when a provider pinned to an
 * `expectedAddress` discovers the backend wallet resolves to a different
 * address (INV-4: never proceed with a drifted on-chain identity).
 *
 * Port of `python/bnbagent/wallets/errors.py`.
 */

/**
 * A wallet backend cannot perform the requested capability/operation.
 *
 * The message is assembled from the capability (or operation) name, the
 * reason it is unsupported, an optional alternative path, and an optional
 * free-form reference pointer (`ref`). Constructing with just the first
 * positional argument uses it verbatim as the message.
 */
export class UnsupportedWalletOperation extends Error {
  constructor(
    capabilityOrOperation: string,
    opts?: { reason?: string; alternative?: string; ref?: string },
  ) {
    let message = capabilityOrOperation;
    if (opts?.reason) {
      message = `${capabilityOrOperation}: ${opts.reason}.`;
    }
    if (opts?.alternative) {
      message += ` Alternative: ${opts.alternative}.`;
    }
    if (opts?.ref) {
      message += ` (ref: ${opts.ref})`;
    }
    super(message);
    this.name = "UnsupportedWalletOperation";
  }
}

/**
 * The wallet resolved to a different address than the caller pinned.
 *
 * Raised on the first address lookup when a provider was constructed with
 * an `expectedAddress` and the backend reports another address. Addresses
 * are public, so both are printed in full. The usual cause in deployment is
 * a stale (or wrong-environment) secret-bundle version materializing old
 * key material — fix the bundle, never the pin (INV-4).
 */
export class WalletIdentityMismatch extends Error {
  readonly expected: string;
  readonly actual: string;

  constructor(opts: { expected: string; actual: string }) {
    super(
      `wallet identity mismatch: expected ${opts.expected} but the wallet reports ${opts.actual}. The key material backing this wallet is not the pinned identity — most often a stale or wrong secret-bundle version (e.g. the TWAK_WALLET_JSON entry) was materialized. Refusing to operate under a drifted on-chain identity.`,
    );
    this.name = "WalletIdentityMismatch";
    this.expected = opts.expected;
    this.actual = opts.actual;
  }
}
