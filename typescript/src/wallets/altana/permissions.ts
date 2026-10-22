/**
 * Default session permissions for a bnbagent agent on an Altana wallet.
 *
 * Builds the call whitelist + spend caps an agent session needs to run the
 * SDK's protocol surface: the ERC-8004 registry, the ERC-8183 stack
 * (commerce / router / policy). Payment tokens are used only for independent
 * on-chain spend caps: session call permissions deliberately exclude ERC-20
 * approvals because an allowance survives session expiry and revocation.
 *
 * The native spend entry is UNCONDITIONAL and load-bearing: an Altana
 * session pays its own relay-recovered gas out of the wallet, and that fee
 * counts against the session's spend permissions. A session granted only a
 * token cap reverts on-chain with `NoSpendPermissions` before it can do
 * anything (field-tested; see the integration plan's pitfall #1) — so
 * `defaultAgentPermissions` always includes a small native allowance even
 * when the caller doesn't ask for one.
 */

import { getAddress as toChecksumAddress, toFunctionSelector } from "viem";
import { NETWORKS } from "../../config.js";
import { BNB_CHAIN_ADDRESSES } from "../../networks/addresses.js";
import { getAssetByAddress, listAssets } from "../../networks/assets.js";
import { SigningPolicy } from "../../signing/policy.js";
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

/** One catalog token's independent session-spend ceiling. */
export interface TokenSpendCap extends SpendCap {
  token: `0x${string}`;
}

/** Protocol roles compiled into least-privilege Altana call selectors. */
export type AgentAuthorizationRole =
  | "identity"
  | "buyer"
  | "seller"
  | "evaluator"
  | "voter";

/** Additional permissions must bind both the target and function selector. */
export interface StrictAgentCallPermission {
  to: `0x${string}`;
  signature: string;
}

/** Default full SDK surface, still selector-restricted on every target. */
export const DEFAULT_AGENT_AUTHORIZATION_ROLES: readonly AgentAuthorizationRole[] =
  ["identity", "buyer", "seller", "evaluator", "voter"];

const REGISTRY_SIGNATURES = {
  register: "register(string,(string,bytes)[])",
  setMetadata: "setMetadata(uint256,string,bytes)",
  setAgentURI: "setAgentURI(uint256,string)",
} as const;

/**
 * The identity Registry's ERC-721 approval and transfer functions. A session
 * allowed to call them could move the agent NFT or leave an approval that
 * outlives the session (SRC-1403), so `extraCalls` cannot add them.
 */
const REGISTRY_FORBIDDEN_SELECTORS: ReadonlySet<string> = new Set(
  [
    "approve(address,uint256)",
    "setApprovalForAll(address,bool)",
    "transferFrom(address,address,uint256)",
    "safeTransferFrom(address,address,uint256)",
    "safeTransferFrom(address,address,uint256,bytes)",
  ].map((signature) => toFunctionSelector(signature)),
);

/**
 * Porto's call-permission wildcards (`Call.anyTarget` / `Call.anySelector`):
 * the account matches them against every contract or every function, so
 * they would bypass the token and Registry checks below.
 */
const PORTO_ANY_TARGET = "0x3232323232323232323232323232323232323232";
const PORTO_ANY_SELECTOR = "0x32323232";

/**
 * The 4-byte selector the account receives for a call-permission signature,
 * or `null`. Porto's `Key.toRelay` sends any `0x`-prefixed signature to the
 * relay verbatim as the selector, so only an exact 4-byte hex value is one.
 */
function selectorOf(signature: string): string | null {
  if (signature.startsWith("0x")) {
    return /^0x[0-9a-fA-F]{8}$/u.test(signature)
      ? signature.toLowerCase()
      : null;
  }
  try {
    return toFunctionSelector(signature);
  } catch {
    return null;
  }
}

/** What a session's call permissions may not reach on one chain. */
interface CallPermissionBoundary {
  registry: string | undefined;
  forbiddenTokens: ReadonlySet<string>;
}

