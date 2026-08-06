/**
 * ERC-8183 protocol specific configuration.
 *
 * Env surface (module-scoped, `ERC8183_` prefix):
 *   ERC8183_COMMERCE_ADDRESS — override commerce_contract
 *   ERC8183_ROUTER_ADDRESS   — override router_contract
 *   ERC8183_POLICY_ADDRESS   — override policy_contract
 *
 * Port of `python/bnbagent/erc8183/constants.py`.
 */

import { type NetworkConfig, resolveNetwork } from "../config.js";
import { getEnv } from "../core/envUtil.js";

/** Env var prefix shared by every ERC-8183-scoped override. */
export const ERC8183_ENV_PREFIX = "ERC8183_";

/**
 * Get ERC-8183 network configuration lazily.
 *
 * Applies `ERC8183_*_ADDRESS` env overrides (when set) on top of the
 * resolved network preset. Global `RPC_URL` overrides are handled inside
 * `resolveNetwork`. Keys mirror the Python dict (`snake_case`) so tooling
 * that consumes this across both SDKs stays byte-identical.
 */
export function getErc8183Config(
  network: string | NetworkConfig = "bsc-testnet",
): Record<string, unknown> {
  const nc = resolveNetwork(network);
  return {
    name: nc.name,
    chain_id: nc.chainId,
    rpc_url: nc.rpcUrl,
    paymaster_url: nc.paymasterUrl ?? "",
    paymaster: nc.usePaymaster,
    commerce_contract:
      getEnv("COMMERCE_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.commerceContract,
    router_contract:
      getEnv("ROUTER_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.routerContract,
    policy_contract:
      getEnv("POLICY_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.policyContract,
  };
}

/**
 * Resolve `network` to a `NetworkConfig` with the ERC-8183 env overlay.
 *
 * String preset → the `ERC8183_*_ADDRESS` env overrides are applied on top
 * of the resolved preset (how a QA/staging contract stack is targeted
 * without code changes). Concrete `NetworkConfig` object → returned as
 * resolved; the caller takes full control and env overrides are ignored.
 */
export function resolveErc8183Network(
  network: string | NetworkConfig = "bsc-testnet",
): NetworkConfig {
  const nc = resolveNetwork(network);
  if (typeof network !== "string") {
    return nc;
  }
  return {
    ...nc,
    commerceContract:
      getEnv("COMMERCE_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.commerceContract,
    routerContract:
      getEnv("ROUTER_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.routerContract,
    policyContract:
      getEnv("POLICY_ADDRESS", undefined, ERC8183_ENV_PREFIX) ??
      nc.policyContract,
  };
}
