/**
 * `TwakX402Payer` — delegated x402 payments through the twak CLI. Port of
 * `python/bnbagent/x402/twak.py`.
 *
 * This is the SDK-primitives guard layer of the three-layer defense in
 * depth: the application policy layer (host allowlists, USD budgets) sits
 * above and calls this payer; twak's own `--max-payment` hard cap sits
 * below — the per-call cap is therefore enforced **twice by design**.
 * `SigningPolicy` itself cannot run on this path (the EIP-712 payload is
 * built, signed and discarded inside the twak process), so each of its
 * rules has a semantic equivalent in the quote-terms precheck: domain ↔
 * `asset`, validity window ↔ `maxTimeoutSeconds`.
 */

import { SessionBudgetTracker } from "../../x402/budget.js";
import {
  X402AmountExceededError,
  X402NoPayableRouteError,
  X402PolicyError,
  X402RecipientMismatchError,
} from "../../x402/errors.js";
import {
  type X402Payer,
  type X402PaymentResult,
  type X402Quote,
  quoteFromCli,
} from "../../x402/payer.js";
import type { TWAKProvider } from "./provider.js";

/**
 * Default cap on the challenge's claimed `maxTimeoutSeconds` (design
 * F-2). Deliberately wider than SigningPolicy's 600s window:
 * field-verified endpoints advertise 3600s, and on the twak path the
 * *actual* signing validity window is set by twak internally — this
 * check only constrains what the challenge claims.
 */
export const DEFAULT_MAX_TIMEOUT_SECONDS = 3600;

/** Constructor options for {@link TwakX402Payer}. */
export interface TwakX402PayerOptions {
  /**
   * `{tokenAddress: totalBaseUnits}` cumulative spend caps, keyed by
   * token (asset) address — same semantics as `X402Signer`'s
   * `sessionBudget`. An existing {@link SessionBudgetTracker} may be
   * passed instead to share one budget across payers/signers.
   */
  sessionBudget?: Record<string, bigint> | SessionBudgetTracker;
  /** When set, the quoted `payTo` must byte-equal this address. */
  expectedPayTo?: string;
  /**
   * When set, the quoted `asset` must equal this token address (for
   * EIP-3009 the asset IS the EIP-712 `verifyingContract` — this is the
   * SigningPolicy domain allowlist relocated to the quote terms).
   */
  expectedAsset?: string;
  /** Reject challenges claiming a wider payment window (default 3600s). */
  maxTimeoutSeconds?: number;
}

/**
 * X402Payer backed by the twak CLI's built-in x402 client (see module
 * docstring). Construct via `TWAKProvider.makeX402Payer()`.
 */
export class TwakX402Payer implements X402Payer {
  readonly #provider: TWAKProvider;
  readonly #budget: SessionBudgetTracker | null;
  readonly #expectedPayTo: string | undefined;
  readonly #expectedAsset: string | undefined;
  readonly #maxTimeoutSeconds: number;

  constructor(provider: TWAKProvider, opts: TwakX402PayerOptions = {}) {
    this.#provider = provider;
    this.#budget =
      opts.sessionBudget === undefined
        ? null
        : opts.sessionBudget instanceof SessionBudgetTracker
          ? opts.sessionBudget
          : new SessionBudgetTracker(opts.sessionBudget);
    this.#expectedPayTo = opts.expectedPayTo;
    this.#expectedAsset = opts.expectedAsset;
    this.#maxTimeoutSeconds =
      opts.maxTimeoutSeconds ?? DEFAULT_MAX_TIMEOUT_SECONDS;
  }

