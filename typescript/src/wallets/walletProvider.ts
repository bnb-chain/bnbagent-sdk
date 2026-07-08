/**
 * Wallet Provider abstract base class.
 *
 * Defines the interface that all wallet providers must implement, allowing
 * for easy swapping between different wallet implementations (EVM, MPC,
 * TWAK, ...).
 *
 * Port of `python/bnbagent/wallets/wallet_provider.py`.
 */

import type { TransactionRequestLegacy, TypedDataDomain } from "viem";
import {
  SIGN_MESSAGE,
  SIGN_TRANSACTION,
  SIGN_TYPED_DATA,
  X402_PAY,
} from "./capabilities.js";
import { UnsupportedWalletOperation } from "./errors.js";
import type { ExecutionContext, IntentExecutor } from "./intents.js";
import { LocalExecutor } from "./localExecutor.js";

/**
 * Input to {@link WalletProvider.signTransaction}.
 *
 * `chainId` is REQUIRED and load-bearing: EIP-155 replay protection depends
 * on it being folded into the signature. viem's `TransactionRequestLegacy`
 * types `chainId` as optional, so this intersection makes the requirement
 * explicit at the interface — an implementer cannot silently omit it and
 * produce a pre-EIP-155 replayable signature.
 */
export type SignableTransaction = TransactionRequestLegacy & {
  chainId: number;
};

/** Result of {@link WalletProvider.signTransaction}. */
export interface SignedTx {
  rawTransaction: `0x${string}`;
  hash: `0x${string}`;
  r: `0x${string}`;
  s: `0x${string}`;
  v: bigint;
}

/**
 * Result of {@link WalletProvider.signMessage} / {@link WalletProvider.signTypedData}.
 *
 * `messageHash` is the digest that was actually signed — the EIP-191
 * personal-sign digest for `signMessage`, the EIP-712 typed-data digest for
 * `signTypedData`. The two are not interchangeable.
 */
export interface SignatureResult {
  messageHash: `0x${string}`;
  r: `0x${string}`;
  s: `0x${string}`;
  v: bigint;
  signature: `0x${string}`;
}

/** Uniform, non-sensitive summary returned by {@link WalletProvider.describe}. */
export interface WalletDescription {
  kind: string;
  address: string | null;
  keyLocation: string | null;
  exists: boolean;
  capabilities: string[];
}

const SIGN_METHOD_CAPABILITIES = [
  [SIGN_MESSAGE, "signMessage"],
  [SIGN_TRANSACTION, "signTransaction"],
  [SIGN_TYPED_DATA, "signTypedData"],
] as const;

/**
 * Abstract base class for wallet providers.
 *
 * This interface defines the contract that all wallet providers must
 * implement, allowing for easy swapping between different wallet
 * implementations (EVM, MPC, TWAK, ...).
 */
export abstract class WalletProvider {
  /**
   * Stable, lowercase identifier for this provider kind (`"evm"`, `"twak"`,
   * `"mpc"`, ...). Used by the wallet factory to select an implementation
   * and by {@link describe} for uniform introspection. Concrete providers
   * override it; third-party subclasses keep the default.
   */
  static readonly kind: string = "custom";

  /**
   * Whether this wallet's ERC-8183 `fund` execution bundles the
   * payment-token approval itself (fund bundles approval: approve + deposit
   * in one operation). `false` for pure signers — the SDK manages the
   * allowance and sends a separate `approve` before `fund`. A
   * self-broadcasting backend that owns the funding flow end-to-end (e.g.
   * the twak CLI, whose `erc8183 fund` approves then deposits) sets this to
   * `true` so the SDK skips its own allowance top-up.
   */
  readonly fundBundlesApproval: boolean = false;

  /**
   * Non-`sign.*` capabilities this provider declares (execution- and
   * service-side bits like `calls.arbitrary` or `broadcast.self`). Concrete
   * providers set this; `sign.*` values are never listed here — they are
   * auto-derived by {@link capabilities}.
   */
  protected readonly extraCapabilities: ReadonlySet<string> = new Set();

  /** Instance accessor mirroring the constructor's static `kind`. */
  get kind(): string {
    return (this.constructor as typeof WalletProvider).kind;
  }

  /** The wallet's on-chain address. */
  abstract get address(): `0x${string}`;

  /**
   * Human-readable description of *where this wallet's key lives*.
   *
   * There is no single shared key store across providers — each owns its
   * own custody (the SDK keystore directory, an external CLI's keychain, a
   * remote MPC enclave, ...). This property gives a uniform way to answer
   * "where is my key?" without unifying the underlying storage. Returns
   * `null` when the location is unknown or not applicable.
   */
  get keyLocation(): string | null {
    return null;
  }

  /**
   * Whether durable key material already backs this provider.
   *
   * Defaults to `true` (a constructed provider is assumed usable).
   * Providers with an on-disk or external store override this to report
   * whether the wallet has actually been created and persisted, so callers
   * can implement a uniform "get-or-create" flow. Implementations MUST NOT
   * throw — they return `false` when existence cannot be confirmed.
   */
  exists(): boolean {
    return true;
  }

  /**
   * The set of capability strings this wallet supports.
   *
   * Values come from `./capabilities` (an open set — third parties may add
   * vendor-namespaced strings; consumers ignore unknown values and treat
   * absence as unsupported).
   *
   * `sign.*` values are **auto-derived from method overrides**: a provider
   * that implements `signMessage` / `signTransaction` / `signTypedData`
   * declares the matching capability by that override alone, so declaration
   * cannot drift from behavior. Corollary (the don't-override-to-raise
   * discipline): never override a `sign*` method just to raise — the base
   * default already raises a descriptive {@link UnsupportedWalletOperation},
   * and an override-to-raise would falsely claim the capability. Everything
   * else comes from the provider's `extraCapabilities`.
   */
  capabilities(): ReadonlySet<string> {
    const derived = new Set<string>();
    for (const [capability, method] of SIGN_METHOD_CAPABILITIES) {
      if (this[method] !== WalletProvider.prototype[method]) {
        derived.add(capability);
      }
    }
    for (const extra of this.extraCapabilities) {
      derived.add(extra);
    }
    return derived;
  }

