/**
 * LocalExecutor — build + sign + broadcast {@link Intent}s locally.
 *
 * This is the default {@link IntentExecutor}: it encodes the intent's
 * mechanical `call`, signs it with a local `WalletProvider`, and broadcasts
 * it via a viem `PublicClient` (optionally through a paymaster). It is
 * protocol-agnostic — it never inspects `Intent.name`/`kwargs` and works
 * purely off `Intent.call`.
 *
 * It lives alongside the wallet providers because executing intents is a
 * wallet-domain concern: a self-broadcasting wallet *is* its own executor,
 * while every pure-signing wallet (EVM, hardware, ...) shares this one
 * adapter to bridge local signing onto `./txSender.js`'s shared send
 * infrastructure.
 *
 * Port of `python/bnbagent/wallets/local_executor.py`; see that module and
 * its tests (`python/tests/test_local_executor_paymaster.py`) for the
 * authoritative semantics this file mirrors. The self-pay path is not
 * reimplemented here — it delegates to `../core/txSender.js`'s
 * `sendSelfPayTx`, the same core `ContractBase.sendTx` uses, so the
 * build/sign/broadcast/retry logic exists exactly once.
 */

import type { PublicClient, TransactionRequestLegacy } from "viem";
import { encodeFunctionData, keccak256 } from "viem";
import type { Paymaster } from "../core/paymaster.js";
import { getDefaultReceiptTimeout, minGasPriceWei } from "../core/txConfig.js";
import {
  describeError,
  estimateGasLimit,
  isOpaqueRevert,
  maxBigint,
  preflightCall,
  sendSelfPayTx,
  waitForReceiptAndInterpret,
} from "../core/txSender.js";
import type { Intent, IntentExecutor, TxResult } from "./intents.js";
import type { WalletProvider } from "./walletProvider.js";

/** Options accepted by the {@link LocalExecutor} constructor. */
export interface LocalExecutorOpts {
  client: PublicClient;
  walletProvider: WalletProvider;
  /** Optional paymaster for gas sponsorship (MegaFuel-style). */
  paymaster?: Paymaster | null;
  /**
   * Seconds to wait for a transaction receipt. `null`/absent (default)
   * resolves {@link getDefaultReceiptTimeout} lazily, at `execute()` time —
   * so a runtime `setDefaultReceiptTimeout()` call is honored even by a
   * cached executor.
   */
  receiptTimeout?: number | null;
}

const USER_AGENT_HEADERS = { UserAgent: "bnbagent/v1.0.0" } as const;

/**
 * Default executor: build, sign (via a local wallet) and broadcast.
 *
 * When a paymaster is configured, attempts a sponsored broadcast first; if
 * the transaction is not sponsorable (or the paymaster is unreachable),
 * falls back to self-pay rather than failing. This lets the per-(protocol,
 * network) sponsorship matrix be resolved at runtime by MegaFuel's
 * `pm_isSponsorable` — e.g. ERC-8183 mainnet writes (never sponsored)
 * self-pay automatically while testnet writes are sponsored — with no
 * sponsorship policy hard-coded into the executor.
 */
export class LocalExecutor implements IntentExecutor {
  private readonly client: PublicClient;
  private readonly walletProvider: WalletProvider;
  private readonly paymaster: Paymaster | null;
  private readonly receiptTimeout: number | null;

  constructor(opts: LocalExecutorOpts) {
    this.client = opts.client;
    this.walletProvider = opts.walletProvider;
    this.paymaster = opts.paymaster ?? null;
    this.receiptTimeout = opts.receiptTimeout ?? null;
  }

  /** Execute an intent's mechanical `call`. */
  async execute(intent: Intent): Promise<TxResult> {
    const call = intent.call;
    if (!call) {
      const label = intent.name ?? intent.description ?? "None";
      throw new Error(
        `LocalExecutor requires Intent.call (a web3 ContractFunction); got None for intent '${label}'`,
      );
    }
    const description = intent.description ?? intent.name ?? "transaction";
    const account = this.walletProvider.address;
    const to = call.address;
    const value = intent.value ?? 0n;
    const data = encodeFunctionData({
      abi: call.abi,
      functionName: call.functionName,
      args: call.args,
    });

    const gas =
      intent.gas ??
      (await estimateGasLimit(
        this.client,
        { account, to, data, value, skipPreflight: false },
        { logPrefix: "[LocalExecutor]" },
      ));

    if (this.paymaster) {
      const sponsoredHash = await this.trySponsored({
        paymaster: this.paymaster,
        account,
        to,
        data,
        value,
        gas,
        description,
      });
      if (sponsoredHash) {
        const timeoutSeconds =
          this.receiptTimeout ?? getDefaultReceiptTimeout();
        return waitForReceiptAndInterpret(
          this.client,
          sponsoredHash,
          timeoutSeconds,
          { requireTransactionSeen: true },
        );
      }
    }

    return sendSelfPayTx({
      client: this.client,
      walletProvider: this.walletProvider,
      to,
      data,
      value,
      gas,
      receiptTimeoutSeconds: this.receiptTimeout ?? undefined,
      logPrefix: "[LocalExecutor]",
    });
  }

