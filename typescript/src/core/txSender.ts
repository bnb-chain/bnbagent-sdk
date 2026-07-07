/**
 * Shared build/sign/broadcast primitives for viem-backed write paths.
 *
 * Extracted from `ContractBase`'s original `sendTx` (see git history) so the
 * self-pay build/sign/broadcast/retry core is implemented exactly once and
 * shared by both `ContractBase.sendTx` (self-pay only) and `LocalExecutor`
 * (self-pay fallback + the pieces its paymaster-sponsored path reuses:
 * `estimateGasLimit`, `preflightCall`, `waitForReceiptAndInterpret`). Port of
 * `python/bnbagent/core/contract_mixin.py` / `python/bnbagent/wallets/local_executor.py`;
 * see those modules and their tests for the authoritative semantics this
 * file mirrors.
 */

import type {
  PublicClient,
  TransactionReceipt,
  TransactionRequestLegacy,
} from "viem";
import { TransactionPendingError } from "../errors.js";
import type { TxResult } from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { NonceManager, type NonceManagerClient } from "./nonceManager.js";
import {
  DEFAULT_GAS_FALLBACK,
  MAX_RETRIES,
  MIN_GAS_PRICE_WEI,
  RETRY_BASE_DELAY,
  getDefaultReceiptTimeout,
  minGasPriceWei,
} from "./txConfig.js";

/**
 * Pre-flight `eth_call` timeout (ms). Matches the Python mixin's 10s
 * `ThreadPoolExecutor` guard: a hung preflight must not block the write path
 * forever, but genuinely revert-shaped results must still surface before
 * broadcast.
 */
export const PREFLIGHT_TIMEOUT_MS = 10_000;

/** Interval (ms) between `eth_getTransactionReceipt` polls while waiting. */
export const RECEIPT_POLL_INTERVAL_MS = 250;

/** Internal sentinel distinguishing "receipt wait timed out" from any other rejection. */
export class ReceiptWaitTimeout extends Error {}

export type PreflightResult =
  | { kind: "ok" }
  | { kind: "timeout" }
  | { kind: "error"; error: unknown };

/**
 * Simulate a transaction via `eth_call`, bounded to
 * {@link PREFLIGHT_TIMEOUT_MS}.
 *
 * Never rejects: a timeout and a preflight error are both reported as a
 * result value so the caller can apply the "timeout/opaque -> warn and
 * proceed, real revert -> throw" policy without racing a second promise.
 */
export async function preflightCall(
  client: PublicClient,
  params: {
    account: `0x${string}`;
    to: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    gas: bigint;
  },
): Promise<PreflightResult> {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        resolve({ kind: "timeout" });
      }
    }, PREFLIGHT_TIMEOUT_MS);

    client
      .call({
        account: params.account,
        to: params.to,
        data: params.data,
        value: params.value,
        gas: params.gas,
      })
      .then(
        () => {
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve({ kind: "ok" });
          }
        },
        (error) => {
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve({ kind: "error", error });
          }
        },
      );
  });
}

/**
 * Estimate gas for a call with a 20% buffer.
 *
 * Falls back to {@link DEFAULT_GAS_FALLBACK} when estimation is
 * unavailable — transport/RPC errors, or a node returning opaque `0x`
 * revert data (the same escape hatch the pre-flight uses). A genuine
 * revert is raised as `Transaction would revert` so the caller sees the
 * reason instead of a masked fallback broadcast.
 */
export async function estimateGasLimit(
  client: PublicClient,
  params: {
    account: `0x${string}`;
    to: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    skipPreflight: boolean;
  },
  opts?: { logPrefix?: string },
): Promise<bigint> {
  const logPrefix = opts?.logPrefix ?? "[txSender]";
  if (params.skipPreflight) {
    return DEFAULT_GAS_FALLBACK;
  }
  try {
    const estimate = await client.estimateGas({
      account: params.account,
      to: params.to,
      data: params.data,
      value: params.value,
      prepare: false,
    });
    return (estimate * 12n) / 10n;
  } catch (error) {
    if (isOpaqueRevert(error)) {
      console.warn(
        `${logPrefix} Gas estimation unavailable (opaque revert); falling back to gas=${DEFAULT_GAS_FALLBACK}`,
      );
      return DEFAULT_GAS_FALLBACK;
    }
    if (describeError(error).toLowerCase().includes("revert")) {
      throw new Error(`Transaction would revert: ${describeError(error)}`);
    }
    console.warn(
      `${logPrefix} Gas estimation unavailable (${describeError(error)}); falling back to gas=${DEFAULT_GAS_FALLBACK}`,
    );
    return DEFAULT_GAS_FALLBACK;
  }
}

