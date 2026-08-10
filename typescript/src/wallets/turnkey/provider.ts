/**
 * Turnkey Wallet Provider — remote signing, keys in AWS Nitro Enclaves.
 *
 * A pure signer over Turnkey's hosted key-management API
 * (https://docs.turnkey.com): the private key is generated and held inside
 * Turnkey's enclave and never leaves it; every sign call is an
 * authenticated HTTPS round-trip stamped by a locally held P-256 API key
 * pair. No key material ever exists on this machine.
 *
 * Operational constraints that shaped this implementation:
 *
 * - **Every successful signature is billed** (free tier: 25/month at
 *   1 request/second; pay-as-you-go $0.10/signature). All client-side
 *   guards (SigningPolicy, chain-id pinning, input validation) run BEFORE
 *   the API call so a refusal never costs quota.
 * - **Root API keys bypass ALL Turnkey server-side policies** (root
 *   quorum). Production deployments must use a non-root API user plus an
 *   explicit ALLOW policy; this cannot be detected client-side.
 * - **Broadcast is not included** (managed broadcast is a paid feature).
 *   The provider only signs; the default `LocalExecutor` broadcasts over
 *   the SDK's own RPC, and the MegaFuel paymaster path works unchanged.
 * - **EIP-712 domain-stripping trap** (`@turnkey/viem` ≤0.14.34, probe
 *   finding 2026-07-24): when the `types` object passed to the Turnkey
 *   account lacks an explicit `EIP712Domain` entry, viem's
 *   `serializeTypedData` silently serializes the domain as `{}` — the
 *   signature succeeds, is billed, and binds an EMPTY domain (unverifiable
 *   / replayable across domains). {@link TurnkeyWalletProvider.signTypedData}
 *   therefore always injects the full `EIP712Domain` type into the enclave
 *   payload. The injection is idempotent, so it stays safe if upstream
 *   fixes the default.
 */

import type {
  LocalAccount,
  TransactionSerializable,
  TypedDataDomain,
} from "viem";
import {
  getAddress,
  getTypesForEIP712Domain,
  hashMessage,
  hashTypedData,
  keccak256,
  parseSignature,
  parseTransaction,
} from "viem";
import { getEnv } from "../../core/envUtil.js";
import { check, inferPrimaryType } from "../../signing/checks.js";
import { SigningPolicy } from "../../signing/policy.js";
import { CALLS_ARBITRARY, PAYMASTER_SPONSOR } from "../capabilities.js";
import { WalletIdentityMismatch } from "../errors.js";
import type {
  SignableTransaction,
  SignatureResult,
  SignedTx,
} from "../walletProvider.js";
import { WalletProvider } from "../walletProvider.js";
import { loadTurnkeySdk } from "./sdkLoader.js";

/** Default Turnkey API host. */
export const TURNKEY_API_BASE_URL_DEFAULT = "https://api.turnkey.com";

const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

/** Constructor options for {@link TurnkeyWalletProvider}. */
export interface TurnkeyWalletProviderOptions {
  /** Turnkey organization id (dashboard → settings). */
  organizationId: string;
  /**
   * The wallet account to sign with — MUST be the account's Ethereum
   * address (`0x` + 40 hex chars), not a Turnkey wallet id or private-key
   * id. Constraining to the address keeps {@link TurnkeyWalletProvider.address}
   * synchronous and avoids a lookup round-trip.
   */
  signWith: string;
  /** P-256 API key public component (dashboard → API keys). */
  apiPublicKey: string;
  /** P-256 API key private component. A client credential — never leaves this process. */
  apiPrivateKey: string;
  /** API host override (default {@link TURNKEY_API_BASE_URL_DEFAULT}). */
  apiBaseUrl?: string;
  /**
   * When set, {@link TurnkeyWalletProvider.signTransaction} refuses any
   * transaction whose `chainId` differs — fail-closed BEFORE the billable
   * API call.
   */
  expectedChainId?: number;
  /**
   * Policy applied to every {@link TurnkeyWalletProvider.signTypedData}
   * call, BEFORE the billable API call. Defaults to
   * {@link SigningPolicy.strictDefault}. This client-side gate is the first
   * of two layers — Turnkey's server-side policy engine is the second (and
   * is bypassed entirely for root API users).
   */
  signingPolicy?: SigningPolicy;
}

