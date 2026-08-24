/**
 * `AltanaX402Payer` — session-key x402 payments from an Altana wallet.
 *
 * Delegated payer (the {@link X402Payer} seam): the Altana SDK signs the
 * payment (`signX402Payment` → ERC-7739-nested ERC-1271 envelope, verified
 * on-chain by the approved checker), while this class owns the HTTP 402
 * loop AND the policy layer the SDK deliberately does not have —
 * per-request `maxPayment`, an optional cumulative `SessionBudgetTracker`,
 * and route selection pinned to the provider's chain. `fetchWithX402` is
 * NOT used: it re-fetches the challenge internally and accepts whatever
 * amount it sees, which would reopen the check-then-pay gap this SDK
 * closes everywhere else. Here the requirement that gets signed is the
 * same object this class validated.
 *
 * On-chain spend bound: session spend caps do NOT apply to Permit2 pulls
 * (the checker gate is independent of spend permissions — Altana docs), so
 * the wallet→Permit2 allowance is the only on-chain ceiling. Keep it
 * bounded via `AltanaWalletProvider.setPermit2Allowance` and treat the
 * caps here as the in-process guard on top.
 *
 * One-time setup (admin, once per session): approve the checker
 * (`approveX402SignatureChecker`) and fund a bounded allowance
 * (`setPermit2Allowance`). Payments then need only the session.
 *
 * B402-merchant wire compatibility (field-verified against CMC, 2026-08-18):
 * the signed requirement carries the challenge's top-level `x402Version` and
 * `resource` (omitting either gets the envelope rejected before signature
 * checks), the paid retry sends the envelope under BOTH `X-PAYMENT` and
 * `PAYMENT-SIGNATURE` (part of the b402 merchant population reads only the
 * latter), and pre-0.7.0 `@altananetwork/sdk` envelopes are normalized
 * JSON-level ({@link normalizeX402PaymentHeader}) without touching the
 * signed bytes. The B402 facilitator verifies the ERC-1271 session
 * signature on the permit2 rails only (eip3009 still requires a 65-byte
 * EOA signature there).
 */

import { SessionBudgetTracker } from "../../x402/budget.js";
import {
  X402AmountExceededError,
  X402NoPayableRouteError,
  X402RecipientMismatchError,
} from "../../x402/errors.js";
import type {
  X402Payer,
  X402PaymentOption,
  X402PaymentResult,
  X402Quote,
} from "../../x402/payer.js";
import type { AltanaWalletProvider } from "./provider.js";

/** The x402 payment header (spec name, case-insensitive on the wire). */
export const X_PAYMENT_HEADER = "X-PAYMENT";
/** b402/Bazaar merchant alias for the payment header; sent alongside
 * `X-PAYMENT` with the identical value (part of that merchant population
 * reads only this name). */
export const PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE";
/** Settlement metadata header on the paid response (base64 JSON). */
export const X_PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE";

/** Known non-CAIP network aliases seen in B402/x402 challenges. */
const NETWORK_ALIAS_CHAIN_IDS: Record<string, number> = {
  bsc: 56,
  bnb: 56,
  "bsc-testnet": 97,
};

/** `"eip155:56"` → 56; known aliases; anything else → null. */
export function chainIdFromX402Network(network: string): number | null {
  const caip = /^eip155:(\d+)$/.exec(network.trim());
  if (caip) {
    return Number(caip[1]);
  }
  return NETWORK_ALIAS_CHAIN_IDS[network.trim().toLowerCase()] ?? null;
}

/**
 * One challenge route plus its raw `accepts[]` entry (what actually gets
 * signed — `signX402Payment` receives the entry verbatim so the SDK owns
 * the rail-specific typed-data construction).
 */
interface ParsedRoute {
  option: X402PaymentOption;
  raw: Record<string, unknown>;
}

/**
 * Parse an HTTP 402 challenge body. Field names follow the x402 wire
 * shape (`maxAmountRequired`); the SDK-normalized `amount` spelling is
 * accepted too. Entries missing an amount/asset/payTo are dropped rather
 * than crashing the whole challenge.
 */
