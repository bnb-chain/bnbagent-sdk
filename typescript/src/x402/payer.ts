/**
 * X402Payer — the delegated-payment seam.
 *
 * Where `X402Signer` exposes the *signing primitive* ("sign these bytes"),
 * `X402Payer` is the seam one level up: "handle this payment". Wallets
 * whose payment machinery lives outside the SDK (an external CLI, a
 * custodial API) plug in here as a whole, instead of pretending to be byte
 * signers.
 *
 * Port of `python/bnbagent/x402/payer.py`. TWAK integration itself
 * (`python/bnbagent/x402/twak.py`) is out of scope for this port — only the
 * data types and the CLI-shaped parse helpers are ported here.
 */

import { resolveB402Asset } from "./assets.js";
import type { ExpectedB402Asset } from "./assets.js";

/**
 * One payable route from a 402 challenge (a quote `accepts` entry).
 *
 * Field-verified against `twak x402 quote --json` output: the CLI
 * pre-filters routes its client cannot pay, so every option here is
 * nominally payable by the backing wallet.
 */
export interface X402PaymentOption {
  /** CAIP-2 network identifier, e.g. `"eip155:56"`. */
  network: string;
  /** x402 scheme, e.g. `"exact"`. */
  scheme: string;
  /**
   * Token contract address. For EIP-3009 routes this address is also the
   * EIP-712 domain `verifyingContract`.
   */
  asset: string;
  tokenName?: string;
  /** Price in atomic token units (parsed from the CLI's decimal string). */
  amount: bigint;
  payTo: string;
  /** e.g. `"eip3009"` or `"permit2"`. */
  transferMethod?: string;
  /** The challenge's claimed payment-validity window, in seconds. */
  maxTimeoutSeconds: number | null;
  preferred: boolean;
  requiresApproval: boolean;
  description?: string;
}

/** Throws if `entry[field]` is missing/null/undefined; mirrors Python's KeyError. */
function requireField(entry: Record<string, unknown>, field: string): unknown {
  const value = entry[field];
  if (value === undefined || value === null) {
    throw new Error(`x402 payment option missing required field: ${field}`);
  }
  return value;
}

/**
 * Map a camelCase CLI `accepts` entry; missing optionals → undefined/false.
 *
 * `network`, `asset`, `amount`, and `payTo` are required (mirrors Python's
 * `entry["..."]` KeyError behavior) — a missing value throws immediately
 * rather than silently coercing to the string `"undefined"`.
 */
export function paymentOptionFromCli(
  entry: Record<string, unknown>,
): X402PaymentOption {
  const timeout = entry.maxTimeoutSeconds;
  return {
    network: String(requireField(entry, "network")),
    scheme: String(entry.scheme ?? "exact"),
    asset: String(requireField(entry, "asset")),
    tokenName: entry.tokenName as string | undefined,
    amount: BigInt(requireField(entry, "amount") as bigint | number | string),
    payTo: String(requireField(entry, "payTo")),
    transferMethod: entry.transferMethod as string | undefined,
    maxTimeoutSeconds:
      timeout !== undefined && timeout !== null ? Number(timeout) : null,
    preferred: Boolean(entry.preferred ?? false),
    requiresApproval: Boolean(entry.requiresApproval ?? false),
    description: entry.description as string | undefined,
  };
}

/** Resolve the option's exact `network + asset` through the catalog. */
export function expectedAssetFromPaymentOption(
  option: X402PaymentOption,
): ExpectedB402Asset {
  return resolveB402Asset(option.network, option.asset);
}

/**
 * A parsed 402 challenge: the resource plus its payable routes.
 *
 * `accepts` may be empty — the quoting client filters out routes on chains
 * it cannot pay.
 */
export interface X402Quote {
  url: string;
  description?: string;
  mimeType?: string;
  accepts: readonly X402PaymentOption[];
  summary?: string;
  /** The raw parsed CLI/HTTP quote JSON, for fields not modeled here. */
  raw: Record<string, unknown>;
}

export function quoteFromCli(data: Record<string, unknown>): X402Quote {
  const resource = (data.resource as Record<string, unknown> | undefined) ?? {};
  const accepts = (data.accepts as Record<string, unknown>[] | undefined) ?? [];
  return {
    url: String(resource.url ?? ""),
    description: resource.description as string | undefined,
    mimeType: resource.mimeType as string | undefined,
    accepts: accepts.map(paymentOptionFromCli),
    summary: data.summary as string | undefined,
    raw: data,
  };
}

/**
 * Outcome of a delegated x402 payment.
 *
 * `response` is the paid endpoint's response body **verbatim**. The
 * payment metadata fields are optional by design — delegated payers fill
 * `amount`/`asset`/`network`/`payTo` from the **quoted** option they paid
 * against, not from settlement. `transaction` is best-effort.
 */
export interface X402PaymentResult {
  success: boolean;
  /** The endpoint's response body, verbatim. */
  response: unknown;
  amount?: bigint;
  asset?: string;
  network?: string;
  payTo?: string;
  transaction?: string;
}

export type X402TransferMethod = "eip3009" | "permit2-exact";

interface ExpectedX402RouteBase {
  readonly x402Version: 2;
  readonly scheme: "exact";
  readonly network: `eip155:${number}`;
  readonly asset: string;
  readonly amount: bigint;
  readonly payTo: string;
  readonly maxTimeoutSeconds: number;
}

export type ExpectedX402Route =
  | (ExpectedX402RouteBase & {
      readonly transferMethod: "eip3009";
    })
  | (ExpectedX402RouteBase & {
      readonly transferMethod: "permit2-exact";
      readonly name: string;
      readonly version: string;
      /** Expected B402 proxy from the caller's trusted capability snapshot. */
      readonly spenderAddress: string;
      /** Explicit trust root; the challenge cannot add to this list. */
      readonly trustedSpenders: readonly string[];
    });

export interface X402ExactPaymentResult extends X402PaymentResult {
  readonly success: true;
  readonly amount: bigint;
  readonly asset: string;
  readonly network: `eip155:${number}`;
  readonly payTo: string;
  readonly transferMethod: X402TransferMethod;
  readonly spenderAddress?: string;
}

export interface X402ExactRequestOptions {
  readonly expectedRoute: ExpectedX402Route;
  readonly maxPayment: bigint;
  readonly method?: string;
  readonly body?: string;
}

/**
 * Structural contract for delegated x402 payment backends.
 *
 * Two methods, aligned with both the CLI verbs and x402 semantics —
 * deliberately not `pay()`: a cache hit on `request` may not pay at all.
 * Implementations may accept extra options.
 */
export interface X402Payer {
  /** Methods this implementation can bind atomically in `requestExact`. */
  readonly exactTransferMethods?: readonly X402TransferMethod[];

  /** Fetch the 402 challenge for `url` without paying. */
  quote(
    url: string,
    opts?: { method?: string; body?: string },
  ): Promise<X402Quote>;

  /**
   * Fetch `url`, completing an x402 payment up to `maxPayment` atomic units
   * if challenged.
   */
  request(
    url: string,
    opts: { maxPayment: bigint; method?: string; body?: string },
  ): Promise<X402PaymentResult>;

  /**
   * Fetch, validate, sign, and retry one challenge without re-fetching or
   * selecting a route outside the caller-supplied exact binding.
   */
  requestExact?(
    url: string,
    opts: X402ExactRequestOptions,
  ): Promise<X402ExactPaymentResult>;
}
