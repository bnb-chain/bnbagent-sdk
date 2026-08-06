/**
 * In-memory per-token session budget tracker for X402Signer.
 *
 * Tracks cumulative spending per checksum-normalized token contract within
 * the lifetime of a single X402Signer instance. Caps are configured at
 * construction.
 *
 * Concurrency model: JavaScript is single-threaded, but `signPayment` still
 * awaits the (possibly slow) underlying `signTypedData` call — so two
 * `signPayment` calls started concurrently (e.g. via `Promise.all`) can
 * interleave. The canonical safe-under-concurrency API is {@link reserve} +
 * {@link rollback}: `reserve` performs a synchronous check-and-increment
 * with no `await` between the check and the increment, so no interleaving
 * point exists for a second caller to observe stale `spent`. The caller
 * does the slow work (signing) *after* `reserve` returns and calls
 * `rollback` if anything fails — preserving the "rejected signs never
 * consume budget" invariant.
 *
 * Port of `python/bnbagent/x402/budget.py` (the thread-`Lock`-guarded
 * critical section there becomes "no `await` inside the critical section"
 * here — the JS single-threaded equivalent of atomicity).
 */

import { getAddress as toChecksumAddress } from "viem";

import { X402BudgetExhaustedError } from "./errors.js";

/**
 * Refuse negative amounts — the counter's load-bearing invariant.
 *
 * The cap test is a one-sided `cur + amount > cap`, so a negative `amount`
 * shrinks the sum and passes trivially while driving the counter negative;
 * every later spend then measures against a huge negative baseline and the
 * session cap stops binding. {@link SessionBudgetTracker.rollback} already
 * floors at zero, but that is the decrement side, where the invariant is only
 * defensive — it has to hold on the increment side to mean anything.
 */
function assertNonNegative(amount: bigint): void {
  if (amount < 0n) {
    throw new X402BudgetExhaustedError(
      `budget amount must be non-negative, got ${amount}`,
    );
  }
}

/** Per-token cumulative spend tracker with caps. */
export class SessionBudgetTracker {
  readonly #caps = new Map<string, bigint>();
  readonly #spent = new Map<string, bigint>();

  /**
   * @param caps `{checksumOrRawTokenAddress: maxTotalBaseUnits}`. Addresses
   *   are checksum-normalized on construction; a missing token is treated
   *   as having no cap — i.e. unlimited session spend for that token
   *   (per-call cap is still enforced separately by `X402Signer`).
   */
  constructor(caps?: Record<string, bigint>) {
    if (caps) {
      for (const [addr, cap] of Object.entries(caps)) {
        this.#caps.set(toChecksumAddress(addr as `0x${string}`), cap);
      }
    }
  }

  capFor(token: string): bigint | null {
    const cap = this.#caps.get(toChecksumAddress(token as `0x${string}`));
    return cap ?? null;
  }

  spent(token: string): bigint {
    return this.#spent.get(toChecksumAddress(token as `0x${string}`)) ?? 0n;
  }

  /**
   * Atomic check-and-increment for the session budget.
   *
   * No `await` occurs between reading `spent`/`cap` and writing the new
   * `spent` value, so two `signPayment` calls racing on the same token
   * cannot both observe the pre-increment `spent` and overspend the cap.
   * The caller performs the slow work (signing) after this returns and
   * calls {@link rollback} on failure to release the reservation.
   *
   * @throws {X402BudgetExhaustedError} If the reservation would exceed the
   *   cap, or if `amount` is negative.
   */
  reserve(token: string, amount: bigint): void {
    assertNonNegative(amount);
    const cs = toChecksumAddress(token as `0x${string}`);
    const cap = this.#caps.get(cs);
    const cur = this.#spent.get(cs) ?? 0n;
    if (cap !== undefined && cur + amount > cap) {
      throw new X402BudgetExhaustedError(
        `value ${amount} would exceed session budget for ${cs} (spent ${cur} / cap ${cap})`,
      );
    }
    this.#spent.set(cs, cur + amount);
  }

  /**
   * Release an earlier {@link reserve} (decrement, floored at zero).
   *
   * Pure arithmetic — never throws, even on a redundant double-rollback.
   */
  rollback(token: string, amount: bigint): void {
    const cs = toChecksumAddress(token as `0x${string}`);
    const cur = this.#spent.get(cs) ?? 0n;
    const next = cur - amount;
    this.#spent.set(cs, next > 0n ? next : 0n);
  }
}