/** Options accepted by {@link TurnkeyWalletProvider.fromEnv}. */
export interface TurnkeyFromEnvOptions {
  expectedChainId?: number;
  signingPolicy?: SigningPolicy;
}

/**
 * Rewrite recognizable Turnkey API failures into actionable errors.
 *
 * Detection is intentionally string/shape-based (the SDK error classes live
 * in the optional peer, which may not be installed at type-check time).
 * Unrecognized errors pass through untouched.
 */
function mapTurnkeyError(op: string, error: unknown): unknown {
  const message = error instanceof Error ? error.message : String(error);
  const status = (error as { status?: unknown } | null)?.status;
  if (/quota/i.test(message)) {
    return new Error(
      `Turnkey signature quota exhausted during ${op} (free tier: 25 billed signatures/month; pay-as-you-go $0.10/signature) — check the Turnkey billing dashboard.`,
      { cause: error },
    );
  }
  if (status === 429 || /rate.?limit/i.test(message)) {
    return new Error(
      `Turnkey rate limit hit during ${op} (free tier allows 1 request/second) — pace calls or upgrade the plan.`,
      { cause: error },
    );
  }
  if (/policy/i.test(message) && /denied|reject/i.test(message)) {
    return new Error(
      `Turnkey server-side policy denied ${op} — verify the API user has an explicit ALLOW policy covering this operation (non-root users are default-deny).`,
      { cause: error },
    );
  }
  return error;
}

/**
 * Wallet provider backed by Turnkey's remote enclave signing service.
 *
 * A pure signer: implements the three `sign*` methods (capabilities derive
 * automatically) and inherits the default `LocalExecutor` path, so ERC-8004
 * / ERC-8183 writes, x402 payments (via `X402Signer`) and MegaFuel
 * sponsorship all work without Turnkey-specific wiring.
 *
 * Construction is cheap and offline — the Turnkey packages load and the
 * signing account is built lazily on the first sign call.
 *
 * ```ts
 * const wallet = TurnkeyWalletProvider.fromEnv({ expectedChainId: 97 });
 * const sig = await wallet.signMessage("hello"); // 1 billed signature
 * ```
 */
export class TurnkeyWalletProvider extends WalletProvider {
  static override readonly kind: string = "turnkey";

  // Arbitrary mechanical contract calls via LocalExecutor; sponsored
  // broadcast via the MegaFuel paymaster (gasPrice=0 legacy signing verified
  // against the enclave, probe 2026-07-27). sign.* derive automatically
  // since all three sign methods below are overridden.
  protected override readonly extraCapabilities: ReadonlySet<string> = new Set([
    CALLS_ARBITRARY,
    PAYMASTER_SPONSOR,
  ]);

  readonly #address: `0x${string}`;
  readonly #organizationId: string;
  readonly #apiPublicKey: string;
  readonly #apiPrivateKey: string;
  readonly #apiBaseUrl: string;
  readonly #expectedChainId: number | undefined;
  readonly #signingPolicy: SigningPolicy;

  #accountPromise: Promise<LocalAccount> | null = null;

  constructor(opts: TurnkeyWalletProviderOptions) {
    super();
    for (const [key, value] of [
      ["organizationId", opts.organizationId],
      ["signWith", opts.signWith],
      ["apiPublicKey", opts.apiPublicKey],
      ["apiPrivateKey", opts.apiPrivateKey],
    ] as const) {
      if (!value) {
        throw new Error(`TurnkeyWalletProvider: '${key}' is required`);
      }
    }
    if (!ADDRESS_RE.test(opts.signWith)) {
      throw new Error(
        `TURNKEY_SIGN_WITH must be the wallet account's Ethereum address (0x + 40 hex chars), not a Turnkey wallet id or private-key id — copy the address from the Turnkey dashboard wallet-account view. Got: '${opts.signWith}'`,
      );
    }
    this.#address = getAddress(opts.signWith);
    this.#organizationId = opts.organizationId;
    this.#apiPublicKey = opts.apiPublicKey;
    this.#apiPrivateKey = opts.apiPrivateKey;
    this.#apiBaseUrl = opts.apiBaseUrl || TURNKEY_API_BASE_URL_DEFAULT;
    this.#expectedChainId = opts.expectedChainId;
    this.#signingPolicy = opts.signingPolicy ?? SigningPolicy.strictDefault();
  }

