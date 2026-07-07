/**
 * SigningPolicy — declarative ruleset for guarding sign-typed-data calls.
 *
 * Port of `python/bnbagent/signing/policy.py`.
 *
 * The policy is the SDK's first line of defense against blind-sign attacks
 * delivered through an EIP-712 typed-data payload. It is intentionally
 * **fail-closed by default**: an unknown `verifyingContract` or an
 * unrecognised `primaryType` is refused. Callers extend the policy with
 * explicit allowlist entries when they know what they are doing.
 */

import { getAddress as toChecksumAddress } from "viem";
import { knownPaymentTokens } from "../networks/addresses.js";
import { toIntStrict } from "./checks.js";

// ── Canonical EIP-712 primary types ─────────────────────────────────────
//
// Source: EIP-3009 (canonical names "TransferWithAuthorization" /
// "ReceiveWithAuthorization"); EIP-2612 ("Permit"); Uniswap Permit2 source
// at github.com/Uniswap/permit2 (allowance-transfer: PermitSingle/PermitBatch;
// signature-transfer: PermitTransferFrom/PermitBatchTransferFrom).

export const EIP3009_TYPES: ReadonlySet<string> = new Set([
  "TransferWithAuthorization",
  "ReceiveWithAuthorization",
]);

export const PERMIT_UNBOUNDED_TYPES: ReadonlySet<string> = new Set([
  "Permit", // EIP-2612 — long-lived allowance to a spender
  "PermitSingle", // Permit2 AllowanceTransfer — long-lived allowance
  "PermitBatch", // Permit2 AllowanceTransfer (batch)
]);

// Permit2 SignatureTransfer family — opt-in only, NOT in default allowlist.
// These are *safer* than the unbounded family because the spender contract
// binds (to, requestedAmount) at call time, but full enforcement requires
// witness validation we don't yet do. Callers explicitly extend the policy
// to use these.
export const PERMIT2_SIGNATURE_TRANSFER_TYPES: ReadonlySet<string> = new Set([
  "PermitTransferFrom",
  "PermitBatchTransferFrom",
]);

function unionSets<T>(a: ReadonlySet<T>, b: Iterable<T>): Set<T> {
  const out = new Set(a);
  for (const item of b) out.add(item);
  return out;
}

/** Build the canonical `"chainId:checksumAddress"` domain-allowlist key. */
function domainKey(chainId: number, address: string): string {
  return `${chainId}:${toChecksumAddress(address as `0x${string}`)}`;
}

function domainPairsToKeys(
  pairs: Iterable<readonly [number, string]>,
): Set<string> {
  const out = new Set<string>();
  for (const [chainId, address] of pairs) out.add(domainKey(chainId, address));
  return out;
}

function parseDomainKey(key: string): [number, string] {
  const idx = key.indexOf(":");
  return [Number(key.slice(0, idx)), key.slice(idx + 1)];
}

function sortDomainPairs(pairs: [number, string][]): [number, string][] {
  return [...pairs].sort(
    (a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0),
  );
}

/** Fields accepted by the {@link SigningPolicy} constructor (all optional; unset fields fall back to the same defaults as the Python dataclass). */
export interface SigningPolicyFields {
  /** `"chainId:checksumAddress"` keys — same representation as {@link SigningPolicy.domainAllowlist}. */
  domainAllowlist?: Iterable<string>;
  primaryTypeAllowlist?: Iterable<string>;
  primaryTypeDenylist?: Iterable<string>;
  validityRequiredPrimaryTypes?: Iterable<string>;
  maxValidityWindowSeconds?: number;
  maxFutureValiditySeconds?: number;
  allowUnknownDomain?: boolean;
}

/** `extend()` options: set-like fields are pairs/iterables unioned with the current value; scalars replace. */
export interface SigningPolicyExtendOptions {
  domainAllowlist?: Iterable<readonly [number, string]>;
  primaryTypeAllowlist?: Iterable<string>;
  primaryTypeDenylist?: Iterable<string>;
  validityRequiredPrimaryTypes?: Iterable<string>;
  maxValidityWindowSeconds?: number;
  maxFutureValiditySeconds?: number;
  allowUnknownDomain?: boolean;
}

