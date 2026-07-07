/**
 * Per-account nonce tracker with local caching and auto-recovery.
 *
 * Production-grade nonce management for sequential blockchain transactions:
 *   - Seeds from "pending" on first use (captures in-mempool txs)
 *   - Local increment avoids RPC on every send
 *   - Auto re-syncs on nonce errors (too low, already known, underpriced)
 *   - Safe under concurrent `getNonce()` calls via a promise-chain mutex
 *   - Singleton per (rpcUrl, account) — shared across client instances
 *
 * Port of `python/bnbagent/core/nonce_manager.py`; see that module and its
 * tests (`python/tests/test_nonce_manager.py`) for the authoritative
 * semantics this file mirrors.
 */

import { getAddress } from "viem";

/**
 * Structural shape this module needs from a viem-like client. Kept minimal
 * (rather than requiring a full `PublicClient`) so the manager stays
 * decoupled from viem's client machinery and tests can pass a plain mock
 * object.
 */
export interface NonceManagerClient {
  transport?: { url?: string };
  uid?: string;
  getTransactionCount(args: {
    address: `0x${string}`;
    blockTag: "pending";
  }): Promise<number>;
}

/** Substrings that indicate a nonce-related RPC error. */
export const NONCE_ERROR_PATTERNS = [
  "nonce too low",
  "already known",
  "replacement transaction underpriced",
] as const;

/**
 * A minimal async mutex built from a promise chain: each `run()` call
 * appends its work to the tail of the chain and waits for its turn,
 * guaranteeing exclusive, ordered execution — the async analog of Python's
 * `threading.Lock`.
 */
class AsyncMutex {
  private tail: Promise<unknown> = Promise.resolve();

  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn, fn);
    // Swallow rejections in the chain itself so one failed task doesn't
    // permanently wedge the mutex for later callers; callers still observe
    // the rejection via the returned `result` promise.
    this.tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }
}

/**
 * Thread-safe (async-safe) nonce manager with local tracking and chain
 * re-sync on error.
 *
 * Usage:
 * ```ts
 * const nonceMgr = NonceManager.forAccount(client, accountAddress);
 * const nonce = await nonceMgr.getNonce(); // auto-seeds, then increments locally
 * // ... send tx with nonce ...
 * // on nonce error:
 * if (await nonceMgr.handleError(error, nonce)) {
 *   // retry with a new nonce from getNonce()
 * }
 * ```
 */
export class NonceManager {
  private static instances = new Map<string, NonceManager>();

  private readonly client: NonceManagerClient;
  private readonly account: `0x${string}`;
  private readonly mutex = new AsyncMutex();
  private nonce: number | null = null;

  private constructor(client: NonceManagerClient, account: `0x${string}`) {
    this.client = client;
    this.account = account;
  }

  /**
   * Get or create a NonceManager singleton for this account + RPC endpoint.
   *
   * Two client instances sharing the same wallet and RPC will automatically
   * share the same NonceManager.
   */
  static forAccount(
    client: NonceManagerClient,
    account: `0x${string}`,
  ): NonceManager {
    const checksummed = getAddress(account);
    const rpcUrl = getRpcUrl(client);
    const key = `${rpcUrl}:${checksummed}`;
    let instance = NonceManager.instances.get(key);
    if (!instance) {
      instance = new NonceManager(client, checksummed);
      NonceManager.instances.set(key, instance);
    }
    return instance;
  }

  /** Clear all singleton instances. For testing only. */
  static _clearAll(): void {
    NonceManager.instances.clear();
  }

  /**
   * Get the next nonce to use.
   *
   * The first call seeds from chain ("pending"). Subsequent calls increment
   * locally without RPC. Safe under concurrency — concurrent callers each
   * get a unique nonce.
   */
  async getNonce(): Promise<number> {
    return this.mutex.run(async () => {
      if (this.nonce === null) {
        this.nonce = await this.client.getTransactionCount({
          address: this.account,
          blockTag: "pending",
        });
      }
      const nonce = this.nonce;
      this.nonce += 1;
      return nonce;
    });
  }

  /**
   * Handle a transaction error. Re-syncs the nonce from chain if the error
   * is nonce-related.
   *
   * @param error - The error raised by broadcasting the transaction.
   * @param usedNonce - The nonce that was used in the failed transaction
   * (kept for logging/debugging parity with the Python implementation).
   * @returns True if the error was nonce-related and the caller should retry.
   */
  async handleError(error: unknown, usedNonce: number): Promise<boolean> {
    void usedNonce;
    const errorStr = String(error).toLowerCase();
    if (!NONCE_ERROR_PATTERNS.some((pattern) => errorStr.includes(pattern))) {
      return false;
    }
    await this.mutex.run(async () => {
      this.nonce = await this.client.getTransactionCount({
        address: this.account,
        blockTag: "pending",
      });
    });
    return true;
  }

  /**
   * Force re-sync from chain on the next `getNonce()` call.
   *
   * Useful after submitting transactions outside this manager.
   */
  reset(): void {
    this.nonce = null;
  }
}

/** Extract an RPC URL (or a client-identity fallback) for singleton keying. */
function getRpcUrl(client: NonceManagerClient): string {
  return client.transport?.url ?? String(client.uid);
}