export function parseX402Challenge(
  url: string,
  body: Record<string, unknown>,
): { quote: X402Quote; routes: ParsedRoute[] } {
  const rawAccepts = Array.isArray(body.accepts)
    ? (body.accepts as Record<string, unknown>[])
    : [];
  const routes: ParsedRoute[] = [];
  for (const entry of rawAccepts) {
    const amount = entry.maxAmountRequired ?? entry.amount;
    const asset = entry.asset;
    const payTo = entry.payTo;
    const network = entry.network;
    if (
      amount === undefined ||
      typeof asset !== "string" ||
      typeof payTo !== "string" ||
      typeof network !== "string"
    ) {
      continue;
    }
    const timeout = entry.maxTimeoutSeconds;
    routes.push({
      raw: entry,
      option: {
        network,
        scheme: String(entry.scheme ?? "exact"),
        asset,
        tokenName: (entry.extra as { name?: string } | undefined)?.name,
        amount: BigInt(amount as string | number | bigint),
        payTo,
        // Real B402 wire carries the rail in extra.assetTransferMethod
        // ("permit2-exact" | "eip3009"); the top-level spelling is legacy.
        transferMethod: (entry.transferMethod ??
          (entry.extra as { assetTransferMethod?: string } | undefined)
            ?.assetTransferMethod) as string | undefined,
        maxTimeoutSeconds:
          timeout !== undefined && timeout !== null ? Number(timeout) : null,
        preferred: Boolean(entry.preferred ?? false),
        requiresApproval: Boolean(entry.requiresApproval ?? false),
        description: entry.description as string | undefined,
      },
    });
  }
  return {
    quote: {
      url,
      description: (body.error ?? body.description) as string | undefined,
      accepts: routes.map((r) => r.option),
      raw: body,
    },
    routes,
  };
}

/** Whether a route rides the permit2/B402 rail (the one live on BSC). */
function isPermit2Route(option: X402PaymentOption): boolean {
  const tag = `${option.scheme} ${option.transferMethod ?? ""}`.toLowerCase();
  return tag.includes("permit2");
}

/**
 * Pick the route to pay on `chainId`: permit2/B402 routes first (EIP-3009
 * needs an ERC-1271-aware token, which BSC's Binance-Peg USDC is not),
 * `preferred` breaking ties, challenge order otherwise.
 */
export function selectX402Route(
  routes: readonly ParsedRoute[],
  chainId: number,
  expectedAsset?: string,
): ParsedRoute | null {
  const candidates = routes.filter(
    (r) =>
      chainIdFromX402Network(r.option.network) === chainId &&
      (expectedAsset === undefined ||
        r.option.asset.toLowerCase() === expectedAsset.toLowerCase()),
  );
  const rank = (r: ParsedRoute) =>
    (isPermit2Route(r.option) ? 0 : 2) + (r.option.preferred ? 0 : 1);
  return [...candidates].sort((a, b) => rank(a) - rank(b))[0] ?? null;
}

/** Constructor options for {@link AltanaX402Payer}. */
export interface AltanaX402PayerOptions {
  /**
   * `{tokenAddress: totalBaseUnits}` cumulative cap across this payer's
   * lifetime (same semantics as `X402Signer`'s `sessionBudget`). Missing
   * token → no cumulative cap; `request`'s `maxPayment` still applies.
   */
  sessionBudget?: Record<string, bigint>;
  /**
   * Pin the payment token: only challenge routes whose `asset` equals
   * this address are considered, so no other token can leave the wallet
   * through this payer (same intent as `TwakX402Payer`'s
   * `expectedAsset`). No match on the chain → `X402NoPayableRouteError`.
   */
  expectedAsset?: string;
  /**
   * Pin the recipient: if the selected route's `payTo` differs from this
   * address the payer throws `X402RecipientMismatchError` before signing
   * (same semantics as `TwakX402Payer`'s `expectedPayTo`).
   */
  expectedPayTo?: string;
  /** Fetch implementation override (tests). Defaults to `globalThis.fetch`. */
  fetchImpl?: typeof fetch;
}

