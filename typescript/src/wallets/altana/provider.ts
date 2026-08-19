/**
 * `AltanaWalletProvider` — a self-broadcasting wallet backed by the Altana
 * agentic-wallet relay, plus its `IntentExecutor`.
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
 * x402: session mode carries `x402.pay` via {@link AltanaX402Payer},
 * backed by `@altananetwork/sdk` >= 0.4.0's `signX402Payment` (an
 * ERC-7739-nested ERC-1271 envelope) after a one-time admin setup —
 * `approveX402SignatureChecker` + a bounded `setPermit2Allowance`. The
 * account's ERC-1271 still rejects raw digests (field-tested); the nested
 * envelope + checker whitelist is the supported path around that, so the
 * old dual-account workaround (separate x402 EOA) is no longer needed.
 * Admin mode has no `x402.pay`: the relay signs payments with session
 * keys only.
 *
 * The `@altananetwork/sdk` peer is loaded lazily on first backend use
 * (`./sdkLoader.js`); constructing the provider and reading
 * `address`/`describe()` never require it.
 */

import { readFileSync, statSync } from "node:fs";
import type { Abi, PublicClient } from "viem";
import { encodeFunctionData, hashMessage } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { erc20Abi } from "../../abis/erc20.js";
import { getEnv } from "../../core/envUtil.js";
import { getDefaultReceiptTimeout } from "../../core/txConfig.js";
import {
  describeError,
  sleep,
  waitForReceiptAndInterpret,
} from "../../core/txSender.js";
import type { QuoteSigner } from "../../erc8183/negotiation.js";
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
  AltanaBalancesResult,
  AltanaCall,
  AltanaExecuteResult,
  AltanaNetwork,
  AltanaNetworkConfig,
  AltanaRegisterSessionKeyResult,
  AltanaSdkClient,
  AltanaSdkClient050,
  AltanaSdkClientX402,
  AltanaSdkModule,
  AltanaSdkModule050,
  AltanaSdkModuleQuoteSigning,
  AltanaSdkModuleX402,
  AltanaSession,
  AltanaSessionPermissions,
  AltanaSignX402PaymentResult,
  AltanaSigner,
  AltanaWallet,
} from "./types.js";
import { AltanaX402Payer, type AltanaX402PayerOptions } from "./x402.js";

/** Relay nonce races retry up to this many attempts (field-tested ~4). */
export const ALTANA_NONCE_RETRY_TRIES = 4;

/** Delay between relay nonce-race retries (the relay view catches up in seconds). */
export const ALTANA_NONCE_RETRY_DELAY_MS = 5_000;

/** The relay's nonce-race error shape (`InvalidNonce`, wrapped variously). */
const NONCE_ERROR_PATTERN = /InvalidNonce|nonce/i;

/**
 * Any Unix-seconds value at or above this (~year 5138) is a milliseconds
 * timestamp passed by mistake — `Date.now()` today is ~1.7e12, and the
 * on-chain expiry field is uint40 (max ~1.1e12) so it could not even be
 * stored.
 */
const MAX_EXPIRY_SECONDS = 100_000_000_000;

/** Options accepted by the {@link AltanaWalletProvider} constructor. */
export interface AltanaWalletProviderOptions {
  /**
   * Altana deployment to talk to: the `"bnb-mainnet"` or `"bnb-testnet"`
   * preset (resolved to the SDK's own `BNB` / `BNB_TESTNET` config at
   * first backend use; the testnet preset requires `@altananetwork/sdk`
   * >= 0.5.0) or a custom `AltanaNetworkConfig`. Defaults to
   * `"bnb-mainnet"`.
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
  /**
   * Register the session's public key in the KeyStore registry (default
   * true; the registration is what costs the ~$0.50 fee). `false` grants
   * an **ephemeral** session (SDK >= 0.5.0): identical enforcement —
   * permissions/expiry live in the account either way — but invisible to
   * KeyStore readers such as `verify_authorization`, so third parties
   * cannot confirm the key's authority on-chain. Right for short-lived
   * x402 payment keys; wrong wherever a counterparty checks the registry.
   * Upgrade one later with {@link AltanaWalletProvider.registerSessionKey}.
   */
  register?: boolean;
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
 * `intents.erc8183`; session mode adds `x402.pay` (SDK >= 0.4.0). Never
 * any `sign.*`.
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

