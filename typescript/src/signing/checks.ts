/**
 * Pure checking functions for SigningPolicy enforcement.
 *
 * Port of `python/bnbagent/signing/checks.py`.
 *
 * Kept separate from {@link "./policy.js"} so they can be unit-tested without
 * instantiating a full SigningPolicy and so future variations (e.g.
 * async-loggable check, dry-run check) can compose them.
 */

import { getAddress as toChecksumAddress } from "viem";
import { PolicyViolation } from "./errors.js";
import { EIP3009_CANONICAL_FIELDS, EIP3009_TYPES } from "./policy.js";
import type { SigningPolicy } from "./policy.js";

export const EIP712_DOMAIN_TYPE_NAME = "EIP712Domain";

/**
 * Return the single non-`EIP712Domain` struct name in `types`.
 *
 * Raises {@link PolicyViolation} if there isn't exactly one. Multiple
 * non-domain structs would create ambiguity over what gets signed and is
 * explicitly rejected — caller must split into separate sign calls.
 */
export function inferPrimaryType(types: Record<string, unknown>): string {
  const nonDomain = Object.keys(types).filter(
    (k) => k !== EIP712_DOMAIN_TYPE_NAME,
  );
  if (nonDomain.length === 0) {
    throw new PolicyViolation(
      "EIP-712 types contains no non-EIP712Domain struct",
    );
  }
  if (nonDomain.length > 1) {
    const listRepr = `[${nonDomain.map((s) => `'${s}'`).join(", ")}]`;
    throw new PolicyViolation(
      `EIP-712 types contains multiple non-EIP712Domain structs: ${listRepr}; sign one at a time to avoid primary-type ambiguity`,
    );
  }
  return nonDomain[0] as string;
}

function checksumOrNone(addr: unknown): string | undefined {
  if (typeof addr !== "string") return undefined;
  try {
    return toChecksumAddress(addr as `0x${string}`);
  } catch {
    return undefined;
  }
}

function reprValue(v: unknown): string {
  if (typeof v === "string") return `'${v}'`;
  if (v === null || v === undefined) return "None";
  return String(v);
}

/**
 * Coerce `value` to an integer with Python `int()` semantics.
 *
 * Mirrors Python's `int(str)` for strings: plain base-10 digits only (with
 * optional sign) — rejects hex ("0x38"), exponents ("1e2"), and non-numeric
 * words ("Infinity"/"NaN") that JS's `Number()` would otherwise silently
 * accept, which would let a malformed chainId / validBefore / validAfter /
 * policy setting slip past a check meant to fail closed.
 *
 * @throws {Error} If `value` is not int-coercible.
 */
