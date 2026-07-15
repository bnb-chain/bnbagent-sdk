/**
 * `AltanaWalletProvider` — a self-broadcasting wallet backed by the Altana
 * (formerly Functor) agentic-wallet relay, plus its `IntentExecutor`.
 *
 * Altana wallets are EIP-7702 accounts: the wallet address IS the admin
 * EOA, upgraded in place on first execute. An **admin** key manages the
 * wallet and grants on-chain-enforced session keys (call whitelist + spend
 * caps + expiry, committed to the public KeyStore registry); a **session**
 * key can only execute within its grant. This provider exposes both modes:
 *
 * - admin mode (`{ privateKey }` or `{ signer }`): full control —
 *   `grantSession` / `revokeSession` plus intent execution.
 * - session mode (`{ session }`): execution only, inside the grant. This
 *   is what an agent process runs with.
 *
 * Execution is relay-shaped, not signer-shaped: the relay builds,
 * broadcasts and fronts gas for a signed intent (recovering the fee from
 * the wallet in the same transaction), so this provider implements NO
 * `sign*` method — per the don't-override-to-raise discipline it simply
 * never declares `sign.*` capabilities — and `makeExecutor` returns an
 * {@link AltanaIntentExecutor} instead of the local build/sign/broadcast
 * path. All ERC-8004/8183 writes flow through the mechanical `Intent.call`
 * form (Altana executes arbitrary calls; no per-operation handlers).
 *
 * x402 is deliberately absent from the capability set: EIP-3009
 * `transferWithAuthorization` is verified by the TOKEN contract outside
 * the wallet's execute() path, where a session signature recovers to the
 * session key (not the wallet) and the account's ERC-1271 rejects raw
 * digests by design. Field-tested dead end — see `makeX402Payer`.
 *
 * The `@altananetwork/sdk` peer is loaded lazily on first backend use
 * (`./sdkLoader.js`); constructing the provider and reading
 * `address`/`describe()` never require it.
 */

import { readFileSync } from "node:fs";
import type { Abi, PublicClient } from "viem";
import { encodeFunctionData } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { erc20Abi } from "../../abis/erc20.js";
import { getEnv } from "../../core/envUtil.js";
import { getDefaultReceiptTimeout } from "../../core/txConfig.js";
import {
  describeError,
  sleep,
  waitForReceiptAndInterpret,
} from "../../core/txSender.js";
import {
  BROADCAST_SELF,
  CALLS_ARBITRARY,
  INTENTS_ERC8004,
  INTENTS_ERC8183,
  X402_PAY,
} from "../capabilities.js";
import {
  UnsupportedWalletOperation,
  WalletIdentityMismatch,
} from "../errors.js";
import { EVMWalletProvider } from "../evmWalletProvider.js";
import {
  ERC8183_FUND,
  type ExecutionContext,
  type Intent,
  type IntentExecutor,
  type TxResult,
} from "../intents.js";
import { WalletProvider } from "../walletProvider.js";
import { loadAltanaSdk } from "./sdkLoader.js";
import { deserializeSession } from "./session.js";
import type {
  AltanaCall,
  AltanaExecuteResult,
  AltanaNetwork,
  AltanaSdkClient,
  AltanaSession,
  AltanaSessionPermissions,
  AltanaSigner,
  AltanaWallet,
} from "./types.js";

/** Relay nonce races retry up to this many attempts (field-tested ~4). */
export const ALTANA_NONCE_RETRY_TRIES = 4;

/** Delay between relay nonce-race retries (the relay view catches up in seconds). */
export const ALTANA_NONCE_RETRY_DELAY_MS = 5_000;

/** The relay's nonce-race error shape (`InvalidNonce`, wrapped variously). */
const NONCE_ERROR_PATTERN = /InvalidNonce|nonce/i;