  /**
   * Fetch the 402 challenge for `url`. Read-only: never triggers wallet
   * creation (the provider's `x402Quote` skips the existence check).
   */
  async quote(
    url: string,
    opts?: { method?: string; body?: string },
  ): Promise<X402Quote> {
    return quoteFromCli(await this.#provider.x402Quote(url, opts));
  }

  /**
   * Fetch `url`, paying up to `maxPayment` atomic units.
   *
   * Flow: quote → pick route → five-point precheck → reserve budget →
   * `twak x402 request` pinned to the prechecked route → result built
   * from the quoted terms (the CLI surfaces no settlement receipt).
   *
   * TOCTOU note: twak re-discovers the challenge between our quote and
   * its payment. `--prefer-network`/`--prefer-asset` pin the route and
   * `--max-payment` caps the damage, narrowing (not eliminating) the
   * window in which the server could swap terms.
   */
  async request(
    url: string,
    opts: {
      maxPayment: bigint;
      method?: string;
      body?: string;
      preferMethod?: string;
      autoApprove?: boolean;
    },
  ): Promise<X402PaymentResult> {
    const quoted = await this.quote(url, {
      ...(opts.method !== undefined ? { method: opts.method } : {}),
      ...(opts.body !== undefined ? { body: opts.body } : {}),
    });
    if (quoted.accepts.length === 0) {
      throw new X402NoPayableRouteError(
        `no payable route for ${url}: the quote's accepts list is empty (the twak client filters out routes on chains it cannot pay)`,
      );
    }
    const option = quoted.accepts.find((o) => o.preferred) ?? quoted.accepts[0];

    // --- five-point precheck on the quoted terms -----------------------
    // 1. payTo: byte-equal vs the caller's committed recipient.
    if (
      this.#expectedPayTo !== undefined &&
      option.payTo.toLowerCase() !== this.#expectedPayTo.toLowerCase()
    ) {
      throw new X402RecipientMismatchError(
        `quoted payTo ${option.payTo} != expected ${this.#expectedPayTo}`,
      );
    }
    // 2. asset ↔ EIP-712 verifyingContract (domain allowlist equivalent).
    if (
      this.#expectedAsset !== undefined &&
      option.asset.toLowerCase() !== this.#expectedAsset.toLowerCase()
    ) {
      throw new X402PolicyError(
        `quoted asset ${option.asset} != expected ${this.#expectedAsset} (asset is the EIP-712 verifyingContract for EIP-3009 routes)`,
      );
    }
    // 3. amount: per-call cap (re-enforced below by twak --max-payment).
    //    Both bounds. The quoted amount comes straight out of an untrusted 402
    //    challenge, and the reserve() below feeds it to the session counter — a
    //    negative would shrink the counter instead of growing it. reserve()
    //    refuses negatives too, but this is the surface that owns the amount,
    //    so it should say so with the amount error rather than a budget error,
    //    and it still has to hold when no budget is configured at all.
    if (option.amount < 0n) {
      throw new X402AmountExceededError(
        `quoted amount ${option.amount} is negative for ${option.asset}`,
      );
    }
    if (option.amount > opts.maxPayment) {
      throw new X402AmountExceededError(
        `quoted amount ${option.amount} exceeds maxPayment ${opts.maxPayment} for ${option.asset}`,
      );
    }
    // 4. claimed validity window (when the option carries one).
    if (option.maxTimeoutSeconds !== null && option.maxTimeoutSeconds < 0) {
      throw new X402PolicyError(
        `quoted maxTimeoutSeconds ${option.maxTimeoutSeconds} is negative`,
      );
    }
    if (
      option.maxTimeoutSeconds !== null &&
      option.maxTimeoutSeconds > this.#maxTimeoutSeconds
    ) {
      throw new X402PolicyError(
        `quoted maxTimeoutSeconds ${option.maxTimeoutSeconds} exceeds the ` +
          `configured cap ${this.#maxTimeoutSeconds}`,
      );
    }
    // 5. network/asset route pinning: passed through as --prefer-network /
    //    --prefer-asset (TOCTOU narrowing — twak re-discovers the
    //    challenge and must land on the route we prechecked).

    // Reserve the QUOTED amount before the slow CLI call, roll back on
    // failure (the CLI surfaces no settlement receipt to reconcile with).
    this.#budget?.reserve(option.asset, option.amount);
    let response: Record<string, unknown>;
    try {
      response = await this.#provider.x402Request(url, {
        maxPayment: opts.maxPayment,
        ...(opts.method !== undefined ? { method: opts.method } : {}),
        ...(opts.body !== undefined ? { body: opts.body } : {}),
        preferNetwork: option.network,
        preferAsset: option.asset,
        ...(opts.preferMethod !== undefined
          ? { preferMethod: opts.preferMethod }
          : {}),
        ...(opts.autoApprove !== undefined
          ? { autoApprove: opts.autoApprove }
          : {}),
      });
    } catch (error) {
      this.#budget?.rollback(option.asset, option.amount);
      throw error;
    }

    const transaction = extractTxHash(response);
    return {
      success: true,
      response,
      amount: option.amount,
      asset: option.asset,
      network: option.network,
      payTo: option.payTo,
      ...(transaction ? { transaction } : {}),
    };
  }
}

/** Best-effort tx hash from the endpoint body (most bodies have none). */
function extractTxHash(response: unknown): string | undefined {
  if (response && typeof response === "object") {
    const r = response as Record<string, unknown>;
    const tx = r.tx_hash ?? r.txHash;
    return tx ? String(tx) : undefined;
  }
  return undefined;
}