/**
 * Immutable signing ruleset.
 *
 * Construct via {@link SigningPolicy.strictDefault} (recommended) or
 * {@link SigningPolicy.permissive} (testing only). Extend with
 * {@link SigningPolicy.extend} to add new known domains or primary types.
 */
export class SigningPolicy {
  // Backing storage is private so nothing outside this class can hold a
  // reference to the live Set — `Object.freeze(this)` alone does not stop
  // `set.add(...)` from mutating a Set exposed as a public field (freezing
  // an object doesn't freeze the collection it points to). The public
  // getters below hand out a fresh copy on every read instead, so a caller
  // that casts past `ReadonlySet<string>` only ever mutates a throwaway
  // copy, never the policy's own state.
  readonly #domainAllowlist: ReadonlySet<string>;
  readonly #primaryTypeAllowlist: ReadonlySet<string>;
  readonly #primaryTypeDenylist: ReadonlySet<string>;
  readonly #validityRequiredPrimaryTypes: ReadonlySet<string>;

  /** `(chainId, checksumAddress)` pairs allowed as the EIP-712 `verifyingContract`, keyed as `"chainId:checksumAddress"`. */
  get domainAllowlist(): ReadonlySet<string> {
    return new Set(this.#domainAllowlist);
  }
  /** EIP-712 primary type names accepted. */
  get primaryTypeAllowlist(): ReadonlySet<string> {
    return new Set(this.#primaryTypeAllowlist);
  }
  /** Names refused unconditionally; takes precedence over the allowlist. */
  get primaryTypeDenylist(): ReadonlySet<string> {
    return new Set(this.#primaryTypeDenylist);
  }
  /** Primary types that MUST carry valid `validBefore` / `validAfter` fields. */
  get validityRequiredPrimaryTypes(): ReadonlySet<string> {
    return new Set(this.#validityRequiredPrimaryTypes);
  }
  /** Upper bound on `validBefore - validAfter`. Defaults to 600s (10 min). */
  readonly maxValidityWindowSeconds: number;
  /** Upper bound on `validBefore - now`. Defaults to 900s (15 min). */
  readonly maxFutureValiditySeconds: number;
  /** Bypass {@link domainAllowlist}. Must be set explicitly; default `false`. */
  readonly allowUnknownDomain: boolean;

  /** Environment values (case-insensitive) that block `permissive()` construction unless `allowInProduction` is passed. */
  static readonly PRODUCTION_ENV_MARKERS: ReadonlySet<string> = new Set([
    "prod",
    "production",
    "live",
    "mainnet-prod",
  ]);

  constructor(fields: SigningPolicyFields = {}) {
    this.#domainAllowlist = new Set(fields.domainAllowlist ?? []);
    this.#primaryTypeAllowlist = new Set(fields.primaryTypeAllowlist ?? []);
    this.#primaryTypeDenylist = new Set(fields.primaryTypeDenylist ?? []);
    this.#validityRequiredPrimaryTypes = new Set(
      fields.validityRequiredPrimaryTypes ?? [],
    );
    this.maxValidityWindowSeconds = fields.maxValidityWindowSeconds ?? 600;
    this.maxFutureValiditySeconds = fields.maxFutureValiditySeconds ?? 900;
    this.allowUnknownDomain = fields.allowUnknownDomain ?? false;
    Object.freeze(this);
  }

  // ── Factory presets ──────────────────────────────────────────────────

  /**
   * Recommended fail-closed default for direct-SDK callers.
   *
   * Defaults are deliberately narrow:
   * - domain: only the U-token deployments registered in `../networks`;
   * - allowlist: only EIP-3009 `TransferWithAuthorization` and
   *   `ReceiveWithAuthorization` — the well-understood single-use
   *   authorisation pattern x402 uses;
   * - denylist: every unbounded Permit variant (ERC-2612 / Permit2
   *   AllowanceTransfer);
   * - validity: required for the allowlisted EIP-3009 types, capped at
   *   600s window / 900s future.
   *
   * Permit2 SignatureTransfer types are intentionally **not** allowlisted
   * by default; extend the policy if you need them.
   */
  static strictDefault(): SigningPolicy {
    return new SigningPolicy({
      domainAllowlist: knownPaymentTokens(),
      primaryTypeAllowlist: EIP3009_TYPES,
      primaryTypeDenylist: PERMIT_UNBOUNDED_TYPES,
      validityRequiredPrimaryTypes: EIP3009_TYPES,
      maxValidityWindowSeconds: 600,
      maxFutureValiditySeconds: 900,
      allowUnknownDomain: false,
    });
  }

  /**
   * Testing-only escape: `allowUnknownDomain=true` and empty deny/allow sets.
   *
   * Refuses to construct when `ENV` or `ENVIRONMENT` env vars indicate a
   * production-class environment (case-insensitive match against
   * {@link PRODUCTION_ENV_MARKERS}). Pass `allowInProduction: true` for
   * break-glass scenarios where you understand the consequences.
   *
   * Always `console.warn`s on construction (even outside production) so the
   * bypass shows up in audit grep. Never use this in agent-reachable code
   * paths.
   *
   * @throws {Error} When env indicates production and `allowInProduction` is
   *   not set.
   */
  static permissive(opts: { allowInProduction?: boolean } = {}): SigningPolicy {
    const allowInProduction = opts.allowInProduction ?? false;
    const envRaw = process.env.ENV || process.env.ENVIRONMENT || "";
    const env = envRaw.trim().toLowerCase();
    if (SigningPolicy.PRODUCTION_ENV_MARKERS.has(env) && !allowInProduction) {
      const markers = [...SigningPolicy.PRODUCTION_ENV_MARKERS].sort();
      throw new Error(
        `SigningPolicy.permissive() refused: ENV=${JSON.stringify(envRaw)} indicates production (matches [${markers.join(", ")}]). Pass allowInProduction=true if this is intentional (e.g. break-glass).`,
      );
    }
    console.warn(
      `SigningPolicy.permissive() in use — POLICY DISABLED. This bypasses ALL signing guards; only acceptable in tests. (env=${JSON.stringify(envRaw)}, allowInProduction=${allowInProduction})`,
    );
    return new SigningPolicy({
      domainAllowlist: [],
      primaryTypeAllowlist: [],
      primaryTypeDenylist: [],
      validityRequiredPrimaryTypes: [],
      allowUnknownDomain: true,
    });
  }

  // ── Composition ──────────────────────────────────────────────────────

  /**
   * Return a new policy with extended/overridden fields.
   *
   * Set-like arguments are *unioned* with the current value (additive).
   * Scalar arguments replace the current value when provided.
   */
  extend(opts: SigningPolicyExtendOptions = {}): SigningPolicy {
    return new SigningPolicy({
      domainAllowlist:
        opts.domainAllowlist !== undefined
          ? unionSets(
              this.domainAllowlist,
              domainPairsToKeys(opts.domainAllowlist),
            )
          : this.domainAllowlist,
      primaryTypeAllowlist:
        opts.primaryTypeAllowlist !== undefined
          ? unionSets(this.primaryTypeAllowlist, opts.primaryTypeAllowlist)
          : this.primaryTypeAllowlist,
      primaryTypeDenylist:
        opts.primaryTypeDenylist !== undefined
          ? unionSets(this.primaryTypeDenylist, opts.primaryTypeDenylist)
          : this.primaryTypeDenylist,
      validityRequiredPrimaryTypes:
        opts.validityRequiredPrimaryTypes !== undefined
          ? unionSets(
              this.validityRequiredPrimaryTypes,
              opts.validityRequiredPrimaryTypes,
            )
          : this.validityRequiredPrimaryTypes,
      maxValidityWindowSeconds:
        opts.maxValidityWindowSeconds ?? this.maxValidityWindowSeconds,
      maxFutureValiditySeconds:
        opts.maxFutureValiditySeconds ?? this.maxFutureValiditySeconds,
      allowUnknownDomain: opts.allowUnknownDomain ?? this.allowUnknownDomain,
    });
  }

  // ── Serialization ────────────────────────────────────────────────────

  /**
   * Serialise to a plain object suitable for JSON round-trips.
   *
   * Sets become sorted arrays for deterministic output; domain entries
   * become `[chainId, address]` pairs. Round-trips via {@link fromDict}.
   */
  toDict(): Record<string, unknown> {
    const domainPairs = sortDomainPairs(
      [...this.domainAllowlist].map(parseDomainKey),
    );
    return {
      domainAllowlist: domainPairs.map(([chainId, address]) => [
        chainId,
        address,
      ]),
      primaryTypeAllowlist: [...this.primaryTypeAllowlist].sort(),
      primaryTypeDenylist: [...this.primaryTypeDenylist].sort(),
      validityRequiredPrimaryTypes: [
        ...this.validityRequiredPrimaryTypes,
      ].sort(),
      maxValidityWindowSeconds: this.maxValidityWindowSeconds,
      maxFutureValiditySeconds: this.maxFutureValiditySeconds,
      allowUnknownDomain: this.allowUnknownDomain,
    };
  }

  /**
   * Reconstruct a SigningPolicy from its {@link toDict} output.
   *
   * Missing keys fall back to the constructor defaults (empty sets / 600s
   * window / 900s future / `false` unknown-domain).
   *
   * @throws {Error} On malformed entries (e.g. a domain entry that is not a
   *   two-element array), or on a chainId / maxValidityWindowSeconds /
   *   maxFutureValiditySeconds that isn't strictly integer-coercible (e.g.
   *   hex "0x38" or exponent "1e2" strings) — mirrors Python's `int()`.
   */
  static fromDict(d: Record<string, unknown>): SigningPolicy {
    const rawDomains = (d.domainAllowlist as unknown[] | undefined) ?? [];
    const domainKeys = new Set<string>();
    rawDomains.forEach((entry, i) => {
      if (!Array.isArray(entry) || entry.length !== 2) {
        throw new Error(
          `domain_allowlist[${i}] must be a [chain_id, address] pair, got ${JSON.stringify(entry)}`,
        );
      }
      const [chainIdRaw, addressRaw] = entry;
      domainKeys.add(domainKey(toIntStrict(chainIdRaw), String(addressRaw)));
    });
    return new SigningPolicy({
      domainAllowlist: domainKeys,
      primaryTypeAllowlist:
        (d.primaryTypeAllowlist as string[] | undefined) ?? [],
      primaryTypeDenylist:
        (d.primaryTypeDenylist as string[] | undefined) ?? [],
      validityRequiredPrimaryTypes:
        (d.validityRequiredPrimaryTypes as string[] | undefined) ?? [],
      maxValidityWindowSeconds: toIntStrict(d.maxValidityWindowSeconds ?? 600),
      maxFutureValiditySeconds: toIntStrict(d.maxFutureValiditySeconds ?? 900),
      allowUnknownDomain: Boolean(d.allowUnknownDomain ?? false),
    });
  }

  // ── Human-readable output ────────────────────────────────────────────

  /** Multi-line operator-friendly summary; safe for logs + CLI output. */
  toString(): string {
    const domainPairs = sortDomainPairs(
      [...this.domainAllowlist].map(parseDomainKey),
    );
    const n = domainPairs.length;
    const lines: string[] = [
      "SigningPolicy(",
      `  domainAllowlist (${n} ${n === 1 ? "entry" : "entries"}):`,
    ];
    for (const [chainId, address] of domainPairs) {
      lines.push(`    - chainId=${chainId} verifyingContract=${address}`);
    }
    if (n === 0) lines.push("    (none)");

    const allow = [...this.primaryTypeAllowlist].sort();
    lines.push(
      `  primaryTypeAllowlist=${allow.length ? JSON.stringify(allow) : "(any)"}`,
    );

    const deny = [...this.primaryTypeDenylist].sort();
    lines.push(
      `  primaryTypeDenylist=${deny.length ? JSON.stringify(deny) : "(none)"}`,
    );

    const required = [...this.validityRequiredPrimaryTypes].sort();
    lines.push(
      `  validity: window<=${this.maxValidityWindowSeconds}s, ` +
        `future<=${this.maxFutureValiditySeconds}s, ` +
        `requiredFor=${required.length ? JSON.stringify(required) : "(none)"}`,
    );
    lines.push(`  allowUnknownDomain=${this.allowUnknownDomain}`);
    lines.push(")");
    return lines.join("\n");
  }
}