/**
 * Poll `eth_getTransactionReceipt` until it resolves or `timeoutSeconds`
 * elapses.
 *
 * Deliberately not viem's built-in `waitForTransactionReceipt` action: that
 * action drives an internal block-number watcher (its own polling
 * interval, replacement-transaction detection, ...) which is far more
 * machinery than this SDK needs and is awkward to drive deterministically
 * under fake timers. A direct poll loop mirrors the Python mixin's own
 * explicit timeout wrapper around `wait_for_transaction_receipt` and keeps
 * the only two timers involved fully under this function's control.
 */
export async function waitForReceipt(
  client: PublicClient,
  hash: `0x${string}`,
  timeoutSeconds: number,
): Promise<TransactionReceipt> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new ReceiptWaitTimeout());
      }
    }, timeoutSeconds * 1000);

    const poll = () => {
      if (settled) {
        return;
      }
      client.getTransactionReceipt({ hash }).then(
        (receipt) => {
          if (settled) {
            return;
          }
          settled = true;
          clearTimeout(timer);
          resolve(receipt);
        },
        () => {
          // Not mined yet (or transiently unavailable) — keep polling
          // until the timeout above fires.
          if (!settled) {
            setTimeout(poll, RECEIPT_POLL_INTERVAL_MS);
          }
        },
      );
    };
    poll();
  });
}

/**
 * Wait for `hash`'s receipt and translate the outcome into the shared
 * write-path result contract: a timeout becomes {@link TransactionPendingError}
 * (broadcast succeeded, nonce consumed — never treated as a fatal/retryable
 * failure), a reverted receipt becomes a plain `Error`, and a successful
 * receipt becomes a {@link TxResult}.
 */
export async function waitForReceiptAndInterpret(
  client: PublicClient,
  hash: `0x${string}`,
  timeoutSeconds: number,
): Promise<TxResult> {
  let receipt: TransactionReceipt;
  try {
    receipt = await waitForReceipt(client, hash, timeoutSeconds);
  } catch (error) {
    if (error instanceof ReceiptWaitTimeout) {
      throw new TransactionPendingError(hash, timeoutSeconds);
    }
    throw error;
  }

  if (receipt.status === "reverted") {
    throw new Error(
      `Transaction reverted on-chain: ${receipt.transactionHash}`,
    );
  }

  return {
    transactionHash: receipt.transactionHash,
    status: receipt.status === "success" ? 1 : 0,
    receipt,
  };
}

/** Options accepted by {@link sendSelfPayTx}. */
export interface SendSelfPayParams {
  client: PublicClient;
  walletProvider: WalletProvider;
  to: `0x${string}`;
  data: `0x${string}`;
  value: bigint;
  gas: bigint;
  skipPreflight?: boolean;
  /**
   * Receipt-wait timeout (seconds). Omitted (default) resolves
   * {@link getDefaultReceiptTimeout} lazily, at send time — so a runtime
   * `setDefaultReceiptTimeout()` call is honored even by a cached caller.
   */
  receiptTimeoutSeconds?: number;
  /** Log-line prefix identifying the caller (e.g. `"[ContractBase]"`). */
  logPrefix?: string;
}

/**
 * Build, sign, and send a transaction with nonce management and retry — the
 * wallet pays its own gas.
 *
 * This is the shared self-pay core: `ContractBase.sendTx` calls it directly,
 * and `LocalExecutor` calls it as the fallback when no paymaster is
 * configured or the paymaster path declines (unreachable / not sponsorable).
 */