function callPermissionBoundary(
  chainId: number,
  registry: `0x${string}` | undefined,
  paymentToken: `0x${string}` | undefined,
): CallPermissionBoundary {
  const forbiddenTokens = new Set<string>(
    paymentToken ? [paymentToken.toLowerCase()] : [],
  );
  try {
    for (const asset of listAssets(chainId)) {
      forbiddenTokens.add(asset.address.toLowerCase());
    }
  } catch {
    // Unknown chains retain the legacy explicit paymentToken boundary.
  }
  return { registry: registry?.toLowerCase(), forbiddenTokens };
}

/**
 * Check one call permission against `boundary`; returns it with a
 * checksummed `to` and a hex selector lowercased. A missing `to` or
 * `signature` is refused because Porto turns it into its any-target or
 * any-function wildcard.
 */
function checkCallPermission(
  call: { to?: string; signature?: string },
  boundary: CallPermissionBoundary,
  label: string,
): StrictAgentCallPermission {
  if (!call.to || !call.signature) {
    throw new Error(`${label} must bind both to and signature`);
  }
  const to = toChecksumAddress(call.to);
  const selector = selectorOf(call.signature);
  if (
    to.toLowerCase() === PORTO_ANY_TARGET ||
    selector === PORTO_ANY_SELECTOR
  ) {
    throw new Error(
      `${label} cannot use Porto's any-target or any-function wildcard (got ${JSON.stringify({ to: call.to, signature: call.signature })}): it would also grant the payment tokens and the Registry's ERC-721 approval and transfer functions`,
    );
  }
  if (selector === null) {
    throw new Error(
      `${label} needs a function signature or an exact 4-byte selector (got ${JSON.stringify(call.signature)}): anything else reaches the Altana relay unchecked`,
    );
  }
  if (boundary.forbiddenTokens.has(to.toLowerCase())) {
    throw new Error(
      "session calls to catalog payment tokens are forbidden: provision Commerce " +
        "allowances outside the session so they cannot outlive session " +
        "expiry or revocation",
    );
  }
  if (
    to.toLowerCase() === boundary.registry &&
    REGISTRY_FORBIDDEN_SELECTORS.has(selector)
  ) {
    throw new Error(
      `session calls to the Registry's ERC-721 approval and transfer functions are forbidden (got ${JSON.stringify(call.signature)}): an agent NFT approval would outlive session expiry or revocation`,
    );
  }
  return {
    to,
    signature: call.signature.startsWith("0x") ? selector : call.signature,
  };
}

/**
 * Apply the `extraCalls` checks of {@link defaultAgentPermissions} to every
 * call of an arbitrary permission set for `chainId`: both `to` and
 * `signature` bound, no Porto wildcard, a parseable signature, no catalog
 * payment token target, and none of the Registry's ERC-721 approval and
 * transfer functions. Returns the permissions with the calls normalised.
 */
export function checkSessionPermissions(
  permissions: AltanaSessionPermissions,
  chainId: number,
  label = "session permissions",
): AltanaSessionPermissions {
  if (permissions.calls === undefined) {
    return permissions;
  }
  const preset = presetTargets(chainId);
  const boundary = callPermissionBoundary(
    chainId,
    preset?.registry,
    preset?.paymentToken,
  );
  return {
    ...permissions,
    calls: permissions.calls.map((call, i) =>
      checkCallPermission(call, boundary, `${label}.calls[${i}]`),
    ),
  };
}

const COMMERCE_SIGNATURES = {
  createJob: "createJob(address,address,uint256,string,address)",
  createJobWithToken:
    "createJobWithToken(address,address,uint256,string,address,address)",
  setProvider: "setProvider(uint256,address,bytes)",
  setBudget: "setBudget(uint256,uint256,bytes)",
  fund: "fund(uint256,uint256,bytes)",
  submit: "submit(uint256,bytes32,bytes)",
  complete: "complete(uint256,bytes32,bytes)",
  reject: "reject(uint256,bytes32,bytes)",
  claimRefund: "claimRefund(uint256)",
} as const;

