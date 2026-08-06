/**
 * Exceptions raised by SigningPolicy enforcement.
 *
 * Port of `python/bnbagent/signing/errors.py`.
 */

/** Optional structured context attached to a {@link PolicyViolation}. */
export interface PolicyViolationOptions {
  primaryType?: string;
  chainId?: number;
  verifyingContract?: string;
}

/**
 * A SigningPolicy check rejected a `signTypedData` request.
 *
 * Carries structured fields so callers can render user-facing diagnostics or
 * branch on rejection reason without parsing the message string.
 */
export class PolicyViolation extends Error {
  readonly reason: string;
  readonly primaryType?: string;
  readonly chainId?: number;
  readonly verifyingContract?: string;

  constructor(reason: string, opts: PolicyViolationOptions = {}) {
    const parts = [reason];
    if (opts.primaryType) {
      parts.push(`primary_type=${opts.primaryType}`);
    }
    if (opts.chainId !== undefined) {
      parts.push(`chain_id=${opts.chainId}`);
    }
    if (opts.verifyingContract) {
      parts.push(`verifyingContract=${opts.verifyingContract}`);
    }
    super(parts.join("; "));
    this.name = "PolicyViolation";
    this.reason = reason;
    this.primaryType = opts.primaryType;
    this.chainId = opts.chainId;
    this.verifyingContract = opts.verifyingContract;
  }
}