export async function sendSelfPayTx(
  params: SendSelfPayParams,
): Promise<TxResult> {
  const {
    client,
    walletProvider,
    to,
    data,
    value,
    gas,
    skipPreflight = false,
    logPrefix = "[txSender]",
  } = params;
  const account = walletProvider.address;

  // Resolve the per-chain gas-price floor once (chainId is an RPC call);
  // reused for every retry attempt below.
  let chainId: number | null = null;
  let floorWei: bigint;
  try {
    chainId = await client.getChainId();
    floorWei = minGasPriceWei(chainId);
  } catch {
    floorWei = MIN_GAS_PRICE_WEI;
  }

  // `PublicClient["transport"]`'s static type doesn't expose `.url` (it's
  // transport-implementation-specific at the type level, even though it's
  // present at runtime for `http()`), so it doesn't structurally satisfy
  // NonceManagerClient's minimal shape. Cast rather than widen that
  // interface just for this one caller.
  const nonceMgr = NonceManager.forAccount(
    client as unknown as NonceManagerClient,
    account,
  );
  let lastError: unknown;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const nonce = await nonceMgr.getNonce();
    try {
      let gasPrice: bigint;
      try {
        const networkGasPrice = await client.getGasPrice();
        gasPrice = maxBigint((networkGasPrice * 12n) / 10n, floorWei);
      } catch {
        gasPrice = floorWei;
      }

      // Pre-flight: simulate via eth_call to surface a revert reason
      // before spending gas. Skipped when skipPreflight is true (e.g. a
      // node that only returns opaque 0x reverts).
      if (!skipPreflight) {
        const preflight = await preflightCall(client, {
          account,
          to,
          data,
          value,
          gas,
        });
        if (preflight.kind === "timeout") {
          console.warn(
            `${logPrefix} Pre-flight eth_call timed out, proceeding anyway`,
          );
        } else if (preflight.kind === "error") {
          if (isOpaqueRevert(preflight.error)) {
            console.warn(
              `${logPrefix} Pre-flight returned opaque 0x revert, proceeding to on-chain tx`,
            );
          } else {
            throw new Error(
              `Transaction would revert: ${describeError(preflight.error)}`,
            );
          }
        }
      }

      const txChainId = chainId ?? (await client.getChainId());
      const tx: TransactionRequestLegacy & { chainId: number } = {
        from: account,
        to,
        data,
        value,
        gas,
        gasPrice,
        nonce,
        chainId: txChainId,
      };

      const signed = await walletProvider.signTransaction(tx);
      const txHash = await client.sendRawTransaction({
        serializedTransaction: signed.rawTransaction,
      });

      const timeoutSeconds =
        params.receiptTimeoutSeconds ?? getDefaultReceiptTimeout();
      return await waitForReceiptAndInterpret(client, txHash, timeoutSeconds);
    } catch (error) {
      if (error instanceof TransactionPendingError) {
        // Not a failure and not retryable here.
        throw error;
      }
      lastError = error;

      // Nonce error -> re-sync and retry.
      if (
        (await nonceMgr.handleError(error, nonce)) &&
        attempt < MAX_RETRIES - 1
      ) {
        console.warn(
          `${logPrefix} Nonce error, retry ${attempt + 1}/${MAX_RETRIES}`,
        );
        continue;
      }

      // Rate limit -> backoff and retry.
      if (isRateLimitError(error) && attempt < MAX_RETRIES - 1) {
        const delaySeconds = RETRY_BASE_DELAY * 2 ** attempt;
        console.warn(
          `${logPrefix} Rate limited, retry ${attempt + 1}/${MAX_RETRIES} in ${delaySeconds.toFixed(1)}s`,
        );
        await sleep(delaySeconds * 1000);
        continue;
      }

      // Any other error path (preflight revert, receipt timeout handled
      // above, transient RPC failure): the cached nonce was already
      // incremented in getNonce() but the tx may not have been mined or
      // even broadcast. Invalidate the cache so the next caller re-seeds
      // from chain instead of leaving a permanent nonce gap that strands
      // every subsequent tx in mempool.
      nonceMgr.reset();
      throw error;
    }
  }

  throw lastError;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function maxBigint(a: bigint, b: bigint): bigint {
  return a > b ? a : b;
}

export function isRateLimitError(error: unknown): boolean {
  const text = describeError(error).toLowerCase();
  return text.includes("429") || text.includes("too many requests");
}

/** Render an error (and its `.cause` chain, if any) as a single string. */
export function describeError(error: unknown): string {
  if (error instanceof Error) {
    const own = error.message;
    const causeText =
      error.cause !== undefined ? describeError(error.cause) : "";
    return causeText && causeText !== own ? `${own}: ${causeText}` : own;
  }
  return String(error);
}

/**
 * Whether `error` represents a revert with no decodable reason data — the
 * `'0x'` escape hatch some nodes return instead of an ABI-encoded revert
 * reason. Ported from the Python mixin's string-matching check
 * (`"'0x'" in err_str or err_str.strip().endswith(", '0x')")`), extended to
 * also walk a JS error's `.cause` chain and any `.data` field directly
 * (viem's contract-error classes carry the raw revert data there).
 */
export function isOpaqueRevert(error: unknown): boolean {
  let current: unknown = error;
  const seen = new Set<unknown>();
  while (current && typeof current === "object" && !seen.has(current)) {
    seen.add(current);
    const data = (current as { data?: unknown }).data;
    if (data === "0x") {
      return true;
    }
    current = (current as { cause?: unknown }).cause;
  }
  const text = describeError(error).trim();
  return text.includes("'0x'") || /,\s*'0x'\)$/.test(text) || text === "0x";
}