  /**
   * Attempt a paymaster-sponsored broadcast.
   *
   * Returns the broadcast tx hash on success, or `null` when the tx is not
   * sponsorable or the paymaster is unreachable — the caller then falls back
   * to self-pay. A genuine pre-flight revert propagates (self-pay could not
   * fix it either). The sponsored *send* itself is never retried into a
   * self-pay fallback, to avoid any double-broadcast risk once submitted.
   */
  private async trySponsored(params: {
    paymaster: Paymaster;
    account: `0x${string}`;
    to: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    gas: bigint;
    description: string;
  }): Promise<`0x${string}` | null> {
    const { paymaster, account, to, data, value, gas, description } = params;

    let nonce: number;
    try {
      nonce = await paymaster.ethGetTransactionCount(account, "pending");
    } catch (error) {
      console.warn(
        `[LocalExecutor] paymaster nonce fetch failed for ${description} (${describeError(error)}); self-paying`,
      );
      return null;
    }

    const chainId = await this.client.getChainId();

    let gasPrice: bigint;
    try {
      const networkGasPrice = await this.client.getGasPrice();
      gasPrice = maxBigint(networkGasPrice, minGasPriceWei(chainId));
    } catch {
      gasPrice = minGasPriceWei(chainId);
    }

    const tx: TransactionRequestLegacy & { chainId: number } = {
      from: account,
      to,
      data,
      value,
      gas,
      gasPrice,
      nonce,
      chainId,
    };

    const preflight = await preflightCall(this.client, {
      account,
      to,
      data,
      value,
      gas,
    });
    if (preflight.kind === "timeout") {
      console.warn(
        `[LocalExecutor] Pre-flight eth_call timed out for ${description}, proceeding`,
      );
    } else if (preflight.kind === "error") {
      if (isOpaqueRevert(preflight.error)) {
        console.warn(
          `[LocalExecutor] Pre-flight returned opaque 0x revert for ${description}, proceeding`,
        );
      } else {
        throw new Error(
          `Transaction would revert: ${describeError(preflight.error)}`,
        );
      }
    }

    let sponsorable: boolean;
    try {
      sponsorable = await paymaster.isSponsorable({
        to,
        from: account,
        value,
        data,
        gas,
      });
    } catch (error) {
      console.warn(
        `[LocalExecutor] isSponsorable check failed for ${description} (${describeError(error)}); self-paying`,
      );
      return null;
    }
    if (!sponsorable) {
      console.info(
        `[LocalExecutor] ${description} is not sponsorable on this network; self-paying gas`,
      );
      return null;
    }

    const sponsoredTx: TransactionRequestLegacy & { chainId: number } = {
      ...tx,
      gasPrice: 0n,
    };
    const signed = await this.walletProvider.signTransaction(sponsoredTx);
    // The on-chain hash of a signed tx is keccak256 of its raw bytes — a
    // mathematical fact independent of anything the relay replies. If the
    // relay's response differs, its hash is untrustworthy: track the signed
    // hash instead, so receipt/presence polling watches the transaction that
    // was actually handed over (a lying relay then surfaces as
    // RelaySubmissionUnverifiedError rather than an eternal wait on a
    // hash that cannot exist).
    const signedTxHash = keccak256(normalizeHash(signed.rawTransaction));
    const rawHash = await paymaster.ethSendRawTransaction(
      signed.rawTransaction,
      { ...USER_AGENT_HEADERS },
    );
    const relayHash = normalizeHash(rawHash);
    if (relayHash.toLowerCase() !== signedTxHash.toLowerCase()) {
      console.warn(
        `[LocalExecutor] paymaster returned tx hash ${relayHash}, which does not match the signed transaction hash ${signedTxHash}; tracking the signed hash`,
      );
      return signedTxHash;
    }
    return relayHash;
  }
}

/** Ensure a paymaster-returned tx hash is `0x`-prefixed. */
function normalizeHash(hash: string): `0x${string}` {
  return (hash.startsWith("0x") ? hash : `0x${hash}`) as `0x${string}`;
}
