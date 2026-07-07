/**
 * Shared transaction sending and retry logic for viem-backed contract
 * clients.
 *
 * Port of `python/bnbagent/core/contract_mixin.py::ContractClientMixin`; see
 * that module and its tests (`python/tests/test_contract_mixin.py`) for the
 * authoritative semantics this file mirrors. Notable JS-side adaptations
 * (there is no 1:1 web3.py/viem equivalent) are called out inline.
 */

import {
  type Abi,
  type PublicClient,
  type TransactionReceipt,
  type TransactionRequestLegacy,
  encodeFunctionData,
  getAbiItem,
} from "viem";
import { TransactionPendingError } from "../errors.js";
import type { Intent, IntentExecutor, TxResult } from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { NonceManager, type NonceManagerClient } from "./nonceManager.js";
import type { Paymaster } from "./paymaster.js";
import {
  DEFAULT_GAS_FALLBACK,
  MAX_RETRIES,
  MIN_GAS_PRICE_WEI,
  RETRY_BASE_DELAY,
  getDefaultReceiptTimeout,
  minGasPriceWei,
} from "./txConfig.js";

/** Options accepted by the {@link ContractBase} constructor. */
export interface ContractBaseOpts {
  client: PublicClient;
  address: `0x${string}`;
  abi: Abi;
  walletProvider?: WalletProvider | null;
  paymaster?: Paymaster | null;
}

/** A decoded event log returned by {@link ContractBase.readEvents}. */
export interface DecodedEventLog {
  eventName: string;
  args: Record<string, unknown>;
  address: `0x${string}`;
  blockNumber: bigint | null;
  blockHash: `0x${string}` | null;
  transactionHash: `0x${string}` | null;
  logIndex: number | null;
  removed: boolean;
  [key: string]: unknown;
}

const READ_ONLY_MESSAGE =
  "wallet_provider is required for write operations (client is read-only)";

/**
 * Pre-flight `eth_call` timeout (ms). Matches the Python mixin's 10s
 * `ThreadPoolExecutor` guard: a hung preflight must not block the write path
 * forever, but genuinely revert-shaped results must still surface before
 * broadcast.
 */
const PREFLIGHT_TIMEOUT_MS = 10_000;

/** Interval (ms) between `eth_getTransactionReceipt` polls while waiting. */
const RECEIPT_POLL_INTERVAL_MS = 250;

/** Internal sentinel distinguishing "receipt wait timed out" from any other rejection. */
class ReceiptWaitTimeout extends Error {}

/**
 * Shared base for contract clients: build/sign/broadcast with nonce
 * management and retry (`sendTx`), the intent execution seam
 * (`executeIntent`), read-with-retry (`callWithRetry`), and a thin
 * `getLogs` wrapper (`readEvents`).
 *
 * Subclasses provide the ABI-specific public methods; this class owns
 * nothing protocol-specific.
 */
export class ContractBase {
  readonly address: `0x${string}`;
  protected readonly client: PublicClient;
  protected readonly abi: Abi;
  protected readonly walletProvider: WalletProvider | null;
  protected readonly paymaster: Paymaster | null;
  private intentExecutor: IntentExecutor | null = null;

  constructor(opts: ContractBaseOpts) {
    this.address = opts.address;
    this.client = opts.client;
    this.abi = opts.abi;
    this.walletProvider = opts.walletProvider ?? null;
    this.paymaster = opts.paymaster ?? null;
  }