/** Options accepted by the {@link AltanaWalletProvider} constructor. */
export interface AltanaWalletProviderOptions {
  /**
   * Altana deployment to talk to: the `"bnb-mainnet"` preset (resolved to
   * the SDK's own `BNB` config at first backend use) or a custom
   * `AltanaNetworkConfig`. Defaults to `"bnb-mainnet"`.
   */
  network?: AltanaNetwork;
  /** Admin mode: the admin EOA's private key (hex, with or without `0x`). */
  privateKey?: string;
  /**
   * Admin mode with a custom signer (`privateKey` or `injected` type).
   * Passkey signers are rejected: their wallet address is only known after
   * an async `createWallet`, which breaks the synchronous `address`
   * contract — drive those through the Altana SDK directly.
   */
  signer?: AltanaSigner;
  /** Session mode: a granted (or `deserializeSession`-restored) session. */
  session?: AltanaSession;
  /** Nonce-race retry tuning (tests use `delayMs: 0`). */
  nonceRetry?: { tries?: number; delayMs?: number };
}

/** Options accepted by {@link AltanaWalletProvider.grantSession}. */
export interface AltanaGrantSessionProviderOpts {
  permissions: AltanaSessionPermissions;
  /** Unix epoch seconds. */
  expiry: number;
  /** Omit to let the SDK generate a fresh secp256k1 session signer. */
  sessionSigner?: AltanaSigner;
  feeToken?: `0x${string}`;
}

/** Options accepted by {@link AltanaWalletProvider.adminFromKeystore}. */
export interface AltanaAdminFromKeystoreOpts {
  /** Password for the encrypted EVM keystore. */
  password: string;
  /** Keystore address to load (auto-selects when exactly one exists). */
  address?: string;
  /** Override the keystore directory (default `~/.bnbagent/wallets/`). */
  walletsDir?: string;
  network?: AltanaNetwork;
}

/** Options accepted by {@link AltanaWalletProvider.sessionFromEnv}. */
export interface AltanaSessionFromEnvOpts {
  network?: AltanaNetwork;
  nonceRetry?: { tries?: number; delayMs?: number };
}

/**
 * Wallet provider for Altana agentic wallets (see module docstring).
 *
 * Capabilities: `broadcast.self`, `calls.arbitrary`, `intents.erc8004`,
 * `intents.erc8183`. Never any `sign.*`, never `x402.pay`.
 */
export class AltanaWalletProvider extends WalletProvider {
  static override readonly kind = "altana";

  /**
   * Altana's `execute` runs approve + fund as one atomic relay batch (see
   * {@link AltanaIntentExecutor}), so the SDK-side allowance management in
   * `ERC8183Client.fund` must be skipped — that path would call
   * `sendTx` → `signTransaction`, which this wallet does not have.
   */
  override readonly fundBundlesApproval = true;

  protected override readonly extraCapabilities: ReadonlySet<string> = new Set([
    BROADCAST_SELF,
    CALLS_ARBITRARY,
    INTENTS_ERC8004,
    INTENTS_ERC8183,
  ]);

  readonly #network: AltanaNetwork;
  readonly #address: `0x${string}`;
  readonly #privateKey: `0x${string}` | null;
  readonly #adminSigner: AltanaSigner | null;
  readonly #session: AltanaSession | null;
  readonly #nonceRetryTries: number;
  readonly #nonceRetryDelayMs: number;