  /**
   * Build a provider from the `TURNKEY_*` environment variables:
   * `TURNKEY_API_PUBLIC_KEY`, `TURNKEY_API_PRIVATE_KEY`, `TURNKEY_ORG_ID`,
   * `TURNKEY_SIGN_WITH` (required) and `TURNKEY_API_BASE_URL` (optional).
   */
  static fromEnv(opts: TurnkeyFromEnvOptions = {}): TurnkeyWalletProvider {
    const values = {
      TURNKEY_API_PUBLIC_KEY: getEnv("TURNKEY_API_PUBLIC_KEY"),
      TURNKEY_API_PRIVATE_KEY: getEnv("TURNKEY_API_PRIVATE_KEY"),
      TURNKEY_ORG_ID: getEnv("TURNKEY_ORG_ID"),
      TURNKEY_SIGN_WITH: getEnv("TURNKEY_SIGN_WITH"),
    };
    const missing = Object.entries(values)
      .filter(([, v]) => !v)
      .map(([k]) => k);
    if (missing.length > 0) {
      throw new Error(
        `TurnkeyWalletProvider.fromEnv: missing required env vars: ${missing.join(", ")}. The values come from the Turnkey dashboard (API keys, organization settings, wallet account address).`,
      );
    }
    return new TurnkeyWalletProvider({
      apiPublicKey: values.TURNKEY_API_PUBLIC_KEY as string,
      apiPrivateKey: values.TURNKEY_API_PRIVATE_KEY as string,
      organizationId: values.TURNKEY_ORG_ID as string,
      signWith: values.TURNKEY_SIGN_WITH as string,
      apiBaseUrl: getEnv("TURNKEY_API_BASE_URL"),
      ...(opts.expectedChainId !== undefined
        ? { expectedChainId: opts.expectedChainId }
        : {}),
      ...(opts.signingPolicy ? { signingPolicy: opts.signingPolicy } : {}),
    });
  }

  get address(): `0x${string}` {
    return this.#address;
  }

  override get keyLocation(): string {
    return `remote:turnkey (${this.#apiBaseUrl}; key held in AWS Nitro enclave, never leaves)`;
  }

  /** The SigningPolicy currently enforcing {@link signTypedData} calls. */
  get signingPolicy(): SigningPolicy {
    return this.#signingPolicy;
  }

  /** The chain id this provider is pinned to, if any. */
  get expectedChainId(): number | undefined {
    return this.#expectedChainId;
  }