export function toIntStrict(value: unknown): number {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${value} is not finite`);
    return Math.trunc(value);
  }
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!/^[+-]?\d+$/.test(trimmed)) {
      throw new Error(`invalid literal for int(): ${reprValue(value)}`);
    }
    return Math.trunc(Number(trimmed));
  }
  throw new Error(`${reprValue(value)} is not int-coercible`);
}

/**
 * Apply `policy` to a typed-data sign request.
 *
 * Returns the inferred `primaryType` on success. Raises
 * {@link PolicyViolation} on first failure (does not aggregate errors).
 *
 * Ordering matters — structure → denylist → allowlist → domain → validity —
 * so the most categorical refusal is reported first.
 */
export function check(
  policy: SigningPolicy,
  domain: Record<string, unknown>,
  types: Record<string, unknown>,
  message: Record<string, unknown>,
  opts: { now?: number } = {},
): string {
  const primaryType = inferPrimaryType(types);

  // ── Structure: domain must have chainId + verifyingContract ─────────
  if (domain.chainId === undefined || domain.chainId === null) {
    throw new PolicyViolation(
      "EIP-712 domain missing chainId — refusing to sign",
      {
        primaryType,
      },
    );
  }
  if (
    domain.verifyingContract === undefined ||
    domain.verifyingContract === null
  ) {
    throw new PolicyViolation(
      "EIP-712 domain missing verifyingContract — refusing to sign",
      {
        primaryType,
      },
    );
  }

  let chainId: number;
  try {
    chainId = toIntStrict(domain.chainId);
  } catch {
    throw new PolicyViolation(
      `EIP-712 domain chainId is not integer-coercible: ${reprValue(domain.chainId)}`,
      { primaryType },
    );
  }

  const verifying = checksumOrNone(domain.verifyingContract);
  if (verifying === undefined) {
    throw new PolicyViolation(
      `EIP-712 domain verifyingContract is not a valid address: ${reprValue(domain.verifyingContract)}`,
      { primaryType, chainId },
    );
  }

  // ── Denylist takes precedence (defense against allowlist misconfig) ──
  if (policy.primaryTypeDenylist.has(primaryType)) {
    throw new PolicyViolation(
      `primary type '${primaryType}' is denylisted (unbounded allowance type — unsafe for agent signing)`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  // ── Allowlist ─────────────────────────────────────────────────────
  // Empty allowlist == "no whitelist applied" (caller opted out, e.g.
  // SigningPolicy.permissive() for tests). Strict policies always seed a
  // non-empty allowlist.
  if (
    policy.primaryTypeAllowlist.size > 0 &&
    !policy.primaryTypeAllowlist.has(primaryType)
  ) {
    throw new PolicyViolation(
      `primary type '${primaryType}' not in allowlist (extend SigningPolicy to opt in)`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  // ── Domain allowlist ─────────────────────────────────────────────
  if (!policy.allowUnknownDomain) {
    const key = `${chainId}:${verifying}`;
    if (!policy.domainAllowlist.has(key)) {
      throw new PolicyViolation(
        `domain (chain_id=${chainId}, verifyingContract=${verifying}) not in allowlist; extend SigningPolicy if intentional`,
        { primaryType, chainId, verifyingContract: verifying },
      );
    }
  }

  // ── Field-shape pinning for known structs ─────────────────────────
  checkFieldShape(types, primaryType, chainId, verifying);

  // ── Validity window (only if primary type requires it) ────────────
  if (policy.validityRequiredPrimaryTypes.has(primaryType)) {
    checkValidity(policy, primaryType, message, chainId, verifying, opts.now);
  }

  return primaryType;
}

/**
 * Pin the field-shape of structs whose shape we know canonically.
 *
 * The allowlist above is name-scoped, and `types` is caller-supplied — for x402
 * it arrives verbatim in an untrusted 402 response body. Without this check an
 * attacker keeps the allowlisted name and rewrites the field's Solidity type,
 * which silently changes what the encoder will accept: the canonical `uint256`
 * is what makes a negative `value` unencodable, so swapping in `int256` removes
 * a value-domain guard the downstream layers were relying on. Field order and
 * count matter too — both feed the typeHash.
 *
 * Scope is keyed on the struct **name**, not on the policy: anything calling
 * itself `TransferWithAuthorization` is held to the EIP-3009 shape even under
 * `permissive()`, because the canonical shape is a property of the name rather
 * than of the ruleset. Types with any other name are untouched, so custom
 * `extend()` primary types keep working.
 *
 * Two neighbouring surfaces are deliberately left unpinned:
 *
 * - `EIP712Domain` — EIP-712 makes every domain field optional, so there is no
 *   single canonical shape to pin against (`salt` is legitimate, so is omitting
 *   `version`). Checked for exploitability: altering the domain shape changes
 *   the domainSeparator, which makes the resulting signature useless against
 *   the real token, and an out-of-range `chainId` cannot pass the domain
 *   allowlist either.
 * - The Permit2 family (`PERMIT2_SIGNATURE_TRANSFER_TYPES`) — opt-in via
 *   `extend()` only, and the SDK carries no canonical field table for them.
 *   Pin them here if they ever enter a default policy.
 */
function checkFieldShape(
  types: Record<string, unknown>,
  primaryType: string,
  chainId: number,
  verifying: string,
): void {
  if (!EIP3009_TYPES.has(primaryType)) return;
  const fields = types[primaryType];
  if (!Array.isArray(fields)) {
    throw new PolicyViolation(
      `types['${primaryType}'] must be an array of field descriptors, got ${reprValue(fields)}`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }
  const describe = (pairs: ReadonlyArray<readonly [unknown, unknown]>) =>
    pairs.map(([n, t]) => `${String(n)} ${String(t)}`).join(", ");
  const actual: ReadonlyArray<readonly [unknown, unknown]> = fields.map((f) =>
    typeof f === "object" && f !== null
      ? ([
          (f as Record<string, unknown>).name,
          (f as Record<string, unknown>).type,
        ] as const)
      : ([undefined, undefined] as const),
  );
  const matches =
    actual.length === EIP3009_CANONICAL_FIELDS.length &&
    actual.every(
      ([n, t], i) =>
        n === EIP3009_CANONICAL_FIELDS[i]?.[0] &&
        t === EIP3009_CANONICAL_FIELDS[i]?.[1],
    );
  if (!matches) {
    throw new PolicyViolation(
      `types['${primaryType}'] does not match the canonical EIP-3009 field shape — refusing to sign a struct whose encoding differs from the on-chain type. expected (${describe(EIP3009_CANONICAL_FIELDS)}), got (${describe(actual)})`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }
}

function checkValidity(
  policy: SigningPolicy,
  primaryType: string,
  message: Record<string, unknown>,
  chainId: number,
  verifying: string,
  now: number | undefined,
): void {
  if (!("validBefore" in message) || !("validAfter" in message)) {
    throw new PolicyViolation(
      `primary type '${primaryType}' requires validBefore + validAfter in message; refusing to sign open-ended authorization`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  let validBefore: number;
  let validAfter: number;
  try {
    validBefore = toIntStrict(message.validBefore);
    validAfter = toIntStrict(message.validAfter);
  } catch (e) {
    throw new PolicyViolation(
      `validBefore / validAfter not integer-coercible: ${(e as Error).message}`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  if (validBefore <= validAfter) {
    throw new PolicyViolation(
      `validBefore (${validBefore}) must be > validAfter (${validAfter})`,
      {
        primaryType,
        chainId,
        verifyingContract: verifying,
      },
    );
  }

  const window = validBefore - validAfter;
  if (window > policy.maxValidityWindowSeconds) {
    throw new PolicyViolation(
      `validity window ${window}s exceeds max ${policy.maxValidityWindowSeconds}s`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  const current =
    now !== undefined ? Math.trunc(now) : Math.trunc(Date.now() / 1000);
  if (validBefore <= current) {
    throw new PolicyViolation(
      `validBefore (${validBefore}) is already expired (current=${current})`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }

  const future = validBefore - current;
  if (future > policy.maxFutureValiditySeconds) {
    throw new PolicyViolation(
      `validBefore ${validBefore} is ${future}s in the future, exceeds max ${policy.maxFutureValiditySeconds}s`,
      { primaryType, chainId, verifyingContract: verifying },
    );
  }
}