  // Assigned in the constructor: session mode adds x402.pay (payments are
  // signed by session keys only — the SDK/relay does not pay from admin).
  protected override readonly extraCapabilities: ReadonlySet<string>;

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
    this.extraCapabilities = new Set([
      BROADCAST_SELF,
      CALLS_ARBITRARY,
      INTENTS_ERC8004,
      INTENTS_ERC8183,
      ...(opts.session ? [X402_PAY] : []),
    ]);

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
      // The file holds the session private key. The SDK never writes it
      // (that's the granting side's job), so the best it can do here is
      // refuse to stay silent about a world/group-readable copy.
      if (process.platform !== "win32") {
        const mode = statSync(file).mode & 0o777;
        if ((mode & 0o077) !== 0) {
          console.warn(
            `[AltanaWalletProvider] ${file} is group/other-readable (mode ${mode.toString(8)}) but contains the session private key — chmod 600 it.`,
          );
        }
      }
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
   * the KeyStoreController) plus gas — unless `register: false`, which
   * grants an ephemeral session with no registry entry and no fee (see
   * {@link AltanaGrantSessionProviderOpts.register} for the visibility
   * trade-off). Persist the result with `serializeSession` — a re-created
   * session object with the same values but different bytes will NOT be
   * honored on-chain.
   *
   * `expiry` is validated up front (integer Unix SECONDS, in the future,
   * not a milliseconds timestamp) because a dead-on-arrival grant still
   * pays the registration fee — same rationale as `ERC8183Client.createJob`'s
   * expiredAt pre-flight.
   */
  async grantSession(
    opts: AltanaGrantSessionProviderOpts,
  ): Promise<AltanaSession> {
    this.#requireAdmin("grantSession");
    const expiry = opts.expiry;
    if (!Number.isInteger(expiry) || expiry <= 0) {
      throw new Error(
        `grantSession: expiry must be a positive integer of Unix epoch SECONDS; got ${expiry}`,
      );
    }
    if (expiry >= MAX_EXPIRY_SECONDS) {
      throw new Error(
        `grantSession: expiry ${expiry} looks like a milliseconds timestamp — pass Unix epoch SECONDS (e.g. Math.floor(Date.now() / 1000) + 3600). Refusing to pay the ~$0.50 registration fee for a broken grant.`,
      );
    }
    const nowSeconds = Math.floor(Date.now() / 1000);
    if (expiry <= nowSeconds) {
      throw new Error(
        `grantSession: expiry ${expiry} is not in the future (now ${nowSeconds}); the session would be dead on arrival. Refusing to pay the ~$0.50 registration fee for it.`,
      );
    }
    if (opts.register === false) {
      // A pre-0.5.0 SDK would silently ignore the flag and register (and
      // charge) anyway — fail here with the exact upgrade instead.
      await this.#v050Sdk("grantSession with register: false");
    }
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
          ...(opts.register !== undefined ? { register: opts.register } : {}),
          ...(opts.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
  }

  /**
   * Register an already-granted **ephemeral** session key in the KeyStore
   * registry — the lazy counterpart to `grantSession({ register: false })`.
   * Admin mode only; requires `@altananetwork/sdk` >= 0.5.0. Registration
   * changes nothing about the session's power (the account already
   * enforces permissions/expiry); it only makes the key visible to
   * registry readers such as `verify_authorization`, and costs the usual
   * ~$0.50 fee. Idempotent: an already-registered key returns
   * `{ alreadyRegistered: true }` without submitting or paying.
   */
  async registerSessionKey(
    session: AltanaSession,
    opts?: { feeToken?: `0x${string}` },
  ): Promise<AltanaRegisterSessionKeyResult> {
    this.#requireAdmin("registerSessionKey");
    const { client } = await this.#v050Sdk("registerSessionKey");
    const { wallet, signer } = await this.#adminWalletAndSigner();
    const result = await this.#enqueue(() =>
      this.#withNonceRetry("registerSessionKey", () =>
        client.registerSessionKey({
          wallet,
          signer,
          session,
          ...(opts?.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
    if (!result.alreadyRegistered && result.status === "FAILED") {
      throw new Error(
        `Altana relay reported FAILED for registerSessionKey (callsId ${result.callsId})`,
      );
    }
    return result;
  }

  /**
   * On-chain balances for this wallet: native BNB, plus any `tokens`
   * (ERC-20) requested. BEP-677 scaled-UI-amount tokens are detected via
   * ERC-165 by the SDK and their `display`/`scaled` values carry the
   * ui-multiplier; `raw` is always the unscaled `balanceOf` that
   * transfers and allowances operate on. Works in both modes (pure read —
   * no relay submission, no queue). Requires `@altananetwork/sdk` >=
   * 0.4.0; the `tokens` option requires >= 0.5.0.
   */
  async balances(opts?: {
    tokens?: readonly `0x${string}`[];
  }): Promise<AltanaBalancesResult> {
    let client: AltanaSdkClient & Partial<AltanaSdkClient050>;
    if (opts?.tokens !== undefined) {
      // Older SDKs would return native-only and silently drop `tokens`.
      ({ client } = await this.#v050Sdk("balances with tokens"));
    } else {
      client = await this.#sdkClient();
      if (typeof client.balances !== "function") {
        throw new Error(
          "Altana balances requires '@altananetwork/sdk' >= 0.4.0 (the installed version has no client.balances). Upgrade with: pnpm add @altananetwork/sdk@latest",
        );
      }
    }
    return (client as AltanaSdkClient & AltanaSdkClient050).balances({
      wallet: this.#address,
      ...(opts?.tokens !== undefined ? { tokens: opts.tokens } : {}),
    });
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
   * Delegated x402 payer for this wallet's session (see
   * {@link AltanaX402Payer}). Session mode only: the SDK/relay signs x402
   * payments with session keys exclusively; run the payer where the
   * session lives. Requires `@altananetwork/sdk` >= 0.4.0 at first use and
   * the one-time admin setup ({@link approveX402SignatureChecker} +
   * {@link setPermit2Allowance}) — without them the facilitator's
   * `isValidSignature`/`transferFrom` fails on-chain, not here.
   *
   * `payerKwargs`: {@link AltanaX402PayerOptions} (`sessionBudget`,
   * `expectedAsset`, `expectedPayTo`, `fetchImpl`).
   */
  override makeX402Payer(
    payerKwargs?: Record<string, unknown>,
  ): AltanaX402Payer {
    if (!this.#session) {
      throw new UnsupportedWalletOperation(X402_PAY, {
        reason:
          "Altana x402 payments are signed by session keys only (the SDK/relay does not pay from the admin key)",
        alternative:
          "grantSession(...) here, persist it with serializeSession, and construct a session-mode AltanaWalletProvider for the paying agent",
      });
    }
    return new AltanaX402Payer(this, payerKwargs as AltanaX402PayerOptions);
  }

  /**
   * Return a quote-only signer backed by this provider's session key.
   *
   * The signer applies the same EIP-191 string hashing as
   * `EVMWalletProvider.signMessage(negotiationHash)`, then delegates the
   * digest to Altana's `signOrder`. The result is the complete ERC-1271
   * envelope verified against {@link address}; the session key address and
   * admin EOA never enter the quote envelope or generic `sign.*` capability
   * surface. Session mode only.
   */
  sessionQuoteSigner(): QuoteSigner {
    if (!this.#session) {
      throw new UnsupportedWalletOperation("erc8183.quote.sign", {
        reason:
          "Altana quote signing needs the session (session-mode provider)",
        alternative:
          "grantSession(...) in the trusted admin environment, persist it with serializeSession, and construct a session-mode AltanaWalletProvider for the agent",
      });
    }
    const session = this.#session;
    return {
      address: this.address,
      validUntil: session.expiry,
      signQuote: async (negotiationHash: string): Promise<string> => {
        const sdk = (await loadAltanaSdk()) as AltanaSdkModule &
          Partial<AltanaSdkModuleQuoteSigning>;
        if (typeof sdk.signOrder !== "function") {
          throw new Error(
            "Altana quote signing requires '@altananetwork/sdk' >= 0.4.0 (the installed version lacks signOrder). Upgrade with: pnpm add @altananetwork/sdk@latest",
          );
        }
        return sdk.signOrder(session, hashMessage(negotiationHash));
      },
    };
  }

  /**
   * Trusted setup for quote verification: authorize `checker` (normally the
   * ERC-8183 Commerce/verifier contract) to call this session's ERC-1271
   * validation path. Admin mode only; the agent never needs the admin EOA.
   *
   * Revoking the session/checker later prevents current-state acceptance but
   * does not invalidate a quote already verified at its `JobFunded`
   * acceptance block via `verifyQuoteSignature({ blockNumber })`.
   */
  async approveQuoteSignatureChecker(
    session: AltanaSession,
    checker: `0x${string}`,
    opts?: { feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    return this.#setSignatureChecker("approveSignatureChecker", session, {
      checker,
      ...(opts?.feeToken ? { feeToken: opts.feeToken } : {}),
    });
  }

  /**
   * One-time x402 setup, step 1 of 2 (admin): whitelist `checker`
   * (default: Permit2) for `session`'s ERC-1271 verification — a relay
   * self-call to `setSignatureCheckerApproval(sessionKeyHash, checker,
   * true)`. Without it, `isValidSignature` answers the facilitator with a
   * failure and every payment settles as invalid. Once per session per
   * rail; costs the relay fee + gas. Requires `@altananetwork/sdk` >= 0.4.0.
   */
  async approveX402SignatureChecker(
    session: AltanaSession,
    opts?: { checker?: `0x${string}`; feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    return this.#setSignatureChecker("approveSignatureChecker", session, opts);
  }

  /**
   * Revoke a checker approved via {@link approveX402SignatureChecker}
   * (admin). Immediate kill switch for the session's x402 signing —
   * cheaper-grained than revoking the whole session.
   */
  async revokeX402SignatureChecker(
    session: AltanaSession,
    opts?: { checker?: `0x${string}`; feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    return this.#setSignatureChecker("revokeSignatureChecker", session, opts);
  }

  /**
   * One-time x402 setup, step 2 of 2 (admin): `approve(Permit2, amount)`
   * on `token` through the relay. BOUNDED by design — session spend caps
   * do not apply to Permit2 pulls (the checker gate is independent of
   * spend permissions), so this allowance is the on-chain ceiling on what
   * a leaked session key can spend via x402. Size it like the old
   * dedicated-EOA balance; top it up or zero it (amount = 0n) as needed.
   * The relay races nothing here (admin queue applies as usual).
   *
   * Delegates to the SDK's `approveTokenForPermit2` with an explicit
   * `amount` (bounded-allowance support confirmed by the Altana team).
   */
  async setPermit2Allowance(
    token: `0x${string}`,
    amount: bigint,
    opts?: { feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    this.#requireAdmin("setPermit2Allowance");
    const { client } = await this.#x402Sdk();
    const { wallet, signer } = await this.#adminWalletAndSigner();
    const result = await this.#enqueue(() =>
      this.#withNonceRetry("setPermit2Allowance", () =>
        client.approveTokenForPermit2({
          wallet,
          signer,
          token,
          amount,
          ...(opts?.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
    if (result.status === "FAILED") {
      throw new Error(
        `Altana relay reported FAILED for setPermit2Allowance (callsId ${result.callsId})`,
      );
    }
    return result;
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

  /**
   * Sign one raw 402 `accepts[]` requirement with this provider's session
   * via the SDK's module-level `signX402Payment(session, requirement)` →
   * complete `X-PAYMENT` header value. Signing is local to the SDK (no
   * relay submission), so it bypasses the serial queue and nonce retry;
   * it is chain-independent (the requirement's `network` carries the
   * chain).
   *
   * @internal Used by {@link AltanaX402Payer}.
   */
  async _signX402Payment(
    requirement: Record<string, unknown>,
  ): Promise<AltanaSignX402PaymentResult> {
    if (!this.#session) {
      throw new UnsupportedWalletOperation(X402_PAY, {
        reason: "x402 signing needs the session (session-mode provider)",
      });
    }
    const { module } = await this.#x402Sdk();
    const result = await module.signX402Payment(this.#session, requirement);
    if (typeof result?.header !== "string" || result.header.length === 0) {
      throw new Error(
        "Altana signX402Payment returned no X-PAYMENT header value; cannot pay",
      );
    }
    return result;
  }

  /**
   * The chain this provider pays x402 on (route-selection pin).
   *
   * @internal Used by {@link AltanaX402Payer}.
   */
  async _x402ChainId(): Promise<number> {
    if (typeof this.#network !== "string") {
      return this.#network.chainId;
    }
    const sdk = await loadAltanaSdk();
    return this.#resolveNetwork(sdk).chainId;
  }

  /** Shared body of approve/revokeX402SignatureChecker (admin, queued). */
  async #setSignatureChecker(
    method: "approveSignatureChecker" | "revokeSignatureChecker",
    session: AltanaSession,
    opts?: { checker?: `0x${string}`; feeToken?: `0x${string}` },
  ): Promise<AltanaExecuteResult> {
    this.#requireAdmin(method);
    const { client, module } = await this.#x402Sdk();
    const { wallet, signer } = await this.#adminWalletAndSigner();
    const result = await this.#enqueue(() =>
      this.#withNonceRetry(method, () =>
        client[method]({
          wallet,
          signer,
          session,
          checker: opts?.checker ?? module.PERMIT2_ADDRESS,
          ...(opts?.feeToken ? { feeToken: opts.feeToken } : {}),
        }),
      ),
    );
    if (result.status === "FAILED") {
      throw new Error(
        `Altana relay reported FAILED for ${method} (callsId ${result.callsId})`,
      );
    }
    return result;
  }

  /**
   * The SDK module + client, gated on the x402 surface shipped in
   * `@altananetwork/sdk` 0.4.0 (`signX402Payment` is module-level; the
   * checker methods live on the client). Duck-checked at first x402 use
   * so a 0.3.3 install keeps every non-x402 path working and fails here
   * with the exact upgrade to run.
   */
  async #x402Sdk(): Promise<{
    client: AltanaSdkClient & AltanaSdkClientX402;
    module: AltanaSdkModule & AltanaSdkModuleX402;
  }> {
    const sdk = (await loadAltanaSdk()) as AltanaSdkModule &
      Partial<AltanaSdkModuleX402>;
    const client = (await this.#sdkClient()) as AltanaSdkClient &
      Partial<AltanaSdkClientX402>;
    const missing = [
      typeof sdk.signX402Payment === "function" ? null : "signX402Payment",
      typeof client.signOrderTypedData === "function"
        ? null
        : "signOrderTypedData",
      typeof client.approveSignatureChecker === "function"
        ? null
        : "approveSignatureChecker",
      typeof sdk.PERMIT2_ADDRESS === "string" ? null : "PERMIT2_ADDRESS",
    ].filter(Boolean);
    if (missing.length > 0) {
      throw new Error(
        `Altana x402 support requires '@altananetwork/sdk' >= 0.4.0 (the installed version lacks ${missing.join(", ")}). Upgrade with: pnpm add @altananetwork/sdk@latest`,
      );
    }
    return {
      client: client as AltanaSdkClient & AltanaSdkClientX402,
      module: sdk as AltanaSdkModule & AltanaSdkModuleX402,
    };
  }

  /**
   * The SDK module + client, gated on the 0.5.0 surface (`BNB_TESTNET`,
   * `registerSessionKey`, ERC-20/BEP-677 `balances`). Same duck-check
   * pattern as {@link #x402Sdk}: pre-0.5.0 installs keep every older path
   * working and fail here with the exact upgrade to run.
   */
  async #v050Sdk(feature: string): Promise<{
    client: AltanaSdkClient & AltanaSdkClient050;
    module: AltanaSdkModule & AltanaSdkModule050;
  }> {
    const sdk = (await loadAltanaSdk()) as AltanaSdkModule &
      Partial<AltanaSdkModule050>;
    const client = (await this.#sdkClient()) as AltanaSdkClient &
      Partial<AltanaSdkClient050>;
    const missing = [
      typeof client.registerSessionKey === "function"
        ? null
        : "registerSessionKey",
      typeof client.balances === "function" ? null : "balances",
      sdk.BNB_TESTNET ? null : "BNB_TESTNET",
    ].filter(Boolean);
    if (missing.length > 0) {
      throw new Error(
        `Altana ${feature} requires '@altananetwork/sdk' >= 0.5.0 (the installed version lacks ${missing.join(", ")}). Upgrade with: pnpm add @altananetwork/sdk@latest`,
      );
    }
    return {
      client: client as AltanaSdkClient & AltanaSdkClient050,
      module: sdk as AltanaSdkModule & AltanaSdkModule050,
    };
  }

  #requireAdmin(operation: string): void {
    if (this.#session) {
      throw new Error(
        `${operation} requires an admin-mode AltanaWalletProvider (constructed with privateKey or signer); a session-mode provider can only execute within its grant. Run ${operation} wherever the admin key lives.`,
      );
    }
  }

  /** Resolve a network preset to the SDK's own config; pass customs through. */
  #resolveNetwork(sdk: AltanaSdkModule): AltanaNetworkConfig {
    if (typeof this.#network !== "string") {
      return this.#network;
    }
    if (this.#network === "bnb-mainnet") {
      return sdk.BNB;
    }
    const testnet = (sdk as AltanaSdkModule & Partial<AltanaSdkModule050>)
      .BNB_TESTNET;
    if (!testnet) {
      throw new Error(
        "The 'bnb-testnet' network preset requires '@altananetwork/sdk' >= 0.5.0 (the installed version has no BNB_TESTNET export). Upgrade with: pnpm add @altananetwork/sdk@latest",
      );
    }
    return testnet;
  }

  async #sdkClient(): Promise<AltanaSdkClient> {
    if (!this.#sdkClientPromise) {
      const promise = (async () => {
        const sdk = await loadAltanaSdk();
        return sdk.createClient({ chains: [this.#resolveNetwork(sdk)] });
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
      { requireTransactionSeen: true },
    );
    return { ...txResult, callsId: result.callsId };
  }
}