const ROUTER_SIGNATURES = {
  registerJob: "registerJob(uint256,address)",
  settle: "settle(uint256,bytes)",
  markExpired: "markExpired(uint256)",
} as const;

const POLICY_SIGNATURES = {
  dispute: "dispute(uint256)",
  voteReject: "voteReject(uint256)",
} as const;

/** Options accepted by {@link defaultAgentPermissions}. */
export interface DefaultAgentPermissionsOpts {
  /** Chain the session will operate on (56 / 97 for the built-in presets). */
  chainId: number;
  /** Legacy default-payment-token cap. Mutually exclusive with `tokenSpends`. */
  tokenSpend?: SpendCap;
  /** Independent caps for checksummed tokens registered in the chain catalog. */
  tokenSpends?: readonly TokenSpendCap[];
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
  /** Protocol roles to grant. Defaults to the full SDK surface. */
  roles?: readonly AgentAuthorizationRole[];
  /** Extra rules; each must bind both target and selector. */
  extraCalls?: readonly StrictAgentCallPermission[];
}

export interface AgentAuthorizationPolicyOpts {
  /** Protocol roles allowed by the on-chain session compiler. */
  roles?: readonly AgentAuthorizationRole[];
  /** Typed-data policy used by the off-chain signer compiler. */
  signingPolicy?: SigningPolicy;
}

/**
 * One logical authorization policy with two enforcement compilers:
 * `toSigningPolicy()` for Studio/EVM typed-data signing and
 * `toAltanaPermissions()` for Altana's on-chain target+selector grants.
 */
export class AgentAuthorizationPolicy {
  readonly #roles: ReadonlySet<AgentAuthorizationRole>;
  readonly #signingPolicy: SigningPolicy;

  constructor(opts: AgentAuthorizationPolicyOpts = {}) {
    this.#roles = new Set(opts.roles ?? DEFAULT_AGENT_AUTHORIZATION_ROLES);
    this.#signingPolicy = opts.signingPolicy ?? SigningPolicy.strictDefault();
    if (this.#roles.size === 0) {
      throw new Error("AgentAuthorizationPolicy requires at least one role");
    }
  }