  #sdkClientPromise: Promise<AltanaSdkClient> | null = null;
  #adminPromise: Promise<{
    wallet: AltanaWallet;
    signer: AltanaSigner;
  }> | null = null;
  /** paymentToken cache, keyed by lowercased commerce address. */
  readonly #paymentTokens = new Map<string, Promise<`0x${string}`>>();
  /**
   * Serial submission queue: the relay races its own account-nonce view
   * when intents land back-to-back, so one provider never lets two of its
   * relay submissions (execute / grantSession / revokeSession) overlap.
   * Cross-process races still exist — the nonce retry absorbs those.
   */
  #queueTail: Promise<unknown> = Promise.resolve();

  constructor(opts: AltanaWalletProviderOptions) {
    super();
    const sources = [opts.privateKey, opts.signer, opts.session].filter(
      (value) => value !== undefined,
    ).length;
    if (sources !== 1) {
      throw new Error(
        `AltanaWalletProvider requires exactly one of privateKey (admin mode), signer (admin mode) or session (session mode); got ${sources === 0 ? "none" : "more than one"}.`,
      );
    }

    this.#network = opts.network ?? "bnb-mainnet";
    this.#nonceRetryTries = opts.nonceRetry?.tries ?? ALTANA_NONCE_RETRY_TRIES;
    this.#nonceRetryDelayMs =
      opts.nonceRetry?.delayMs ?? ALTANA_NONCE_RETRY_DELAY_MS;

    if (opts.session) {
      this.#session = opts.session;
      this.#privateKey = null;
      this.#adminSigner = null;
      this.#address = opts.session.walletAddress;
      return;
    }

    this.#session = null;
    if (opts.signer) {
      if (opts.signer.type === "passkey") {
        throw new Error(
          "AltanaWalletProvider does not support passkey admin signers: a passkey wallet's address is only known after an async createWallet (it is a bootstrap EOA, not the signer's address). Use the Altana SDK directly for passkey flows, or an EOA-backed signer here.",
        );
      }
      this.#adminSigner = opts.signer;
      this.#privateKey = null;
      this.#address = opts.signer.address;
      return;
    }

    // privateKey admin mode. The address derives synchronously from the key
    // (EIP-7702: the wallet address IS this EOA) — no SDK load needed.
    this.#adminSigner = null;
    const raw = opts.privateKey as string;
    const stripped = raw.startsWith("0x") ? raw.slice(2) : raw;
    if (!/^[0-9a-fA-F]{64}$/.test(stripped)) {
      throw new Error(
        "Invalid private key: must be 64 hex characters (32 bytes)",
      );
    }
    this.#privateKey = `0x${stripped}`;
    this.#address = privateKeyToAccount(this.#privateKey).address;
  }

  /** `"admin"` (can grant/revoke sessions) or `"session"` (execute only). */
  get mode(): "admin" | "session" {
    return this.#session ? "session" : "admin";
  }

  /**
   * The wallet's on-chain address. EIP-7702 makes this the admin EOA
   * itself; in session mode it is the granted session's `walletAddress`.
   */
  get address(): `0x${string}` {
    return this.#address;
  }

  /**
   * Admin mode from an encrypted Keystore V3 file on disk — reuses
   * {@link EVMWalletProvider}'s keystore handling (scrypt + AES-128-CTR,
   * `~/.bnbagent/wallets/`) so one keystore can back both the local-signing
   * and the Altana identity of the same EOA. Follows EVMWalletProvider's
   * get-or-create flow: a missing keystore is created and persisted.
   */
  static adminFromKeystore(
    opts: AltanaAdminFromKeystoreOpts,
  ): AltanaWalletProvider {
    const evm = new EVMWalletProvider({
      password: opts.password,
      address: opts.address,
      walletsDir: opts.walletsDir,
    });
    return new AltanaWalletProvider({
      privateKey: evm.exportPrivateKey(),
      network: opts.network,
    });
  }

  /**
   * Session mode from the environment: `ALTANA_SESSION` (the serialized
   * session JSON, wins when both are set) or `ALTANA_SESSION_FILE` (path
   * to it). The payload contains the session private key — treat the file
   * like key material (mode 0600, never commit).
   */
  static async sessionFromEnv(
    opts?: AltanaSessionFromEnvOpts,
  ): Promise<AltanaWalletProvider> {
    const inline = getEnv("ALTANA_SESSION");
    const file = getEnv("ALTANA_SESSION_FILE");
    let serialized: string;
    if (inline) {
      serialized = inline;
    } else if (file) {
      serialized = readFileSync(file, "utf8");
    } else {
      throw new Error(
        "no Altana session in the environment: set ALTANA_SESSION (the serialized session JSON) or ALTANA_SESSION_FILE (path to a session file written from serializeSession output).",
      );
    }
    const session = await deserializeSession(serialized);
    return new AltanaWalletProvider({
      session,
      network: opts?.network,
      nonceRetry: opts?.nonceRetry,
    });
  }

  /**
   * Grant an on-chain session key scoped by `permissions` until `expiry`.
   * Admin mode only. Registration costs ~$0.50 in native BNB (charged by
   * the KeyStoreController) plus gas. Persist the result with
   * `serializeSession` — a re-created session object with the same values
   * but different bytes will NOT be honored on-chain.
   */
  async grantSession(
    opts: AltanaGrantSessionProviderOpts,
  ): Promise<AltanaSession> {
    this.#requireAdmin("grantSession");
    const sdkClient = await this.#sdkClient();
    const { wallet, signer } = await this.#adminWalletAndSigner();
    return this.#enqueue(() =>
      this.#withNonceRetry("grantSession", () =>
        sdkClient.grantSession({
          wallet,
          signer,
          permissions: opts.permissions,
          expiry: opts.expiry,
          ...(opts.sessionSigner ? { sessionSigner: opts.sessionSigner } : {}),
          ...(opts.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
  }

  /**
   * Revoke a session (by object or by its public key). Admin mode only.
   * Free at the protocol level (gas only); effect is immediate — the
   * session's next execute fails at the on-chain validator.
   */
  async revokeSession(
    session: AltanaSession | `0x${string}`,
    opts?: { feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    this.#requireAdmin("revokeSession");
    const sdkClient = await this.#sdkClient();
    const { wallet, signer } = await this.#adminWalletAndSigner();
    const result = await this.#enqueue(() =>
      this.#withNonceRetry("revokeSession", () =>
        sdkClient.revokeSession({
          wallet,
          signer,
          session,
          ...(opts?.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
    if (result.status === "FAILED") {
      throw new Error(
        `Altana relay reported FAILED for revokeSession (callsId ${result.callsId})`,
      );
    }
    return result;
  }

  /**
   * Self-broadcasting wallets are their own executor: returns an
   * {@link AltanaIntentExecutor} bound to this provider and `context`.
   * Requires no `sign.transaction` — the relay owns build/broadcast.
   */
  override makeExecutor(context: ExecutionContext): IntentExecutor {
    return new AltanaIntentExecutor(this, context);
  }

  /**
   * x402 payments are structurally unsupported on Altana wallets — this is
   * a protocol fact, not a missing feature. EIP-3009
   * `transferWithAuthorization` is verified by the token contract outside
   * the wallet's execute() path: `ecrecover` resolves a session signature
   * to the session key (never the wallet), and the Porto account's
   * ERC-1271 rejects all raw-digest signatures by anti-replay design.
   * Field-tested on-chain in both directions.
   */
  override makeX402Payer(payerKwargs?: Record<string, unknown>): never {
    void payerKwargs;
    throw new UnsupportedWalletOperation(X402_PAY, {
      reason:
        "Altana wallets cannot delegate x402/EIP-3009 payment signing to a session key (the token verifies signatures outside the wallet's execute path; the account's ERC-1271 rejects raw digests)",
      alternative:
        "use a separate dedicated low-balance EOA for x402 payments (EVMWalletProvider + X402Signer) and keep the Altana wallet for on-chain execution; RECEIVING x402 payments at the Altana wallet address works fine",
    });
  }

  // ── Internal plumbing (shared by the executor; not public API) ─────────

  /**
   * Submit a batch of calls through the relay as ONE atomic intent, on the
   * provider's serial queue with nonce-race retry. Dispatches the admin or
   * session path by construction mode.
   *
   * @internal Used by {@link AltanaIntentExecutor}.
   */
  async _relayExecute(
    calls: readonly AltanaCall[],
    label: string,
  ): Promise<AltanaExecuteResult> {
    const sdkClient = await this.#sdkClient();
    if (this.#session) {
      const session = this.#session;
      return this.#enqueue(() =>
        this.#withNonceRetry(label, () =>
          sdkClient.execute({ session, calls }),
        ),
      );
    }
    const { wallet, signer } = await this.#adminWalletAndSigner();
    return this.#enqueue(() =>
      this.#withNonceRetry(label, () =>
        sdkClient.execute({ wallet, signer, calls }),
      ),
    );
  }

  /**
   * Resolve (and cache forever) the ERC-8183 kernel's payment token via
   * the execution context's PublicClient. The fund intent does not carry
   * the token address — it is immutable on the kernel, so one read per
   * kernel per provider suffices.
   *
   * @internal Used by {@link AltanaIntentExecutor}.
   */
  async _paymentTokenFor(
    client: PublicClient,
    commerce: `0x${string}`,
    abi: Abi,
  ): Promise<`0x${string}`> {
    const key = commerce.toLowerCase();
    let cached = this.#paymentTokens.get(key);
    if (!cached) {
      cached = client
        .readContract({ address: commerce, abi, functionName: "paymentToken" })
        .then(
          (token) => {
            if (typeof token !== "string" || !token.startsWith("0x")) {
              throw new Error(
                `paymentToken() on ${commerce} returned a non-address value: ${String(token)}`,
              );
            }
            return token as `0x${string}`;
          },
          (error: unknown) => {
            // Don't cache failures — the next fund retries the read.
            this.#paymentTokens.delete(key);
            throw error;
          },
        );
      this.#paymentTokens.set(key, cached);
    }
    return cached;
  }

  #requireAdmin(operation: string): void {
    if (this.#session) {
      throw new Error(
        `${operation} requires an admin-mode AltanaWalletProvider (constructed with privateKey or signer); a session-mode provider can only execute within its grant. Run ${operation} wherever the admin key lives.`,
      );
    }
  }

  async #sdkClient(): Promise<AltanaSdkClient> {
    if (!this.#sdkClientPromise) {
      const promise = (async () => {
        const sdk = await loadAltanaSdk();
        const network =
          this.#network === "bnb-mainnet" ? sdk.BNB : this.#network;
        return sdk.createClient({ chains: [network] });
      })();
      this.#sdkClientPromise = promise;
      promise.catch(() => {
        if (this.#sdkClientPromise === promise) {
          this.#sdkClientPromise = null;
        }
      });
    }
    return this.#sdkClientPromise;
  }

  /**
   * Admin wallet handle + signer, created exactly once per provider
   * (`createWallet` registers the account with the relay; it is
   * counterfactual and idempotent, but there is no reason to repeat it).
   */
  async #adminWalletAndSigner(): Promise<{
    wallet: AltanaWallet;
    signer: AltanaSigner;
  }> {
    if (!this.#adminPromise) {
      const promise = (async () => {
        const sdk = await loadAltanaSdk();
        const signer =
          this.#adminSigner ??
          sdk.signerFromPrivateKey(this.#privateKey as `0x${string}`);
        const sdkClient = await this.#sdkClient();
        const created = await sdkClient.createWallet({ signer });
        if (created.address.toLowerCase() !== this.#address.toLowerCase()) {
          // EIP-7702 pins wallet == admin EOA; a drifted backend identity
          // must never be signed for (same INV-4 rule as the other
          // providers).
          throw new WalletIdentityMismatch({
            expected: this.#address,
            actual: created.address,
          });
        }
        return { wallet: { address: created.address }, signer };
      })();
      this.#adminPromise = promise;
      promise.catch(() => {
        if (this.#adminPromise === promise) {
          this.#adminPromise = null;
        }
      });
    }
    return this.#adminPromise;
  }

  /** Chain `task` onto the serial queue; a failed task never poisons it. */
  #enqueue<T>(task: () => Promise<T>): Promise<T> {
    const result = this.#queueTail.then(() => task());
    this.#queueTail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  /** Retry `fn` on relay nonce races (see the queue comment); rethrow the rest. */
  async #withNonceRetry<T>(label: string, fn: () => Promise<T>): Promise<T> {
    for (let attempt = 1; ; attempt++) {
      try {
        return await fn();
      } catch (error) {
        const message = describeError(error);
        if (
          attempt >= this.#nonceRetryTries ||
          !NONCE_ERROR_PATTERN.test(message)
        ) {
          throw error;
        }
        console.warn(
          `[AltanaWalletProvider] ${label}: relay nonce race (attempt ${attempt}/${this.#nonceRetryTries}), retrying in ${(this.#nonceRetryDelayMs / 1000).toFixed(1)}s`,
        );
        await sleep(this.#nonceRetryDelayMs);
      }
    }
  }
}

