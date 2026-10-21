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

import { getAddress } from "viem";
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
import {
  TwakPaymentNotSentError,
  type TwakPaymentTerms,
  TwakPaymentTermsMismatchError,
} from "./confirm.js";
import type { TWAKProvider } from "./provider.js";

/**
 * Default cap on the challenge's claimed `maxTimeoutSeconds` (design
 * F-2). Deliberately wider than SigningPolicy's 600s window:
 * field-verified endpoints advertise 3600s, and on the twak path the
 * *actual* signing validity window is set by twak internally — this
 * check only constrains what the challenge claims.
 */
export const DEFAULT_MAX_TIMEOUT_SECONDS = 3600;

/** twak's default when a challenge entry names no transfer method. */
const DEFAULT_TRANSFER_METHOD = "eip3009";

/** Normalise a transfer method the way twak's `canonicalMethod` does. */
function canonicalMethod(method: string | undefined): string {
  const lower = (method ?? DEFAULT_TRANSFER_METHOD).toLowerCase();
  return lower === "permit2" ? "permit2-exact" : lower;
}

/** Constructor options for {@link TwakX402Payer}. */
export interface TwakX402PayerOptions {
  /**
   * `{tokenAddress: totalBaseUnits}` cumulative spend caps, keyed by
   * token (asset) address — same semantics as `X402Signer`'s
   * `sessionBudget`. An existing {@link SessionBudgetTracker} may be
   * passed instead to share one budget across payers/signers.
   */
  sessionBudget?: Record<string, bigint> | SessionBudgetTracker;
  /** Trusted recipient anchor; the quoted `payTo` must byte-equal it. */
  expectedPayTo: string;
  /**
   * The quoted `asset` must equal this trusted token address (for
   * EIP-3009 the asset IS the EIP-712 `verifyingContract` — this is the
   * SigningPolicy domain allowlist relocated to the quote terms).
   */
  expectedAsset: string;
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
  readonly #expectedPayTo: `0x${string}`;
  readonly #expectedAsset: `0x${string}`;
  readonly #maxTimeoutSeconds: number;