  get roles(): ReadonlySet<AgentAuthorizationRole> {
    return new Set(this.#roles);
  }

  toSigningPolicy(): SigningPolicy {
    return this.#signingPolicy;
  }

  toAltanaPermissions(
    opts: Omit<DefaultAgentPermissionsOpts, "roles">,
  ): AltanaSessionPermissions {
    return compileAltanaPermissions(opts, this.#roles);
  }
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
 * Calls whitelist (in order): selector-bound registry, commerce, router,
 * policy, then any `extraCalls`. The payment token is never a call target.
 * Spend caps: the payment-token cap, then the unconditional native allowance.
 *
 * @throws {Error} when `chainId` has no built-in preset and `addresses`
 *   does not supply all five targets.
 */
export function defaultAgentPermissions(
  opts: DefaultAgentPermissionsOpts,
): AltanaSessionPermissions {
  const { roles, ...permissionOpts } = opts;
  return new AgentAuthorizationPolicy({ roles }).toAltanaPermissions(
    permissionOpts,
  );
}

function compileAltanaPermissions(
  opts: Omit<DefaultAgentPermissionsOpts, "roles">,
  roles: ReadonlySet<AgentAuthorizationRole>,
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

  const calls: StrictAgentCallPermission[] = [];
  const allow = (to: `0x${string}`, signatures: readonly string[]) => {
    for (const signature of signatures) calls.push({ to, signature });
  };

  if (roles.has("identity")) {
    allow(targets.registry, Object.values(REGISTRY_SIGNATURES));
  }
  if (roles.has("buyer")) {
    allow(targets.commerce, [
      COMMERCE_SIGNATURES.createJob,
      COMMERCE_SIGNATURES.createJobWithToken,
      COMMERCE_SIGNATURES.setProvider,
      COMMERCE_SIGNATURES.setBudget,
      COMMERCE_SIGNATURES.fund,
      COMMERCE_SIGNATURES.reject,
      COMMERCE_SIGNATURES.claimRefund,
    ]);
    allow(targets.router, Object.values(ROUTER_SIGNATURES));
    allow(targets.policy, [POLICY_SIGNATURES.dispute]);
  }
  if (roles.has("seller")) {
    allow(targets.commerce, [COMMERCE_SIGNATURES.submit]);
  }
  if (roles.has("evaluator")) {
    allow(targets.commerce, [
      COMMERCE_SIGNATURES.complete,
      COMMERCE_SIGNATURES.reject,
    ]);
    allow(targets.router, [
      ROUTER_SIGNATURES.settle,
      ROUTER_SIGNATURES.markExpired,
    ]);
  }
  if (roles.has("voter")) {
    allow(targets.policy, [POLICY_SIGNATURES.voteReject]);
  }

  const boundary = callPermissionBoundary(
    opts.chainId,
    targets.registry,
    targets.paymentToken,
  );
  for (const extra of opts.extraCalls ?? []) {
    calls.push(
      checkCallPermission(
        extra,
        boundary,
        "defaultAgentPermissions.extraCalls",
      ),
    );
  }

  const dedupedCalls: AltanaCallPermission[] = [
    ...new Map(
      calls.map((call) => [`${call.to.toLowerCase()}:${call.signature}`, call]),
    ).values(),
  ];

  const hasLegacySpend = opts.tokenSpend !== undefined;
  const hasMultiSpend = opts.tokenSpends !== undefined;
  if (hasLegacySpend === hasMultiSpend) {
    throw new Error(
      "defaultAgentPermissions requires exactly one of tokenSpend or tokenSpends",
    );
  }

  const validateCap = (cap: SpendCap, label: string): void => {
    if (typeof cap.limit !== "bigint" || cap.limit < 0n) {
      throw new Error(`${label}.limit must be a non-negative bigint`);
    }
    if (
      cap.period !== undefined &&
      !["minute", "hour", "day", "week", "month", "year"].includes(cap.period)
    ) {
      throw new Error(`${label} has an unsupported spend period`);
    }
  };

  const tokenSpendEntries: AltanaSpendPermission[] = [];
  if (opts.tokenSpend !== undefined) {
    validateCap(opts.tokenSpend, "tokenSpend");
    tokenSpendEntries.push({
      limit: opts.tokenSpend.limit,
      period: opts.tokenSpend.period ?? "day",
      token: targets.paymentToken,
    });
  } else {
    const caps = opts.tokenSpends ?? [];
    if (caps.length === 0) {
      throw new Error("tokenSpends must be non-empty");
    }
    const seen = new Set<string>();
    for (const cap of caps) {
      validateCap(cap, `tokenSpends[${tokenSpendEntries.length}]`);
      let token: `0x${string}`;
      try {
        token = toChecksumAddress(cap.token);
      } catch (error) {
        throw new Error(
          `token spend address is invalid: ${String(cap.token)}`,
          {
            cause: error,
          },
        );
      }
      if (token !== cap.token) {
        throw new Error(
          `token spend address must be checksummed: ${cap.token}`,
        );
      }
      try {
        getAssetByAddress(opts.chainId, token);
      } catch (error) {
        throw new Error(
          `token spend address must be a registered catalog token on chainId=${opts.chainId}: ${token}`,
          { cause: error },
        );
      }
      const key = token.toLowerCase();
      if (seen.has(key)) {
        throw new Error(`duplicate token spend cap: ${token}`);
      }
      seen.add(key);
      tokenSpendEntries.push({
        limit: cap.limit,
        period: cap.period ?? "day",
        token,
      });
    }
  }

  const spend: AltanaSpendPermission[] = [
    ...tokenSpendEntries,
    // Unconditional: the session's own relay gas fee counts as native
    // spend; without this entry every execute reverts NoSpendPermissions.
    {
      limit: opts.nativeSpend?.limit ?? DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
      period: opts.nativeSpend?.period ?? "day",
    },
  ];

  return { calls: dedupedCalls, spend };
}
