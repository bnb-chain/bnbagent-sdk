/**
 * Secondary confirmation that a relay-returned tx hash is really invisible.
 *
 * The MegaFuel relay failure mode behind `RelaySubmissionUnverifiedError` is
 * "relay accepted a hash the chain never saw". Before a caller reacts to that
 * (e.g. re-signing the same nonce as a self-paid transaction), it is worth a
 * few seconds to ask *other* RPC endpoints whether the transaction is truly
 * unseen — a flaky primary RPC must not trigger a same-nonce race against a
 * transaction that actually reached the mempool.
 *
 * A decisive result requires two distinct RPC origins. Anything weaker is
 * `"inconclusive"`, and callers fail closed instead of starting a second
 * broadcast from one configurable source's claim.
 */

import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
} from "../networks/addresses.js";
import { getEnv } from "./envUtil.js";

/** Outcome of probing a single RPC endpoint for a transaction. */
export type TxProbeResult = "seen" | "unseen" | "error";

/**
 * Aggregate verdict across all fallback endpoints:
 * - `"seen"` — two independent origins can see the transaction.
 * - `"confirmed-unseen"` — two independent origins report it absent.
 * - `"inconclusive"` — fewer than two agree, or any seen/unseen answers conflict.
 */
export type SecondaryConfirmation =
  | "confirmed-unseen"
  | "seen"
  | "inconclusive";

/** Injectable probe seam (tests replace the raw-fetch default). */
export type TxPresenceProbe = (
  rpcUrl: string,
  hash: `0x${string}`,
  timeoutMs: number,
) => Promise<TxProbeResult>;

/** Options for {@link confirmTxUnseen}. */
export interface RelayVerifierOpts {
  chainId: number;
  /** The RPC the primary client already polled — deduped from the fallbacks. */
  primaryRpcUrl?: string;
  /** Explicit fallback endpoints; overrides env and the built-in table. */
  fallbackRpcUrls?: string[] | null;
  /** Probe implementation; defaults to a raw `eth_getTransactionByHash` fetch. */
  probe?: TxPresenceProbe;
  /** Per-endpoint timeout (ms). */
  perEndpointTimeoutMs?: number;
}

/** Default per-endpoint probe timeout (ms). */
export const RELAY_VERIFIER_ENDPOINT_TIMEOUT_MS = 3_000;
const RELAY_VERIFIER_QUORUM = 2;

/**
 * Built-in public fallback endpoints per chain. Deliberately module-local:
 * these are last-resort defaults, overridable via
 * `BNBAGENT_FALLBACK_RPC_URLS` (comma-separated) or
 * {@link RelayVerifierOpts.fallbackRpcUrls}.
 */
const FALLBACK_RPC_URLS: Record<number, string[]> = {
  [BSC_TESTNET_CHAIN_ID]: [
    "https://bsc-testnet-rpc.publicnode.com",
    "https://data-seed-prebsc-1-s1.bnbchain.org:8545",
    "https://data-seed-prebsc-2-s1.bnbchain.org:8545",
  ],
  [BSC_MAINNET_CHAIN_ID]: [
    "https://bsc-rpc.publicnode.com",
    "https://bsc-dataseed1.bnbchain.org",
    "https://bsc-dataseed2.bnbchain.org",
  ],
};

/**
 * Resolve the fallback endpoints to probe: explicit opts, then the
 * `BNBAGENT_FALLBACK_RPC_URLS` env var, then the built-in per-chain table —
 * always minus the primary RPC (probing it again adds no information).
 */
export function resolveFallbackRpcUrls(
  opts: Pick<
    RelayVerifierOpts,
    "chainId" | "primaryRpcUrl" | "fallbackRpcUrls"
  >,
): string[] {
  let urls: string[];
  if (opts.fallbackRpcUrls && opts.fallbackRpcUrls.length > 0) {
    urls = opts.fallbackRpcUrls;
  } else {
    const raw = getEnv("BNBAGENT_FALLBACK_RPC_URLS");
    if (raw !== undefined && raw.trim() !== "") {
      urls = raw.split(",");
    } else {
      urls = FALLBACK_RPC_URLS[opts.chainId] ?? [];
    }
  }
  const primary = rpcOrigin(opts.primaryRpcUrl);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const entry of urls) {
    const trimmed = entry.trim();
    if (trimmed === "") {
      continue;
    }
    const key = rpcOrigin(trimmed);
    if (key === null) {
      continue;
    }
    if (key === primary || seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(trimmed);
  }
  return out;
}

/**
 * Ask the fallback RPC endpoints (in parallel) whether `hash` is visible.
 *
 * Never rejects. See {@link SecondaryConfirmation} for the quorum semantics.
 */
export async function confirmTxUnseen(
  hash: `0x${string}`,
  opts: RelayVerifierOpts,
): Promise<SecondaryConfirmation> {
  const urls = resolveFallbackRpcUrls(opts);
  if (urls.length === 0) {
    return "inconclusive";
  }
  const probe = opts.probe ?? fetchTxPresence;
  const timeoutMs =
    opts.perEndpointTimeoutMs ?? RELAY_VERIFIER_ENDPOINT_TIMEOUT_MS;
  const results = await Promise.all(
    urls.map(async (url): Promise<TxProbeResult> => {
      try {
        return await probe(url, hash, timeoutMs);
      } catch {
        return "error";
      }
    }),
  );
  const seenCount = results.filter((result) => result === "seen").length;
  const unseenCount = results.filter((result) => result === "unseen").length;
  if (seenCount > 0 && unseenCount > 0) {
    return "inconclusive";
  }
  if (seenCount >= RELAY_VERIFIER_QUORUM) {
    return "seen";
  }
  if (unseenCount >= RELAY_VERIFIER_QUORUM) {
    return "confirmed-unseen";
  }
  return "inconclusive";
}

/**
 * Default probe: a single raw `eth_getTransactionByHash` JSON-RPC call via
 * `fetch`, bounded by `timeoutMs`. A `null` result is an authoritative
 * "unseen"; transport failures, non-2xx responses, and RPC errors are all
 * `"error"` (that endpoint contributes no signal).
 */
async function fetchTxPresence(
  rpcUrl: string,
  hash: `0x${string}`,
  timeoutMs: number,
): Promise<TxProbeResult> {
  try {
    const response = await fetch(rpcUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "eth_getTransactionByHash",
        params: [hash],
      }),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) {
      return "error";
    }
    const payload = (await response.json()) as {
      result?: unknown;
      error?: unknown;
    };
    if (payload.error !== undefined && payload.error !== null) {
      return "error";
    }
    if (payload.result === null) {
      return "unseen";
    }
    if (
      typeof payload.result !== "object" ||
      payload.result === undefined ||
      payload.result === null
    ) {
      return "error";
    }
    const returnedHash = (payload.result as { hash?: unknown }).hash;
    return typeof returnedHash === "string" &&
      returnedHash.toLowerCase() === hash.toLowerCase()
      ? "seen"
      : "error";
  } catch {
    return "error";
  }
}

/** Independent-source key: scheme + host + port, never path or query. */
function rpcOrigin(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return parsed.origin.toLowerCase();
  } catch {
    return null;
  }
}