/** Best-effort `text` → parsed JSON, else the raw text. */
async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * Normalize a signed x402 payment header for the b402 merchant wire —
 * JSON/envelope-level only, the signature bytes are never touched.
 *
 * `@altananetwork/sdk` >= 0.7.0 already emits a compliant envelope, so this
 * is a strict no-op there (the original header string is returned
 * byte-identical). For older installs it back-fills the three field-verified
 * rejection causes (poc run log, altana-x402-poc):
 *
 * 1. missing top-level `resource` → injected from the challenge ("payment
 *    header resource is null" otherwise);
 * 2. missing top-level `x402Version` → injected from the challenge;
 * 3. permit2 dialect `payload.permit` + `payload.from` without
 *    `payload.permit2Authorization` → the b402 spelling is added alongside
 *    ("payment payload permit2 authorization or witness is null" otherwise).
 *
 * A header that does not decode as base64 JSON is returned verbatim rather
 * than corrupted.
 */
export function normalizeX402PaymentHeader(
  header: string,
  challenge: { x402Version?: unknown; resource?: unknown },
): string {
  let envelope: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(
      Buffer.from(header, "base64").toString("utf8"),
    );
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      return header;
    }
    envelope = parsed as Record<string, unknown>;
  } catch {
    return header;
  }
  let changed = false;
  if (envelope.resource == null && challenge.resource != null) {
    envelope.resource = challenge.resource;
    changed = true;
  }
  if (envelope.x402Version == null && challenge.x402Version != null) {
    envelope.x402Version = challenge.x402Version;
    changed = true;
  }
  const payload = envelope.payload;
  if (
    payload !== null &&
    typeof payload === "object" &&
    !Array.isArray(payload)
  ) {
    const p = payload as Record<string, unknown>;
    const permit = p.permit;
    if (
      p.permit2Authorization == null &&
      permit !== null &&
      typeof permit === "object" &&
      !Array.isArray(permit) &&
      typeof p.from === "string"
    ) {
      const pm = permit as Record<string, unknown>;
      p.permit2Authorization = {
        permitted: pm.permitted,
        from: p.from,
        spender: pm.spender,
        nonce: pm.nonce,
        deadline: pm.deadline,
        witness: pm.witness,
      };
      changed = true;
    }
  }
  if (!changed) {
    return header;
  }
  return Buffer.from(JSON.stringify(envelope), "utf8").toString("base64");
}

