/**
 * X402Signer — x402-specific signing wrapper around a typed-data signer.
 *
 * The wallet's `SigningPolicy` defends against the *structural* class of
 * blind-sign attacks (unknown domain, denylisted primary type, validity
 * window). X402Signer adds the *transactional* layer on top:
 *
 * - expected-recipient byte-equal verification (the LLM cannot quietly
 *   redirect a payment by altering `message.to`)
 * - per-call max-value cap per token (so a malicious 402 challenge with an
 *   inflated `value` is rejected before signing)
 * - session-cumulative budget tracker (rate-limits a compromised agent even
 *   if individual calls are within max-value)
 *
 * x402 SchemeExactEVM and EIP-3009 `TransferWithAuthorization` are the
 * primary intended primary types; callers signing other types via this
 * wrapper should ensure the message has `to` and `value` fields with the
 * same semantics.
 *
 * Port of `python/bnbagent/x402/signer.py`.
 */

import { getAddress as toChecksumAddress } from "viem";

import { PolicyViolation } from "../signing/errors.js";
import { SIGN_TYPED_DATA } from "../wallets/capabilities.js";
import { UnsupportedWalletOperation } from "../wallets/errors.js";
import type { SignatureResult } from "../wallets/walletProvider.js";
import { SessionBudgetTracker } from "./budget.js";
import {
  X402AmountExceededError,
  X402PolicyError,
  X402RecipientMismatchError,
} from "./errors.js";

/**
 * The narrow contract X402Signer actually depends on.
 *
 * Matching is structural: any object with the right shape can be passed —
 * no inheritance from `WalletProvider` required (mirrors coinbase x402's
 * `ClientEvmSigner`: "deliberately tiny, structural — the entire wallet
 * contract for the buyer"). `supports` is optional; when present and
 * callable it is consulted at `X402Signer` construction time.
 */
export interface TypedDataSigner {
  readonly address: string;
  signTypedData(
    domain: Record<string, unknown>,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult>;
  supports?(capability: string): boolean;
}

/** Constructor options for {@link X402Signer}. */
export interface X402SignerOptions {
  /**
   * `{tokenAddress: maxBaseUnits}` cap on `message.value` for any
   * `signPayment` call against that token (e.g. `{U_MAINNET: 1_000_000n}`
   * for 1 USDC-scale unit; choose units consistent with the on-chain
   * token). Missing token → no per-call cap.
   */
  maxValuePerCall?: Record<string, bigint>;
  /**
   * `{tokenAddress: totalBaseUnits}` cap on cumulative spend across all
   * `signPayment` calls in this signer's lifetime. Independent of the
   * per-call cap.
   */
  sessionBudget?: Record<string, bigint>;
}

/** Arguments to {@link X402Signer.signPayment}. */
export interface SignPaymentOptions {
  /**
   * EIP-712 domain; `chainId` + `verifyingContract` must be present
   * (checked by the wallet's `SigningPolicy`).
   */
  domain: Record<string, unknown>;
  /** EIP-712 types dict. */
  types: Record<string, { name: string; type: string }[]>;
  /**
   * Struct values. Must include `to` and `from` for X402Signer's
   * recipient/signer-binding guards, and `value` for the amount guards
   * (defaults to `0` if absent).
   */
  message: Record<string, unknown>;
  /**
   * Address the caller commits to as the payee. Compared byte-equal
   * (case-insensitive) against `message.to`. Any drift →
   * `X402RecipientMismatchError`.
   */
  expectedTo: string;
}

/**
 * Constrained signer for x402 payment flows.
 *
 * Construct once per wallet (any {@link TypedDataSigner}) per scope/session.
 * Pass the resulting signer to agent tool functions instead of the raw
 * wallet — the closure then cannot bypass the policy stack.
 */
export class X402Signer {
  readonly #wallet: TypedDataSigner;
  readonly #maxValue = new Map<string, bigint>();
  readonly #budget: SessionBudgetTracker;

  /**
   * @param wallet The underlying wallet. Anything matching the narrow
   *   {@link TypedDataSigner} shape (`address` + `signTypedData`) works —
   *   no `WalletProvider` inheritance required. The wallet's own
   *   `SigningPolicy` still applies — X402Signer never bypasses it.
   *
   * @throws {UnsupportedWalletOperation} The wallet exposes `supports()`
   *   and reports no `sign.typed_data` capability — rejected here at
   *   composition time, before any payment flow runs. Duck-typed signers
   *   without `supports()` pass through (the runtime sign call is the gate
   *   for those).
   */
  constructor(wallet: TypedDataSigner, opts: X402SignerOptions = {}) {
    // Composition-time gate: a capability-aware wallet that cannot sign
    // EIP-712 is rejected immediately, with a pointer to the delegated
    // path. Duck-typed objects without `supports()` pass through unchanged
    // — the narrow-protocol use case; the runtime gates take over there.
    const { supports } = wallet;
    if (
      typeof supports === "function" &&
      !supports.call(wallet, SIGN_TYPED_DATA)
    ) {
      throw new UnsupportedWalletOperation(SIGN_TYPED_DATA, {
        reason:
          "this wallet cannot sign EIP-712 typed data, so X402Signer cannot produce x402 payment signatures",
        alternative:
          "use the wallet's delegated x402 payer instead (wallet kinds that sign payments internally, e.g. twak)",
      });
    }
    this.#wallet = wallet;
    if (opts.maxValuePerCall) {
      for (const [addr, cap] of Object.entries(opts.maxValuePerCall)) {
        this.#maxValue.set(toChecksumAddress(addr as `0x${string}`), cap);
      }
    }
    this.#budget = new SessionBudgetTracker(opts.sessionBudget);
  }