  /** Whether `capability` is in {@link capabilities} (membership test). */
  supports(capability: string): boolean {
    return this.capabilities().has(capability);
  }

  /**
   * Return a uniform, non-sensitive summary of this wallet.
   *
   * Never includes private key material.
   */
  describe(): WalletDescription {
    let address: string | null;
    try {
      address = this.address;
    } catch {
      address = null;
    }
    return {
      kind: this.kind,
      address,
      keyLocation: this.keyLocation,
      exists: this.exists(),
      capabilities: [...this.capabilities()].sort(),
    };
  }

  /**
   * Return the {@link IntentExecutor} that runs operations for this wallet.
   *
   * This makes execution polymorphic, so callers never special-case wallet
   * kinds. The default wraps this signer in a `LocalExecutor` that builds,
   * signs and broadcasts via the provided `context` — the path every
   * pure-signing wallet (EVM, hardware, ...) shares.
   *
   * A self-broadcasting wallet (one that owns the broadcast step, e.g. a
   * CLI-backed backend) overrides this to return itself.
   *
   * The default requires the `sign.transaction` capability and throws
   * {@link UnsupportedWalletOperation} at this construction point — before
   * any intent runs — when it is absent.
   */
  makeExecutor(context: ExecutionContext): IntentExecutor {
    if (!this.supports(SIGN_TRANSACTION)) {
      throw new UnsupportedWalletOperation(SIGN_TRANSACTION, {
        reason: "the default executor signs transactions locally",
        alternative:
          "self-broadcasting wallets must override makeExecutor() " +
          "to return their own IntentExecutor",
      });
    }
    return new LocalExecutor({
      client: context.client,
      walletProvider: this,
      paymaster: context.paymaster ?? null,
      receiptTimeout: context.receiptTimeout ?? null,
    });
  }

  /**
   * Return the x402 payer for this wallet.
   *
   * The default is a **capability gate**, not dead code — the same pattern
   * as the `sign*` defaults: wallets without a payment backend get a
   * descriptive refusal at composition time, and a delegated backend (e.g.
   * twak) overrides this to return its own payer. `payerKwargs` are
   * forwarded verbatim to the payer constructor, so payer-specific options
   * never freeze this signature.
   */
  makeX402Payer(payerKwargs?: Record<string, unknown>): never {
    void payerKwargs;
    throw new UnsupportedWalletOperation(X402_PAY, {
      reason: `the '${this.kind}' wallet has no x402 payment backend in the SDK yet`,
      alternative:
        "wallets with sign.typed_data can use X402Signer directly " +
        "today; a local payer that upgrades this default is planned",
    });
  }

  /**
   * Sign a transaction.
   *
   * The default throws {@link UnsupportedWalletOperation}; implementing this
   * method declares the `sign.transaction` capability.
   */
  async signTransaction(tx: SignableTransaction): Promise<SignedTx> {
    void tx;
    throw new UnsupportedWalletOperation(SIGN_TRANSACTION, {
      reason: `the '${this.kind}' wallet does not implement raw-transaction signing`,
      alternative:
        "use a wallet whose capabilities() include 'sign.transaction', " +
        "or route high-level operations through the wallet's own " +
        "executor (makeExecutor() / execute(Intent(...)))",
    });
  }

  /**
   * Sign a message using EIP-191 personal sign.
   *
   * The default throws {@link UnsupportedWalletOperation}; implementing this
   * method declares the `sign.message` capability.
   *
   * `messageHash` on the returned {@link SignatureResult} is the **EIP-191
   * personal-sign digest** (`keccak256("\x19Ethereum Signed Message:\n" ||
   * len || message)`) — not interchangeable with the digest returned by
   * `signTypedData`.
   */
  async signMessage(message: string): Promise<SignatureResult> {
    void message;
    throw new UnsupportedWalletOperation(SIGN_MESSAGE, {
      reason: `the '${this.kind}' wallet does not implement EIP-191 personal-sign`,
      alternative: "use a wallet whose capabilities() include 'sign.message'",
    });
  }

  /**
   * Sign typed structured data per EIP-712.
   *
   * The default throws {@link UnsupportedWalletOperation}; implementing this
   * method declares the `sign.typed_data` capability.
   *
   * Used for protocols requiring signed structured payloads — EIP-3009
   * transferWithAuthorization (x402 micropay), ERC-8183 negotiate quotes,
   * permit2, etc. The signing key never leaves the wallet implementation.
   *
   * `messageHash` on the returned {@link SignatureResult} is the **EIP-712
   * typed-data digest** (`keccak256("\x19\x01" || domainSeparator ||
   * hashStruct(message))`) — not the EIP-191 digest returned by
   * `signMessage`. This is the value that on-chain `ecrecover` will use
   * against this signature.
   */
  async signTypedData(
    domain: TypedDataDomain,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult> {
    void domain;
    void types;
    void message;
    throw new UnsupportedWalletOperation(SIGN_TYPED_DATA, {
      reason: `the '${this.kind}' wallet does not implement EIP-712 typed-data signing`,
      alternative:
        "use a wallet whose capabilities() include 'sign.typed_data', " +
        "or a delegated flow that signs internally (e.g. the x402 payer " +
        "path for payments)",
    });
  }
}
