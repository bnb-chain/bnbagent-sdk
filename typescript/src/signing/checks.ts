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

function toIntStrict(value: unknown): number {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${value} is not finite`);
    return Math.trunc(value);
  }
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "string") {
    // Mirror Python's int(str) semantics: plain base-10 digits only (with
    // optional sign) — reject hex ("0x38"), exponents ("1e2"), and
    // non-numeric words ("Infinity"/"NaN") that JS's `Number()` would
    // otherwise silently accept, which would let a malformed chainId /
    // validBefore / validAfter slip past a check meant to fail closed.
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

  // ── Validity window (only if primary type requires it) ────────────
  if (policy.validityRequiredPrimaryTypes.has(primaryType)) {
    checkValidity(policy, primaryType, message, chainId, verifying, opts.now);
  }

  return primaryType;
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
