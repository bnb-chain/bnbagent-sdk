/**
 * Read-only viem client construction.
 *
 * Kept intentionally minimal: callers that only read state (contract views,
 * event logs, nonce/gas lookups) don't need a `chain` object — that's only
 * needed for wallet-side formatting/signing niceties viem's own actions
 * provide, which this SDK's {@link WalletProvider}s don't use.
 */

import { http, type PublicClient, createPublicClient } from "viem";

/** Default BSC testnet RPC endpoint, used when no `rpcUrl` is given. */
export const DEFAULT_BSC_TESTNET_RPC_URL =
  "https://data-seed-prebsc-2-s2.binance.org:8545";

/**
 * Build a read-only viem `PublicClient` for `rpcUrl`.
 *
 * An empty string (or omitted argument) resolves to
 * {@link DEFAULT_BSC_TESTNET_RPC_URL}.
 */
export function createPublicClientFor(rpcUrl?: string): PublicClient {
  const url =
    rpcUrl && rpcUrl.length > 0 ? rpcUrl : DEFAULT_BSC_TESTNET_RPC_URL;
  return createPublicClient({ transport: http(url) });
}