  constructor(provider: TWAKProvider, opts: TwakX402PayerOptions) {
    if (opts === undefined) {
      throw new Error(
        "TwakX402Payer requires expectedPayTo and expectedAsset trusted anchors",
      );
    }
    this.#provider = provider;
    this.#budget =
      opts.sessionBudget === undefined
        ? null
        : opts.sessionBudget instanceof SessionBudgetTracker
          ? opts.sessionBudget
          : new SessionBudgetTracker(opts.sessionBudget);
    this.#expectedPayTo = getAddress(opts.expectedPayTo);
    this.#expectedAsset = getAddress(opts.expectedAsset);
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
   * its payment. `--prefer-network`/`--prefer-asset`/`--prefer-method` pin
   * the route and `--max-payment` is set to the prechecked quoted amount
   * (not `maxPayment`), so twak cannot pay more than the amount the session
   * budget reserved. twak's pre-signing prompt is then confirmed only if
   * its final amount, asset, network and payTo equal the prechecked ones
   * (a drifted route throws before anything is signed).
   *
   * Budget: the reservation is released only when no payment can have
   * been sent (twak failed before the confirmation, reported the payment
   * cancelled / Permit2 approval required, or, on the verified twak
   * release, returned a response without asking to sign). Once the payment was
   * confirmed, a failure keeps the amount reserved and throws
   * `TwakPaymentStateUnknownError`; a paid response is returned verbatim,
   * even if its body reports an error.
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
    // twak applies --prefer-method before choosing the route it pays, so the
    // route must be filtered the same way BEFORE the precheck below;
    // otherwise the precheck validates one route and twak pays another.
    const methodPref =
      opts.preferMethod === undefined
        ? undefined
        : canonicalMethod(opts.preferMethod);
    const candidates =
      methodPref === undefined
        ? quoted.accepts
        : quoted.accepts.filter(
            (o) => canonicalMethod(o.transferMethod) === methodPref,
          );
    if (candidates.length === 0) {
      throw new X402NoPayableRouteError(
        `no payable route for ${url} with transfer method "${methodPref}"`,
      );
    }
    const option = candidates.find((o) => o.preferred) ?? candidates[0];

    // --- five-point precheck on the quoted terms -----------------------
    // 1. payTo: byte-equal vs the caller's committed recipient.
    if (option.payTo.toLowerCase() !== this.#expectedPayTo.toLowerCase()) {
      throw new X402RecipientMismatchError(
        `quoted payTo ${option.payTo} != expected ${this.#expectedPayTo}`,
      );
    }
    // 2. asset ↔ EIP-712 verifyingContract (domain allowlist equivalent).
    if (option.asset.toLowerCase() !== this.#expectedAsset.toLowerCase()) {
      throw new X402PolicyError(
        `quoted asset ${option.asset} != expected ${this.#expectedAsset} (asset is the EIP-712 verifyingContract for EIP-3009 routes)`,
      );
    }
    // 3. amount: per-call cap. twak's --max-payment below is the quoted
    //    amount itself, which is at most this cap.
    if (option.amount > opts.maxPayment) {
      throw new X402AmountExceededError(
        `quoted amount ${option.amount} exceeds maxPayment ${opts.maxPayment} for ${option.asset}`,
      );
    }
    // 4. claimed validity window (when the option carries one).
    if (
      option.maxTimeoutSeconds !== null &&
      option.maxTimeoutSeconds > this.#maxTimeoutSeconds
    ) {
      throw new X402PolicyError(
        `quoted maxTimeoutSeconds ${option.maxTimeoutSeconds} exceeds the ` +
          `configured cap ${this.#maxTimeoutSeconds}`,
      );
    }
    // 5. network/asset/method route pinning: passed through as
    //    --prefer-network / --prefer-asset / --prefer-method (TOCTOU
    //    narrowing — twak re-discovers the challenge and must land on the
    //    route we prechecked; its prompt does not show the transfer method).

    // Reserve the QUOTED amount before the slow CLI call, roll back only if
    // nothing can have been sent (the CLI surfaces no settlement receipt to
    // reconcile with).
    this.#budget?.reserve(option.asset, option.amount);
    let response: unknown;
    const signed: TwakPaymentTerms[] = [];
    // Keep only the confirmed amount reserved when twak signed less than quoted.
    const releaseUnsigned = () => {
      const paid = signed[0]?.amount;
      if (paid !== undefined && paid < option.amount) {
        this.#budget?.rollback(option.asset, option.amount - paid);
      }
    };
    try {
      // twak re-fetches the challenge before paying and only checks its
      // amount against --max-payment; capping it at the reserved quote keeps
      // the budget ledger an upper bound on what twak can pay.
      response = await this.#provider.x402Request(url, {
        maxPayment: option.amount,
        ...(opts.method !== undefined ? { method: opts.method } : {}),
        ...(opts.body !== undefined ? { body: opts.body } : {}),
        preferNetwork: option.network,
        preferAsset: option.asset,
        preferMethod: canonicalMethod(option.transferMethod),
        ...(opts.autoApprove !== undefined
          ? { autoApprove: opts.autoApprove }
          : {}),
        // twak shows the re-fetched challenge's final terms before
        // signing; the provider confirms only if they match these (the
        // amount may be lower) and reports what it confirmed.
        expectedTerms: {
          amount: option.amount,
          asset: option.asset,
          network: option.network,
          payTo: option.payTo,
        },
        onConfirm: (terms) => signed.push(terms),
      });
    } catch (error) {
      if (error instanceof TwakPaymentTermsMismatchError) {
        this.#budget?.rollback(option.asset, option.amount);
        throw termsMismatch(error, option.payTo);
      }
      if (error instanceof TwakPaymentNotSentError) {
        this.#budget?.rollback(option.asset, option.amount);
        throw error.error;
      }
      releaseUnsigned();
      throw error;
    }

    releaseUnsigned();
    const transaction = extractTxHash(response);
    return {
      success: true,
      response,
      amount: signed[0]?.amount ?? option.amount,
      asset: option.asset,
      network: option.network,
      payTo: option.payTo,
      ...(transaction ? { transaction } : {}),
    };
  }
}

function termsMismatch(
  error: TwakPaymentTermsMismatchError,
  quotedPayTo: string,
): Error {
  if (error.field === "payTo") {
    return new X402RecipientMismatchError(
      `twak's final payTo ${error.actual} != quoted ${quotedPayTo}; declined before signing`,
    );
  }
  if (error.field === "amount") {
    return new X402PolicyError(
      `twak's final payment amount ${error.actual} exceeds quoted ${error.expected}; declined before signing`,
      { cause: error },
    );
  }
  if (error.field === "prompt") {
    return new X402PolicyError(
      `twak's confirmation prompt was not recognised (${JSON.stringify(error.actual)}); declined before signing`,
      { cause: error },
    );
  }
  return new X402PolicyError(
    `twak's final payment ${error.field} ${JSON.stringify(error.actual)} != quoted ${JSON.stringify(error.expected)}; declined before signing`,
    { cause: error },
  );
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
