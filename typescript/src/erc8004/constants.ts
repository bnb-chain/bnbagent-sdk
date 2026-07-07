/**
 * ERC-8004 Identity Registry specific configuration.
 *
 * Env surface (module-scoped, `ERC8004_` prefix):
 *   `ERC8004_REGISTRY_ADDRESS` — override `registryContract`
 *
 * Port of `python/bnbagent/erc8004/constants.py`.
 */

import { type NetworkConfig, resolveNetwork } from "../config.js";
import { getEnv } from "../core/envUtil.js";
import { SDK_VERSION } from "../version.js";

export const ERC8004_ENV_PREFIX = "ERC8004_";

/** Resolved ERC-8004 network configuration. */
export interface Erc8004Config {
  name: string;
  chainId: number;
  rpcUrl: string;
  paymasterUrl: string;
  paymaster: boolean;
  registryContract: string;
}

/**
 * Get ERC-8004 network configuration lazily.
 *
 * Applies the `ERC8004_REGISTRY_ADDRESS` env override (when set) on top of
 * the resolved network preset. Global `RPC_URL` overrides are handled inside
 * `resolveNetwork`.
 */
export function getErc8004Config(
  network: string | NetworkConfig = "bsc-testnet",
): Erc8004Config {
  const nc = resolveNetwork(network);
  const registryOverride = getEnv(
    "REGISTRY_ADDRESS",
    undefined,
    ERC8004_ENV_PREFIX,
  );
  return {
    name: nc.name,
    chainId: nc.chainId,
    rpcUrl: nc.rpcUrl,
    paymasterUrl: nc.paymasterUrl || "",
    paymaster: nc.usePaymaster,
    registryContract: registryOverride || nc.registryContract,
  };
}

/** Metadata key used to tag every registration with the SDK that made it. */
export const BUILT_WITH_KEY = "built_with";

const BUILT_WITH_URL = "https://github.com/bnb-chain/bnbagent-sdk";

/** `https://github.com/bnb-chain/bnbagent-sdk#v<version>` for this SDK build. */
export function getBuiltWithValue(): string {
  return `${BUILT_WITH_URL}#v${SDK_VERSION}`;
}
