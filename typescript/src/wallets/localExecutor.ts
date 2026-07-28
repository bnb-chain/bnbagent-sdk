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
import {
  NONCE_ERROR_PATTERNS,
  NonceManager,
  type NonceManagerClient,
} from "../core/nonceManager.js";
import type { Paymaster } from "../core/paymaster.js";
import { getDefaultReceiptTimeout, minGasPriceWei } from "../core/txConfig.js";
import {
  describeError,
  estimateGasLimit,
  isInsufficientFundsError,
  isOpaqueRevert,
  maxBigint,
  preflightCall,
  sendSelfPayTx,
  waitForReceiptAndInterpret,
} from "../core/txSender.js";
import {
  RelayFallbackFailedError,
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../errors.js";
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
  /**
   * Seconds a relay-returned hash may stay invisible to the chain RPC before
   * the sponsored wait aborts as unverified. `null`/absent uses
   * {@link RELAY_UNSEEN_ABORT_SECONDS}.
   */
  relayUnseenTimeout?: number | null;
  /**
   * When a sponsored broadcast is accepted by the relay but never observed
   * on-chain, re-sign the same nonce and self-pay it directly. Defaults to
   * `true`; set `false` to keep the old "fail loudly, spend nothing" behavior
   * (also the seam tests use to pin the raw unverified surface).
   */
  selfPayFallback?: boolean;
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
  private readonly relayUnseenTimeout: number | null;
  private readonly selfPayFallback: boolean;

  constructor(opts: LocalExecutorOpts) {
    this.client = opts.client;
    this.walletProvider = opts.walletProvider;
    this.paymaster = opts.paymaster ?? null;
    this.receiptTimeout = opts.receiptTimeout ?? null;
    this.relayUnseenTimeout = opts.relayUnseenTimeout ?? null;
    this.selfPayFallback = opts.selfPayFallback ?? true;
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
      const sponsored = await this.trySponsored({
        paymaster: this.paymaster,
        account,
        to,
        data,
        value,
        gas,
        description,
      });
      if (sponsored) {
        const timeoutSeconds =
          this.receiptTimeout ?? getDefaultReceiptTimeout();
        try {
          return await waitForReceiptAndInterpret(
            this.client,
            sponsored.hash,
            timeoutSeconds,
            {
              requireTransactionSeen: true,
              unseenAbortSeconds: this.relayUnseenTimeout ?? undefined,
            },
          );
        } catch (error) {
          if (
            error instanceof RelaySubmissionUnverifiedError &&
            this.selfPayFallback
          ) {
            return await this.selfPayAfterUnverifiedRelay(
              sponsored,
              error,
              timeoutSeconds,
              description,
            );
          }
          throw error;
        }
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
   * Returns the tracked tx hash plus the (pre-zero-gasPrice) signed request on
   * success, or `null` when the tx is not sponsorable or the paymaster is
   * unreachable — the caller then falls back to self-pay. The request is
   * returned so that, if the relay accepts the hash but never broadcasts it,
   * the caller can re-sign the SAME nonce/payload as a self-paid transaction.
   * A genuine pre-flight revert propagates (self-pay could not fix it either).
   * The sponsored *send* itself is never retried into a self-pay fallback, to
   * avoid any double-broadcast risk once submitted.
   */
  private async trySponsored(params: {
    paymaster: Paymaster;
    account: `0x${string}`;
    to: `0x${string}`;
    data: `0x${string}`;
    value: bigint;
    gas: bigint;
    description: string;
  }): Promise<{
    hash: `0x${string}`;
    tx: TransactionRequestLegacy & { chainId: number };
  } | null> {
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
      return { hash: signedTxHash, tx };
    }
    return { hash: relayHash, tx };
  }

  /**
   * Recover from a sponsored broadcast the relay accepted but never put
   * on-chain: re-sign the SAME nonce as a self-paid transaction and broadcast
   * it directly. Same nonce is the safety property — at most one of the relay
   * tx and the self-pay tx can ever land, so this can never double-execute.
   *
   * Races are handled at three points: a final re-check before re-signing, a
   * re-check if the direct broadcast hits a nonce conflict (the relay tx
   * surfaced late), and a re-check if the self-pay tx stays pending (the relay
   * tx won). A wallet with no gas surfaces as {@link RelayFallbackFailedError}.
   */
  private async selfPayAfterUnverifiedRelay(
    sponsored: {
      hash: `0x${string}`;
      tx: TransactionRequestLegacy & { chainId: number };
    },
    relayError: RelaySubmissionUnverifiedError,
    timeoutSeconds: number,
    description: string,
  ): Promise<TxResult> {
    const { hash: relayHash, tx } = sponsored;
    const account = this.walletProvider.address;

    // (1) Final re-check: the relay tx may have landed exactly as the wait
    // aborted. Skip the redundant second broadcast if so.
    const alreadyLanded = await this.relayTxOutcome(relayHash, timeoutSeconds);
    if (alreadyLanded) {
      return alreadyLanded;
    }

    // (2) About to charge the wallet — say so loudly.
    console.warn(
      `[LocalExecutor] MegaFuel relay accepted ${description} (tx ${relayHash}) ` +
        `but the chain RPC never saw it within ${relayError.timeoutSeconds}s; ` +
        `re-signing nonce ${tx.nonce} as a SELF-PAID transaction — gas will be ` +
        `charged to ${account}.`,
    );

    // (3) Refresh the gas price (the sponsored quote is at least the unseen
    // window stale) and re-sign the SAME nonce/payload. The 1.2x bump also
    // outbids the gasPrice-0 relay tx under replacement semantics if both
    // happen to sit in one mempool.
    let gasPrice: bigint;
    try {
      const networkGasPrice = await this.client.getGasPrice();
      gasPrice = maxBigint(
        (networkGasPrice * 12n) / 10n,
        minGasPriceWei(tx.chainId),
      );
    } catch {
      gasPrice = minGasPriceWei(tx.chainId);
    }
    const signed = await this.walletProvider.signTransaction({
      ...tx,
      gasPrice,
    });

    let selfPayHash: `0x${string}`;
    try {
      selfPayHash = await this.client.sendRawTransaction({
        serializedTransaction: signed.rawTransaction,
      });
    } catch (error) {
      if (isInsufficientFundsError(error)) {
        throw new RelayFallbackFailedError(
          relayHash,
          relayError.timeoutSeconds,
          `wallet ${account} has insufficient BNB for gas`,
          { cause: error },
        );
      }
      if (isNonceError(error)) {
        // The nonce was consumed out-of-band — most likely the relay tx
        // surfaced after all. If it landed, that is success; otherwise never
        // re-nonce (that is the double-execution risk) — surface the original
        // unverified error.
        const raced = await this.relayTxOutcome(relayHash, timeoutSeconds);
        if (raced) {
          return raced;
        }
        throw relayError;
      }
      throw new RelayFallbackFailedError(
        relayHash,
        relayError.timeoutSeconds,
        describeError(error),
        { cause: error },
      );
    }

    // (4) The self-pay tx consumed a nonce outside NonceManager; force the
    // shared cache to re-seed from chain for the next caller.
    NonceManager.forAccount(
      this.client as unknown as NonceManagerClient,
      account,
    ).reset();

    // (5) Wait for the self-pay receipt; if it stays pending but the relay tx
    // won the race, return the relay outcome instead.
    try {
      return await waitForReceiptAndInterpret(
        this.client,
        selfPayHash,
        timeoutSeconds,
      );
    } catch (error) {
      if (error instanceof TransactionPendingError) {
        const raced = await this.relayTxOutcome(relayHash, timeoutSeconds);
        if (raced) {
          return raced;
        }
      }
      throw error;
    }
  }

  /**
   * Single-shot check of whether `relayHash` actually made it on-chain:
   * returns its {@link TxResult} if a receipt exists, or waits out the receipt
   * if the tx is at least visible in the mempool. Returns `null` when the tx
   * is neither mined nor seen. A reverted receipt throws.
   */
  private async relayTxOutcome(
    relayHash: `0x${string}`,
    timeoutSeconds: number,
  ): Promise<TxResult | null> {
    let receipt: Awaited<
      ReturnType<PublicClient["getTransactionReceipt"]>
    > | null;
    try {
      receipt = await this.client.getTransactionReceipt({ hash: relayHash });
    } catch {
      receipt = null;
    }
    if (receipt) {
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
    let seen = false;
    try {
      await this.client.getTransaction({ hash: relayHash });
      seen = true;
    } catch {
      seen = false;
    }
    if (seen) {
      return await waitForReceiptAndInterpret(
        this.client,
        relayHash,
        timeoutSeconds,
      );
    }
    return null;
  }
}

/** Whether `error` is a nonce-conflict RPC error (nonce too low / already known / underpriced). */
function isNonceError(error: unknown): boolean {
  const text = describeError(error).toLowerCase();
  return NONCE_ERROR_PATTERNS.some((pattern) => text.includes(pattern));
}

/** Ensure a paymaster-returned tx hash is `0x`-prefixed. */
function normalizeHash(hash: string): `0x${string}` {
  return (hash.startsWith("0x") ? hash : `0x${hash}`) as `0x${string}`;
}