  /**
   * Lazily build (and cache) the Turnkey-backed viem account. Concurrent
   * first callers share one in-flight promise; a rejection clears the cache
   * so a transient API failure is retryable on the next call.
   */
  #account(): Promise<LocalAccount> {
    if (!this.#accountPromise) {
      this.#accountPromise = this.#initAccount().then(
        (account) => account,
        (error: unknown) => {
          this.#accountPromise = null;
          throw error;
        },
      );
    }
    return this.#accountPromise;
  }

  async #initAccount(): Promise<LocalAccount> {
    const { sdkServer, viem } = await loadTurnkeySdk();
    const turnkey = new sdkServer.Turnkey({
      apiBaseUrl: this.#apiBaseUrl,
      apiPublicKey: this.#apiPublicKey,
      apiPrivateKey: this.#apiPrivateKey,
      defaultOrganizationId: this.#organizationId,
    });
    const account = await viem.createAccount({
      client: turnkey.apiClient(),
      organizationId: this.#organizationId,
      signWith: this.#address,
    });
    const actual = getAddress(account.address);
    if (actual !== this.#address) {
      throw new WalletIdentityMismatch({
        expected: this.#address,
        actual,
      });
    }
    return account;
  }

  /** Run a billable account call with Turnkey-aware error rewriting. */
  async #vendor<T>(op: string, fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (error) {
      throw mapTurnkeyError(op, error);
    }
  }

  /**
   * Sign a message using EIP-191 personal sign.
   *
   * The digest is hashed locally and blind-signed by the enclave
   * (`HEXADECIMAL` + `NO_OP`), so Turnkey's server-side policies cannot see
   * the message content — content-level control lives in this SDK's
   * client-side policy layer.
   */
  override async signMessage(message: string): Promise<SignatureResult> {
    const account = await this.#account();
    const signature = await this.#vendor("signMessage", () =>
      account.signMessage({ message }),
    );
    const { r, s, v } = parseSignature(signature);
    return {
      messageHash: hashMessage(message),
      r,
      s,
      v: v as bigint,
      signature,
    };
  }

  /**
   * Sign EIP-712 typed data after passing the configured
   * {@link SigningPolicy} — the policy check runs BEFORE the billable API
   * call, so a refusal costs no quota.
   *
   * The full typed-data document goes to the enclave
   * (`PAYLOAD_ENCODING_EIP712`), where server-side policies can filter on
   * `eth.eip_712.domain` / `primary_type` / `message`.
   */
  override async signTypedData(
    domain: TypedDataDomain,
    types: Record<string, { name: string; type: string }[]>,
    message: Record<string, unknown>,
  ): Promise<SignatureResult> {
    check(
      this.#signingPolicy,
      domain as Record<string, unknown>,
      types,
      message,
    );
    // Drop a caller-supplied EIP712Domain for hashing parity with the other
    // providers (identical signatures whether or not it was supplied) …
    const messageTypes = Object.fromEntries(
      Object.entries(types).filter(([k]) => k !== "EIP712Domain"),
    );
    const primaryType = inferPrimaryType(types);
    // … then inject the full domain type into the enclave payload:
    // @turnkey/viem (≤0.14.34) serializes the domain as {} when the types
    // object lacks an explicit EIP712Domain entry — the signature would
    // bind an empty domain. See the module docstring.
    const fullTypes = {
      EIP712Domain: getTypesForEIP712Domain({ domain }),
      ...messageTypes,
    };
    const account = await this.#account();
    const signature = await this.#vendor("signTypedData", () =>
      account.signTypedData({
        domain,
        types: fullTypes,
        primaryType,
        message,
      } as Parameters<LocalAccount["signTypedData"]>[0]),
    );
    const { r, s, v } = parseSignature(signature);
    const messageHash = hashTypedData({
      domain,
      types: messageTypes,
      primaryType,
      message,
    } as Parameters<typeof hashTypedData>[0]);
    return { messageHash, r, s, v: v as bigint, signature };
  }

  /**
   * Sign a transaction (legacy or EIP-1559 — the serializer infers the type
   * from the fee fields; both shapes are enclave-verified).
   *
   * When the provider was constructed with `expectedChainId`, a mismatching
   * `tx.chainId` is refused before the billable API call.
   */
  override async signTransaction(tx: SignableTransaction): Promise<SignedTx> {
    if (
      this.#expectedChainId !== undefined &&
      tx.chainId !== this.#expectedChainId
    ) {
      throw new Error(
        `Refusing to sign for chainId=${tx.chainId}: this Turnkey provider is pinned to chainId=${this.#expectedChainId} (every Turnkey signature is billed, so the mismatch fails closed before the API call).`,
      );
    }
    const account = await this.#account();
    const rawTransaction = await this.#vendor("signTransaction", () =>
      account.signTransaction(tx as unknown as TransactionSerializable),
    );
    const parsed = parseTransaction(rawTransaction);
    const v =
      parsed.v ??
      (parsed.yParity !== undefined ? BigInt(parsed.yParity) + 27n : 0n);
    return {
      rawTransaction,
      hash: keccak256(rawTransaction),
      r: parsed.r as `0x${string}`,
      s: parsed.s as `0x${string}`,
      v,
    };
  }
}