  get walletAddress(): string {
    return this.#wallet.address;
  }

  get budget(): SessionBudgetTracker {
    return this.#budget;
  }

  /**
   * Sign an x402 / EIP-3009 payment after all guards pass.
   *
   * @throws {X402RecipientMismatchError} `message.to` (or `message.from`)
   *   is missing, invalid, or differs from the expected address.
   * @throws {X402AmountExceededError} `message.value` exceeds the per-call
   *   `maxValuePerCall` for this token.
   * @throws {X402BudgetExhaustedError} The session budget would be
   *   exceeded.
   * @throws {X402PolicyError} Wraps an underlying `PolicyViolation` from
   *   the wallet's `SigningPolicy`, or a malformed/missing
   *   `domain.verifyingContract`.
   * @throws {X402AmountExceededError} `message.value` is not a valid integer
   *   amount, is negative, or exceeds the per-call `maxValuePerCall` for this
   *   token.
   */
  async signPayment(opts: SignPaymentOptions): Promise<SignatureResult> {
    const { domain, types, message, expectedTo } = opts;
    // A malformed/missing verifyingContract would otherwise throw a raw viem
    // InvalidAddressError, escaping the documented X402 error contract.
    let verifying: `0x${string}`;
    try {
      verifying = toChecksumAddress(domain.verifyingContract as `0x${string}`);
    } catch (e) {
      throw new X402PolicyError(
        `invalid or missing verifyingContract in EIP-712 domain: ${JSON.stringify(domain.verifyingContract)}`,
        { cause: e },
      );
    }

    // ── L0 recipient (cheapest check, fail fast) ────────────────────────
    const msgTo = message.to;
    if (typeof msgTo !== "string") {
      throw new X402RecipientMismatchError(
        `message.to is missing or not an address: ${JSON.stringify(msgTo)}`,
      );
    }
    if (msgTo.toLowerCase() !== expectedTo.toLowerCase()) {
      throw new X402RecipientMismatchError(
        `expectedTo=${expectedTo} does not match message.to=${msgTo} — refusing to sign`,
      );
    }

    // ── L1 per-call value cap ────────────────────────────────────────────
    // BigInt() throws a raw RangeError/SyntaxError on a float or non-numeric
    // value; surface it as a documented X402 error instead.
    let value: bigint;
    try {
      value = BigInt((message.value ?? 0) as bigint | number | string);
    } catch (e) {
      throw new X402AmountExceededError(
        `message.value is not a valid integer amount: ${JSON.stringify(message.value)}`,
        { cause: e },
      );
    }
    // BigInt() widens rather than validates: the declared uint256 field-shape
    // is not enforced here, so a negative slips through the one-sided cap test
    // below and neutralises the session budget. Refuse before reserve() so a
    // rejected call still costs no budget.
    if (value < 0n) {
      throw new X402AmountExceededError(
        `message.value must be non-negative, got ${value}`,
      );
    }
    const cap = this.#maxValue.get(verifying);
    if (cap !== undefined && value > cap) {
      throw new X402AmountExceededError(
        `value ${value} exceeds max_value_per_call=${cap} for token ${verifying}`,
      );
    }

    // ── L1.5 signer binding: message.from must be this wallet ───────────
    // A forged 'from' (with policy-compliant to/value) would otherwise
    // reserve budget and sign. On-chain EIP-3009 rejects the mismatched
    // signer, but the session budget is already spent — a DoS on payment
    // capability. Check before reserve() so rejected calls cost nothing.
    const msgFrom = message.from;
    if (typeof msgFrom !== "string") {
      throw new X402RecipientMismatchError(
        `message.from is missing or not an address: ${JSON.stringify(msgFrom)}`,
      );
    }
    let walletCs: string;
    let msgFromCs: string;
    try {
      walletCs = toChecksumAddress(this.#wallet.address as `0x${string}`);
      msgFromCs = toChecksumAddress(msgFrom as `0x${string}`);
    } catch {
      throw new X402RecipientMismatchError(
        `message.from is not a valid address: ${JSON.stringify(msgFrom)}`,
      );
    }
    if (msgFromCs !== walletCs) {
      throw new X402RecipientMismatchError(
        `message.from=${msgFromCs} does not match wallet ${walletCs} — refusing to sign`,
      );
    }

    // ── L2 session budget (atomic reserve; rollback on any failure) ─────
    // reserve() does the check+increment synchronously with no `await` in
    // between, so two concurrent signPayment calls cannot both pass the
    // budget check and overspend. The reservation is released by
    // rollback() if the downstream sign fails — preserving "rejected signs
    // never consume budget" under interleaved-async concurrency.
    this.#budget.reserve(verifying, value);

    // ── L3 wallet sign (SigningPolicy enforces here) ────────────────────
    try {
      return await this.#wallet.signTypedData(domain, types, message);
    } catch (e) {
      this.#budget.rollback(verifying, value);
      if (e instanceof PolicyViolation) {
        // Re-raise as X402-layer error for caller convenience while
        // preserving full context via `cause`.
        throw new X402PolicyError(e.message, { cause: e });
      }
      // Any other failure must release the reservation; re-thrown as-is so
      // the budget never silently locks up.
      throw e;
    }
  }
}
