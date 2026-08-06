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

/**
 * Structural contract for delegated x402 payment backends.
 *
 * Two methods, aligned with both the CLI verbs and x402 semantics —
 * deliberately not `pay()`: a cache hit on `request` may not pay at all.
 * Implementations may accept extra options.
 */
export interface X402Payer {
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
}
