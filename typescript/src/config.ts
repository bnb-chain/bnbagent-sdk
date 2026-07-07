/**
 * Top-level SDK configuration.
 *
 * Exports:
 *
 * - {@link NetworkConfig} — per-network defaults (RPC, paymaster, contract
 *   addresses for every module that uses on-chain state).
 * - {@link resolveNetwork} — looks up a preset by name, with an optional
 *   `RPC_URL` env override. **Module-specific contract overrides live in
 *   their own module configs**, not here.
 *
 * Env var surface
 * ---------------
 * `resolveNetwork` is intentionally narrow: it only reads `RPC_URL`.
 * Module-scoped env vars are owned by the corresponding module config. The
 * project-root `.env.example` is the authoritative reference.
 */

import { getEnv } from "./core/envUtil.js";

/**
 * Per-network configuration with ALL protocol addresses.
 *
 * ERC-8183 is a three-contract stack: AgenticCommerce kernel (escrow),
 * EvaluatorRouter (routing + hook), and OptimisticPolicy (silence-approves,
 * vote-rejects). Payment token is NOT configured here — it is immutable on
 * the Commerce kernel and read at runtime via `ERC8183Client.paymentToken`.
 */
export interface NetworkConfig {
  name: string;
  chainId: number;
  rpcUrl: string;
  paymasterUrl?: string;
  usePaymaster: boolean;
  /** ERC-8004 Identity Registry */
  registryContract: string;
  /** ERC-8183 stack */
  commerceContract: string;
  routerContract: string;
  policyContract: string;
}

export const NETWORKS: Record<string, NetworkConfig> = {
  "bsc-testnet": {
    name: "bsc-testnet",
    chainId: 97,
    rpcUrl: "https://data-seed-prebsc-2-s2.binance.org:8545",
    paymasterUrl: "https://bsc-megafuel-testnet.nodereal.io",
    usePaymaster: true,
    registryContract: "0x8004A818BFB912233c491871b3d84c89A494BD9e",
    commerceContract: "0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de",
    routerContract: "0xd7d36d66d2f1b608a0f943f722d27e3744f66f25",
    policyContract: "0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6",
  },
  "bsc-mainnet": {
    name: "bsc-mainnet",
    chainId: 56,
    rpcUrl: "https://bsc-dataseed.binance.org",
    paymasterUrl: "https://bsc-megafuel.nodereal.io/",
    usePaymaster: true,
    registryContract: "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    commerceContract: "0xea4daa3100a767e86fded867729ae7446476eba6",
    routerContract: "0x51895229e12f9876011789b04f8698af06ccd6da",
    policyContract: "0x9c01845705b3078aa2e8cff7520a6376fd766de5",
  },
};

/**
 * Resolve a network preset to a concrete `NetworkConfig`.
 *
 * Accepts either a preset name (`"bsc-testnet"` / `"bsc-mainnet"`) or a
 * concrete `NetworkConfig` instance:
 *
 * - **String** → look up the preset; apply the RPC env override if set.
 *   Module-scoped contract-address envs are NOT read here — they belong to
 *   each module's own config loader.
 * - **NetworkConfig** → returned as-is; env vars are never applied (fully
 *   explicit control is the point of passing an object).
 *
 * RPC override precedence (a process that touches several networks needs
 * per-network pins — a single shared URL would silently apply to the wrong
 * chain):
 *
 * 1. `RPC_URL_<NETWORK>` — per-network, e.g. `RPC_URL_BSC_TESTNET` /
 *    `RPC_URL_BSC_MAINNET` (preset name uppercased, `-` → `_`).
 * 2. `RPC_URL` — global, network-agnostic.
 * 3. The preset default.
 */
export function resolveNetwork(
  network: string | NetworkConfig = "bsc-testnet",
): NetworkConfig {
  if (typeof network !== "string") {
    return network;
  }

  const nc = NETWORKS[network];
  if (nc === undefined) {
    throw new Error(`Unknown network: ${network}`);
  }

  const perNetworkKey = `RPC_URL_${nc.name.toUpperCase().replace(/-/g, "_")}`;
  const rpcOverride = getEnv(perNetworkKey) ?? getEnv("RPC_URL");
  if (rpcOverride) {
    const usePaymaster = !rpcOverride.startsWith("http://localhost");
    return {
      ...nc,
      rpcUrl: rpcOverride,
      usePaymaster,
    };
  }
  return nc;
}