/** Decode the settlement header (base64 JSON) into a tx hash, best-effort. */
function transactionFromResponse(response: Response): string | undefined {
  const header = response.headers.get(X_PAYMENT_RESPONSE_HEADER);
  if (!header) {
    return undefined;
  }
  try {
    const decoded = JSON.parse(
      Buffer.from(header, "base64").toString("utf8"),
    ) as Record<string, unknown>;
    const tx = decoded.transaction ?? decoded.txHash ?? decoded.transactionHash;
    return typeof tx === "string" ? tx : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Delegated x402 payer for a session-mode {@link AltanaWalletProvider}
 * (see module docstring). Construct via `provider.makeX402Payer()`.
 */
export class AltanaX402Payer implements X402Payer {
  readonly #provider: AltanaWalletProvider;
  readonly #budget: SessionBudgetTracker;
  readonly #expectedAsset: string | undefined;
  readonly #expectedPayTo: string | undefined;
  readonly #fetch: typeof fetch;

  /** @internal Constructed by {@link AltanaWalletProvider.makeX402Payer}. */
  constructor(
    provider: AltanaWalletProvider,
    opts: AltanaX402PayerOptions = {},
  ) {
    this.#provider = provider;
    this.#budget = new SessionBudgetTracker(opts.sessionBudget);
    this.#expectedAsset = opts.expectedAsset;
    this.#expectedPayTo = opts.expectedPayTo;
    this.#fetch = opts.fetchImpl ?? globalThis.fetch;
  }

  /** Fetch the 402 challenge for `url` without paying (empty `accepts` when the endpoint answered non-402). */
  async quote(
    url: string,
    opts?: { method?: string; body?: string },
  ): Promise<X402Quote> {
    const response = await this.#fetch(url, {
      method: opts?.method ?? "GET",
      ...(opts?.body !== undefined ? { body: opts.body } : {}),
    });
    if (response.status !== 402) {
      // Not challenged: nothing to pay. Drain the body so the connection
      // is reusable, and surface the status for the caller.
      await response.text();
      return { url, accepts: [], raw: { status: response.status } };
    }
    const body = (await parseBody(response)) as Record<string, unknown>;
    if (typeof body !== "object" || body === null) {
      throw new Error(
        `x402 challenge from ${url} is not a JSON object (got ${typeof body})`,
      );
    }
    return parseX402Challenge(url, body).quote;
  }

  /**
   * Fetch `url`, paying the 402 challenge (once) if within `maxPayment`.
   *
   * The signed requirement is byte-identical to the one validated here —
   * no re-fetch between check and sign. A non-402 first answer returns
   * without paying (cache-hit semantics).
   */
  async request(
    url: string,
    opts: { maxPayment: bigint; method?: string; body?: string },
  ): Promise<X402PaymentResult> {
    const method = opts.method ?? "GET";
    const init: RequestInit = {
      method,
      ...(opts.body !== undefined ? { body: opts.body } : {}),
    };
    const first = await this.#fetch(url, init);
    if (first.status !== 402) {
      if (!first.ok) {
        throw new Error(
          `x402 request to ${url} failed before payment: HTTP ${first.status}`,
        );
      }
      return { success: true, response: await parseBody(first) };
    }

    const challengeBody = (await parseBody(first)) as Record<string, unknown>;
    const { routes } = parseX402Challenge(url, challengeBody);
    const chainId = await this.#provider._x402ChainId();
    const route = selectX402Route(routes, chainId, this.#expectedAsset);
    if (!route) {
      const seen =
        routes.map((r) => `${r.option.network}:${r.option.asset}`).join(", ") ||
        "<none>";
      throw new X402NoPayableRouteError(
        `no payable x402 route on chain ${chainId}${this.#expectedAsset ? ` for asset ${this.#expectedAsset}` : ""} for ${url} (challenge routes: ${seen})`,
      );
    }
    const { option } = route;
    if (
      this.#expectedPayTo !== undefined &&
      option.payTo.toLowerCase() !== this.#expectedPayTo.toLowerCase()
    ) {
      throw new X402RecipientMismatchError(
        `quoted payTo ${option.payTo} != expected ${this.#expectedPayTo}`,
      );
    }
    if (option.amount > opts.maxPayment) {
      throw new X402AmountExceededError(
        `x402 route asks ${option.amount} ${option.tokenName ?? option.asset}, above maxPayment ${opts.maxPayment}`,
      );
    }

    // The b402 merchant wire rejects envelopes missing the challenge's
    // top-level x402Version/resource, and @altananetwork/sdk only carries
    // them into the envelope when the signed requirement includes them.
    const x402Version = challengeBody.x402Version ?? 2;
    const resource = challengeBody.resource;
    const requirement: Record<string, unknown> = {
      ...route.raw,
      x402Version,
      ...(resource !== undefined && resource !== null ? { resource } : {}),
    };

    this.#budget.reserve(option.asset, option.amount);
    try {
      const { header: signedHeader } =
        await this.#provider._signX402Payment(requirement);
      const header = normalizeX402PaymentHeader(signedHeader, {
        x402Version,
        resource,
      });
      const paid = await this.#fetch(url, {
        ...init,
        headers: {
          [X_PAYMENT_HEADER]: header,
          [PAYMENT_SIGNATURE_HEADER]: header,
        },
      });
      if (!paid.ok) {
        throw new Error(
          `x402 payment for ${url} was signed but the paid retry failed: HTTP ${paid.status} (amount ${option.amount}, asset ${option.asset})`,
        );
      }
      const transaction = transactionFromResponse(paid);
      return {
        success: true,
        response: await parseBody(paid),
        amount: option.amount,
        asset: option.asset,
        network: option.network,
        payTo: option.payTo,
        ...(transaction ? { transaction } : {}),
      };
    } catch (error) {
      // The payment may not have settled; release the in-process
      // reservation and let the caller retry (settled-but-failed-HTTP is
      // observable on-chain via the bounded Permit2 allowance).
      this.#budget.rollback(option.asset, option.amount);
      throw error;
    }
  }
}
