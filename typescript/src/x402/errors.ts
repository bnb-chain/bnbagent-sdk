/**
 * Errors raised by X402Signer.
 *
 * Port of `python/bnbagent/x402/errors.py`.
 */

/**
 * Base class for X402Signer-layer refusals.
 *
 * Accepts (and forwards) the standard `ErrorOptions.cause` so subclasses
 * further down the chain (e.g. {@link X402PolicyError}) can chain an
 * underlying error without it being swallowed by this base constructor.
 */
export class X402SignerError extends Error {
  constructor(message?: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "X402SignerError";
  }
}

/**
 * `message.to` did not byte-equal the caller-supplied `expectedTo`.
 *
 * Forces the caller to commit to a destination address before invoking the
 * signer; defends against an upstream LLM tool quietly altering the payee
 * in a 402 challenge. Also raised when `message.from` does not match the
 * wallet's own address (the "signer binding" guard).
 */
export class X402RecipientMismatchError extends X402SignerError {
  constructor(message?: string) {
    super(message);
    this.name = "X402RecipientMismatchError";
  }
}

/** `message.value` exceeded the per-call `maxValuePerCall` for this token. */
export class X402AmountExceededError extends X402SignerError {
  constructor(message?: string) {
    super(message);
    this.name = "X402AmountExceededError";
  }
}

/** The session budget for this token would be exceeded by this call. */
export class X402BudgetExhaustedError extends X402SignerError {
  constructor(message?: string) {
    super(message);
    this.name = "X402BudgetExhaustedError";
  }
}

/** A SigningPolicy violation surfaced from the underlying wallet. */
export class X402PolicyError extends X402SignerError {
  constructor(message?: string, opts?: { cause?: unknown }) {
    super(
      message,
      opts?.cause !== undefined ? { cause: opts.cause } : undefined,
    );
    this.name = "X402PolicyError";
  }
}

/**
 * The quote's `accepts` list held no route this client can pay.
 *
 * The quoting client filters out routes on chains it does not support, so
 * an empty list means the endpoint and the wallet share no network.
 */
export class X402NoPayableRouteError extends X402SignerError {
  constructor(message?: string) {
    super(message);
    this.name = "X402NoPayableRouteError";
  }
}
