/**
 * Shared transaction sending and retry logic for viem-backed contract
 * clients.
 *
 * Port of `python/bnbagent/core/contract_mixin.py::ContractClientMixin`; see
 * that module and its tests (`python/tests/test_contract_mixin.py`) for the
 * authoritative semantics this file mirrors. Notable JS-side adaptations
 * (there is no 1:1 web3.py/viem equivalent) are called out inline.
 *
 * The build/sign/broadcast/retry core of `sendTx` lives in `./txSender.js`,
 * shared with `LocalExecutor`'s self-pay fallback (see that module's
 * docstring) — this class stays a thin ABI-aware wrapper around it.
 */

import {
  type Abi,
  type PublicClient,
  encodeFunctionData,
  getAbiItem,
} from "viem";
import type { Intent, IntentExecutor, TxResult } from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import type { Paymaster } from "./paymaster.js";
import { MAX_RETRIES, RETRY_BASE_DELAY } from "./txConfig.js";
import {
  estimateGasLimit,
  isRateLimitError,
  sendSelfPayTx,
  sleep,
} from "./txSender.js";

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
   * buffer; pass an explicit `gas` to skip estimation entirely. Delegates to
   * `./txSender.js`'s shared self-pay core once the ABI-specific calldata
   * and gas limit are resolved.
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
      (await estimateGasLimit(
        this.client,
        { account, to: this.address, data, value, skipPreflight },
        { logPrefix: "[ContractBase]" },
      ));

    return sendSelfPayTx({
      client: this.client,
      walletProvider,
      to: this.address,
      data,
      value,
      gas,
      skipPreflight,
      logPrefix: "[ContractBase]",
    });
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
}