  /**
   * Call a read function with retry on rate limit (`429`/"too many
   * requests"); any other error propagates immediately.
   */
  protected async callWithRetry<T>(fn: () => Promise<T>): Promise<T> {
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

  /**
   * Run an {@link Intent} through the wallet's executor.
   *
   * This is the write path for clients migrated to the intent seam: the
   * wallet decides how the intent executes (a pure signer wraps itself in a
   * local build/sign/broadcast executor; a self-broadcasting wallet runs the
   * semantic operation itself). The executor is built lazily and cached for
   * the lifetime of this instance.
   */
  protected async executeIntent(intent: Intent): Promise<TxResult> {
    if (!this.walletProvider) {
      throw new Error(READ_ONLY_MESSAGE);
    }
    if (!this.intentExecutor) {
      this.intentExecutor = this.walletProvider.makeExecutor({
        client: this.client,
        paymaster: this.paymaster,
      });
    }
    return this.intentExecutor.execute(intent);
  }

  /**
   * Build, sign, and send a transaction with nonce management and retry.
   *
   * `gas` undefined (default) estimates the limit on-chain with a 20%
   * buffer; pass an explicit `gas` to skip estimation entirely.
   */
  protected async sendTx(req: {
    functionName: string;
    args: readonly unknown[];
    value?: bigint;
    gas?: bigint;
    skipPreflight?: boolean;
  }): Promise<TxResult> {
    const walletProvider = this.walletProvider;
    if (!walletProvider) {
      throw new Error(READ_ONLY_MESSAGE);
    }

    const value = req.value ?? 0n;
    const skipPreflight = req.skipPreflight ?? false;
    const account = walletProvider.address;
    const data = encodeFunctionData({
      abi: this.abi,
      functionName: req.functionName,
      args: req.args,
    });

    const gas =
      req.gas ??
      (await this.estimateGasLimit({ account, data, value, skipPreflight }));

    // Resolve the per-chain gas-price floor once (chainId is an RPC call);
    // reused for every retry attempt below.
    let chainId: number | null = null;
    let floorWei: bigint;
    try {
      chainId = await this.client.getChainId();
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
      this.client as unknown as NonceManagerClient,
      account,
    );
    let lastError: unknown;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      const nonce = await nonceMgr.getNonce();
      try {
        let gasPrice: bigint;
        try {
          const networkGasPrice = await this.client.getGasPrice();
          gasPrice = maxBigint((networkGasPrice * 12n) / 10n, floorWei);
        } catch {
          gasPrice = floorWei;
        }

        // Pre-flight: simulate via eth_call to surface a revert reason
        // before spending gas. Skipped when skipPreflight is true (e.g. a
        // node that only returns opaque 0x reverts).
        if (!skipPreflight) {
          const preflight = await this.preflightCall({
            account,
            to: this.address,
            data,
            value,
            gas,
          });
          if (preflight.kind === "timeout") {
            console.warn(
              "[ContractBase] Pre-flight eth_call timed out, proceeding anyway",
            );
          } else if (preflight.kind === "error") {
            if (isOpaqueRevert(preflight.error)) {
              console.warn(
                "[ContractBase] Pre-flight returned opaque 0x revert, proceeding to on-chain tx",
              );
            } else {
              throw new Error(
                `Transaction would revert: ${describeError(preflight.error)}`,
              );
            }
          }
        }

        const txChainId = chainId ?? (await this.client.getChainId());
        const tx: TransactionRequestLegacy & { chainId: number } = {
          from: account,
          to: this.address,
          data,
          value,
          gas,
          gasPrice,
          nonce,
          chainId: txChainId,
        };

        const signed = await walletProvider.signTransaction(tx);
        const txHash = await this.client.sendRawTransaction({
          serializedTransaction: signed.rawTransaction,
        });

        const timeoutSeconds = getDefaultReceiptTimeout();
        let receipt: TransactionReceipt;
        try {
          receipt = await this.waitForReceipt(txHash, timeoutSeconds);
        } catch (error) {
          if (error instanceof ReceiptWaitTimeout) {
            // Broadcast succeeded (nonce consumed) but unconfirmed in time —
            // surface as pending with the hash, never as a fatal/retry (a
            // blind retry would risk a double-broadcast).
            throw new TransactionPendingError(txHash, timeoutSeconds);
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
            `[ContractBase] Nonce error, retry ${attempt + 1}/${MAX_RETRIES}`,
          );
          continue;
        }

        // Rate limit -> backoff and retry.
        if (isRateLimitError(error) && attempt < MAX_RETRIES - 1) {
          const delaySeconds = RETRY_BASE_DELAY * 2 ** attempt;
          console.warn(
            `[ContractBase] Rate limited, retry ${attempt + 1}/${MAX_RETRIES} in ${delaySeconds.toFixed(1)}s`,
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

  /**
   * Thin wrapper over `publicClient.getLogs` scoped to this contract's
   * address, resolving `eventName` against the ABI and forwarding an
   * optional indexed-argument filter.
   */
  protected async readEvents(opts: {
    eventName: string;
    fromBlock: bigint;
    toBlock?: bigint | "latest";
    args?: Record<string, unknown>;
  }): Promise<DecodedEventLog[]> {
    const eventAbi = getAbiItem({ abi: this.abi, name: opts.eventName });
    if (!eventAbi || eventAbi.type !== "event") {
      throw new Error(
        `readEvents: no event named "${opts.eventName}" on this contract's ABI`,
      );
    }

    // `abi: Abi` is not a `const` literal here, so viem can't narrow
    // `event`'s arg-filter shape from it; readEvents is intentionally
    // minimal (see module docstring) rather than threading a `const abi`
    // generic through ContractBase for this.
    const logs = await this.client.getLogs({
      address: this.address,
      event: eventAbi,
      args: opts.args as never,
      fromBlock: opts.fromBlock,
      toBlock: opts.toBlock ?? "latest",
    });

    return logs.map((log) => {
      const decoded = log as unknown as {
        eventName?: string;
        args?: Record<string, unknown>;
        address: `0x${string}`;
        blockNumber: bigint | null;
        blockHash: `0x${string}` | null;
        transactionHash: `0x${string}` | null;
        logIndex: number | null;
        removed: boolean;
      };
      return {
        eventName: decoded.eventName ?? opts.eventName,
        args: decoded.args ?? {},
        address: decoded.address,
        blockNumber: decoded.blockNumber,
        blockHash: decoded.blockHash,
        transactionHash: decoded.transactionHash,
        logIndex: decoded.logIndex,
        removed: decoded.removed,
      };
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
  private async estimateGasLimit(params: {
    account: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    skipPreflight: boolean;
  }): Promise<bigint> {
    if (params.skipPreflight) {
      return DEFAULT_GAS_FALLBACK;
    }
    try {
      const estimate = await this.client.estimateGas({
        account: params.account,
        to: this.address,
        data: params.data,
        value: params.value,
        prepare: false,
      });
      return (estimate * 12n) / 10n;
    } catch (error) {
      if (isOpaqueRevert(error)) {
        console.warn(
          `[ContractBase] Gas estimation unavailable (opaque revert); falling back to gas=${DEFAULT_GAS_FALLBACK}`,
        );
        return DEFAULT_GAS_FALLBACK;
      }
      if (describeError(error).toLowerCase().includes("revert")) {
        throw new Error(`Transaction would revert: ${describeError(error)}`);
      }
      console.warn(
        `[ContractBase] Gas estimation unavailable (${describeError(error)}); falling back to gas=${DEFAULT_GAS_FALLBACK}`,
      );
      return DEFAULT_GAS_FALLBACK;
    }
  }

  /**
   * Simulate the transaction via `eth_call`, bounded to
   * {@link PREFLIGHT_TIMEOUT_MS}.
   *
   * Never rejects: a timeout and a preflight error are both reported as a
   * result value so the caller can apply the "timeout/opaque -> warn and
   * proceed, real revert -> throw" policy without racing a second promise.
   */
  private async preflightCall(params: {
    account: `0x${string}`;
    to: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    gas: bigint;
  }): Promise<
    { kind: "ok" } | { kind: "timeout" } | { kind: "error"; error: unknown }
  > {
    return new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          resolve({ kind: "timeout" });
        }
      }, PREFLIGHT_TIMEOUT_MS);

      this.client
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
   * Poll `eth_getTransactionReceipt` until it resolves or `timeoutSeconds`
   * elapses.
   *
   * Deliberately not viem's built-in `waitForTransactionReceipt` action: that
   * action drives an internal block-number watcher (its own polling
   * interval, replacement-transaction detection, ...) which is far more
   * machinery than this SDK needs and is awkward to drive deterministically
   * under fake timers. A direct poll loop mirrors the Python mixin's own
   * explicit timeout wrapper around `wait_for_transaction_receipt` and keeps
   * the only two timers involved fully under this method's control.
   */
  private async waitForReceipt(
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
        this.client.getTransactionReceipt({ hash }).then(
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
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function maxBigint(a: bigint, b: bigint): bigint {
  return a > b ? a : b;
}

function isRateLimitError(error: unknown): boolean {
  const text = describeError(error).toLowerCase();
  return text.includes("429") || text.includes("too many requests");
}

/** Render an error (and its `.cause` chain, if any) as a single string. */
function describeError(error: unknown): string {
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
function isOpaqueRevert(error: unknown): boolean {
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