/**
 * Executes {@link Intent}s through the Altana relay.
 *
 * Consumes the intent's mechanical `call` form (protocol-agnostic, like
 * `LocalExecutor`) with ONE semantic special case: an `erc8183.fund`
 * intent is prepended with an exact-amount `approve(commerce,
 * expectedBudget)` in the SAME relay batch, because `fund` pulls the
 * escrow via `transferFrom` and the wallet has no separate-signing path
 * for a standalone approve. `fund` consumes the entire approved amount,
 * so the allowance returns to zero — no USDT-style reset-to-zero hazard.
 *
 * The relay result carries no receipt, so the executor waits for one via
 * the context's PublicClient with the shared
 * `waitForReceiptAndInterpret` — success/revert/timeout semantics are
 * byte-identical to the local signing path (callers already parse
 * `result.receipt.logs`, which works because a 7702 batch's inner logs
 * carry the emitting contract's address).
 */
export class AltanaIntentExecutor implements IntentExecutor {
  readonly #provider: AltanaWalletProvider;
  readonly #context: ExecutionContext;

  constructor(provider: AltanaWalletProvider, context: ExecutionContext) {
    this.#provider = provider;
    this.#context = context;
  }

  async execute(intent: Intent): Promise<TxResult> {
    const call = intent.call;
    if (!call) {
      const label = intent.name ?? intent.description ?? "None";
      throw new Error(
        `AltanaIntentExecutor requires Intent.call (a pre-encoded contract call); got None for intent '${label}'`,
      );
    }
    const label = intent.description ?? intent.name ?? "transaction";

    const calls: AltanaCall[] = [
      {
        to: call.address,
        value: intent.value ?? 0n,
        data: encodeFunctionData({
          abi: call.abi,
          functionName: call.functionName,
          args: call.args,
        }),
      },
    ];

    if (intent.name === ERC8183_FUND) {
      const amount =
        (intent.kwargs?.expectedBudget as bigint | undefined) ??
        (call.args[1] as bigint | undefined);
      if (typeof amount !== "bigint") {
        throw new Error(
          "erc8183.fund intent is missing its amount (kwargs.expectedBudget / call.args[1]); cannot build the bundled approve",
        );
      }
      const token = await this.#provider._paymentTokenFor(
        this.#context.client,
        call.address,
        call.abi,
      );
      calls.unshift({
        to: token,
        value: 0n,
        data: encodeFunctionData({
          abi: erc20Abi,
          functionName: "approve",
          args: [call.address, amount],
        }),
      });
    }

    const result = await this.#provider._relayExecute(calls, label);
    if (result.status === "FAILED") {
      throw new Error(
        `Altana relay reported FAILED for ${label} (callsId ${result.callsId})`,
      );
    }
    if (!result.transactionHash) {
      // The relay's PENDING semantics are undocumented; without a hash
      // there is nothing to wait on, so surface the callsId for manual
      // follow-up instead of pretending success.
      throw new Error(
        `Altana relay returned status ${result.status} without a transactionHash for ${label} (callsId ${result.callsId}); cannot confirm on-chain inclusion`,
      );
    }

    const timeoutSeconds =
      this.#context.receiptTimeout ?? getDefaultReceiptTimeout();
    const txResult = await waitForReceiptAndInterpret(
      this.#context.client,
      result.transactionHash,
      timeoutSeconds,
    );
    return { ...txResult, callsId: result.callsId };
  }
}
