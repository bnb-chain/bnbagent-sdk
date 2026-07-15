/**
 * Default session permissions for a bnbagent agent on an Altana wallet.
 *
 * Builds the call whitelist + spend caps an agent session needs to run the
 * SDK's protocol surface: the ERC-8004 registry, the ERC-8183 stack
 * (commerce / router / policy) and the payment token.
 *
 * The native spend entry is UNCONDITIONAL and load-bearing: an Altana
 * session pays its own relay-recovered gas out of the wallet, and that fee
 * counts against the session's spend permissions. A session granted only a
 * token cap reverts on-chain with `NoSpendPermissions` before it can do
 * anything (field-tested; see the integration plan's pitfall #1) — so
 * `defaultAgentPermissions` always includes a small native allowance even
 * when the caller doesn't ask for one.
 */

import { getAddress as toChecksumAddress } from "viem";
import { NETWORKS } from "../../config.js";
import { BNB_CHAIN_ADDRESSES } from "../../networks/addresses.js";
import type {
  AltanaCallPermission,
  AltanaSessionPermissions,
  AltanaSpendPermission,
} from "./types.js";

/**
 * Default native (BNB) allowance per day: 0.02 BNB — generous headroom for
 * relay gas recovery across a day of agent transactions, small enough to
 * bound the damage of a leaked session key.
 */
export const DEFAULT_NATIVE_GAS_ALLOWANCE_WEI = 20_000_000_000_000_000n;

/** The five contract targets an agent session is allowed to call. */
export interface AgentPermissionTargets {
  registry: `0x${string}`;
  commerce: `0x${string}`;
  router: `0x${string}`;
  policy: `0x${string}`;
  paymentToken: `0x${string}`;
}

/** A spend cap: `period` defaults to `"day"`. */
export interface SpendCap {
  limit: bigint;
  period?: AltanaSpendPermission["period"];
}

/** Options accepted by {@link defaultAgentPermissions}. */
export interface DefaultAgentPermissionsOpts {
  /** Chain the session will operate on (56 / 97 for the built-in presets). */
  chainId: number;
  /** Payment-token spend cap (the agent's working budget). */
  tokenSpend: SpendCap;
  /**
   * Native (gas) spend cap. Defaults to
   * {@link DEFAULT_NATIVE_GAS_ALLOWANCE_WEI} per day — never omitted (see
   * module docstring).
   */
  nativeSpend?: SpendCap;
  /**
   * Per-target address overrides. On a known chain each field overrides the
   * preset; on an unknown `chainId` ALL five must be provided.
   */
  addresses?: Partial<AgentPermissionTargets>;
  /** Extra call rules appended after the five protocol targets. */
  extraCalls?: readonly AltanaCallPermission[];
}

/** Resolve the preset targets for `chainId`, or `null` if unknown. */
function presetTargets(chainId: number): AgentPermissionTargets | null {
  const network = Object.values(NETWORKS).find((n) => n.chainId === chainId);
  const deployment = BNB_CHAIN_ADDRESSES[chainId];
  if (!network || !deployment) {
    return null;
  }
  return {
    registry: toChecksumAddress(network.registryContract),
    commerce: toChecksumAddress(network.commerceContract),
    router: toChecksumAddress(network.routerContract),
    policy: toChecksumAddress(network.policyContract),
    paymentToken: deployment.paymentToken,
  };
}

/**
 * Build the default {@link AltanaSessionPermissions} for a bnbagent agent.
 *
 * Calls whitelist (in order): registry, commerce, router, policy,
 * paymentToken, then any `extraCalls`. Spend caps: the payment-token cap,
 * then the unconditional native allowance.
 *
 * @throws {Error} when `chainId` has no built-in preset and `addresses`
 *   does not supply all five targets.
 */
export function defaultAgentPermissions(
  opts: DefaultAgentPermissionsOpts,
): AltanaSessionPermissions {
  const preset = presetTargets(opts.chainId);
  const overrides = opts.addresses ?? {};
  const merged: Partial<AgentPermissionTargets> = { ...preset, ...overrides };

  const missing = (
    ["registry", "commerce", "router", "policy", "paymentToken"] as const
  ).filter((field) => !merged[field]);
  if (missing.length > 0) {
    throw new Error(
      `defaultAgentPermissions: no built-in targets for chainId=${opts.chainId} and addresses is missing [${missing.join(", ")}]. On chains without a preset (known: 56, 97) pass all five addresses explicitly.`,
    );
  }
  const targets = merged as AgentPermissionTargets;

  const calls: AltanaCallPermission[] = [
    { to: targets.registry },
    { to: targets.commerce },
    { to: targets.router },
    { to: targets.policy },
    { to: targets.paymentToken },
    ...(opts.extraCalls ?? []),
  ];

  const spend: AltanaSpendPermission[] = [
    {
      limit: opts.tokenSpend.limit,
      period: opts.tokenSpend.period ?? "day",
      token: targets.paymentToken,
    },
    // Unconditional: the session's own relay gas fee counts as native
    // spend; without this entry every execute reverts NoSpendPermissions.
    {
      limit: opts.nativeSpend?.limit ?? DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
      period: opts.nativeSpend?.period ?? "day",
    },
  ];

  return { calls, spend };
}
