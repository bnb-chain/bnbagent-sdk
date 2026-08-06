/**
 * Multicall3 batch read utility for EVM chains.
 *
 * Aggregates multiple read-only contract calls into a single RPC request via
 * the canonical Multicall3 contract (deployed at the same address on every
 * EVM chain). This avoids one round trip per call when reading the same
 * function across many arguments (e.g. `getJob(id)` for hundreds of ids).
 *
 * Port of `python/bnbagent/core/multicall.py::multicall_read`; see that
 * module and its tests (`python/tests/test_multicall.py`) for the
 * authoritative semantics this file mirrors. The Python version hand-encodes
 * calldata and hand-decodes `aggregate3`'s return tuples against `eth_abi`;
 * this port instead delegates encode/decode to viem's
 * `publicClient.multicall`, which already speaks Multicall3's `aggregate3`
 * ABI. What *is* kept explicit, to preserve the Python contract's batch-count
 * semantics, is the batching loop itself: viem's own `batchSize` option
 * chunks calls by *encoded calldata byte size*, not call count, so it can't
 * reproduce "N calls / batchSize -> ceil(N/batchSize) RPC calls" on its own.
 * `batchSize: 0` is passed to viem on every call below to disable that
 * internal chunking, leaving exactly one `aggregate3` invocation per batch
 * this module carves out of `callArgsList`.
 */

import type { Abi, ContractFunctionParameters, PublicClient } from "viem";
import { MAX_RETRIES, RETRY_BASE_DELAY } from "./txConfig.js";
import { describeError, isRateLimitError, sleep } from "./txSender.js";

/** Canonical Multicall3 address — deployed at the same address on every EVM chain. */
export const MULTICALL3_ADDRESS: `0x${string}` =
  "0xcA11bde05977b3631167028862bE2a173976CA11";

/** Default max calls per `aggregate3` batch. */
export const DEFAULT_BATCH_SIZE = 100;

/** Options accepted by {@link multicallRead}. */
export interface MulticallReadOpts {
  address: `0x${string}`;
  abi: Abi;
  functionName: string;
  callArgsList: readonly (readonly unknown[])[];
  /** Max calls per `aggregate3` batch. Defaults to {@link DEFAULT_BATCH_SIZE}. */
  batchSize?: number;
  /**
   * When `true` (default), a failed call within a batch surfaces as
   * `[false, null]` alongside the batch's other results. When `false`, any
   * failed call throws instead of being tolerated — mirroring the Python
   * `allow_failure` flag's "one bad call fails the whole read" behavior.
   */
  allowFailure?: boolean;
}

/**
 * Batch-read a single contract function over many argument tuples via
 * Multicall3.
 *
 * Splits `callArgsList` into `batchSize`-sized chunks (default
 * {@link DEFAULT_BATCH_SIZE}) and issues one `publicClient.multicall` per
 * chunk, each independently wrapped in the shared rate-limit retry (5
 * attempts, `RETRY_BASE_DELAY * 2^attempt` backoff on a "429"/"too many
 * requests" error; any other error propagates immediately, aborting the
 * whole read). Results are returned as `[success, result]` pairs in input
 * order.
 *
 * An empty `callArgsList` short-circuits to `[]` before any RPC call.
 *
 * @throws {Error} `Function <name> not found in ABI` before any RPC call, if
 *   `functionName` doesn't name a function on `abi`.
 */
export async function multicallRead(
  client: PublicClient,
  opts: MulticallReadOpts,
): Promise<Array<[boolean, unknown]>> {
  const {
    address,
    abi,
    functionName,
    callArgsList,
    batchSize = DEFAULT_BATCH_SIZE,
    allowFailure = true,
  } = opts;

  // Mirrors the Python port's control flow: the empty-list short-circuit is
  // checked first, so an empty `callArgsList` returns `[]` even for a
  // `functionName` that isn't on `abi` — only a non-empty list pays for
  // validation (and, below, batching/RPC).
  if (callArgsList.length === 0) {
    return [];
  }

  if (
    !abi.some((item) => item.type === "function" && item.name === functionName)
  ) {
    throw new Error(`Function ${functionName} not found in ABI`);
  }

  const results: Array<[boolean, unknown]> = [];

  for (let i = 0; i < callArgsList.length; i += batchSize) {
    const batch = callArgsList.slice(i, i + batchSize);
    const contracts: ContractFunctionParameters[] = batch.map((args) => ({
      address,
      abi,
      functionName,
      args,
    }));

    // `allowFailure: true` (hardcoded, not `opts.allowFailure`) keeps viem's
    // return shape a uniform `{ status, result | error }[]` regardless of
    // this call's `allowFailure` option; the option is applied ourselves
    // below so both branches map to the same `[boolean, unknown]` tuple
    // shape instead of forking on viem's raw-vs-wrapped return type.
    //
    // Caveat this works around: with `allowFailure: true`, viem's own
    // `multicall` action never rethrows a transport-level rejection (a
    // genuine HTTP 429 from the RPC) — it settles each `readContract` call
    // via `Promise.allSettled` and folds a rejection straight into that
    // call's `{ status: "failure", error }` entry. Left alone, `callWithRetry`
    // below would never see the 429 as a thrown error, so a rate-limited
    // batch would silently turn into up to `batchSize` `[false, null]`
    // results instead of being retried. So: scan the resolved entries for a
    // rate-limit signature (walking `.cause` chains, same as
    // `ContractBase.callWithRetry`'s check) and, if found, throw it from
    // inside the retried closure — discarding this batch's partial results
    // and driving the same backoff-and-retry loop a rejected `fn()` would.
    // Non-rate-limit failures are left alone and still map to `[false,
    // null]` below.
    const batchResults = await callWithRetry(async () => {
      const result = await client.multicall({
        contracts,
        allowFailure: true,
        batchSize: 0,
        multicallAddress: MULTICALL3_ADDRESS,
      });
      const rateLimited = result.find(
        (entry) => entry.status === "failure" && isRateLimitError(entry.error),
      );
      if (rateLimited && rateLimited.status === "failure") {
        throw rateLimited.error;
      }
      return result;
    });

    for (const entry of batchResults) {
      if (entry.status === "success") {
        results.push([true, entry.result]);
      } else if (allowFailure) {
        results.push([false, null]);
      } else {
        throw new Error(
          `multicallRead: call failed (allowFailure=false): ${describeError(entry.error)}`,
        );
      }
    }
  }

  return results;
}

/**
 * Run `fn` with retry on rate limit (`429`/"too many requests"); any other
 * error propagates immediately. Mirrors `ContractBase.callWithRetry` — kept
 * as a standalone copy here since multicall reads aren't tied to a single
 * contract/`ContractBase` instance.
 */
async function callWithRetry<T>(fn: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (isRateLimitError(error) && attempt < MAX_RETRIES - 1) {
        await sleep(RETRY_BASE_DELAY * 2 ** attempt * 1000);
        continue;
      }
      throw error;
    }
  }
  throw lastError;
}
