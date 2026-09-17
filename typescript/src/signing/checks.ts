/**
 * Pure checking functions for SigningPolicy enforcement.
 *
 * Port of `python/bnbagent/signing/checks.py`. `inferPrimaryType` is
 * currently ahead of the Python port: it accepts nested struct graphs, the
 * Python side still requires a single non-domain struct.
 *
 * Kept separate from {@link "./policy.js"} so they can be unit-tested without
 * instantiating a full SigningPolicy and so future variations (e.g.
 * async-loggable check, dry-run check) can compose them.
 */

import { getAddress as toChecksumAddress } from "viem";
import { describeValue } from "../utils/errorMessages.js";
import { PolicyViolation } from "./errors.js";
import { EIP3009_CANONICAL_FIELDS, EIP3009_TYPES } from "./policy.js";
import type { SigningPolicy } from "./policy.js";

export const EIP712_DOMAIN_TYPE_NAME = "EIP712Domain";

// ── Schema bounds ────────────────────────────────────────────────────────
//
// `types` is caller-supplied — for x402 it arrives verbatim in an untrusted
// 402 response body — so every limit below is enforced *before* the schema
// reaches a regex, the recursive walk or the wallet signer. A hostile schema
// therefore costs O(bytes) and nothing more.

/** Max non-`EIP712Domain` structs in one `types` dict. */
const MAX_SCHEMA_STRUCTS = 256;
/** Max field descriptors summed over all non-domain structs. */
const MAX_SCHEMA_FIELDS = 4096;
/** Max struct nesting depth (the primary type is depth 1). */
const MAX_SCHEMA_DEPTH = 64;
/** Max length of a struct name, a field name or a field type string. */
const MAX_SCHEMA_IDENTIFIER_LENGTH = 256;
/** How many struct names an error message lists before eliding the rest. */
const MAX_NAMES_IN_ERROR = 8;

// Solidity identifier — EIP-712 requires struct and field names to be one.
const IDENTIFIER_RE = /^[A-Za-z_$][A-Za-z0-9_$]*$/;
// `<identifier>` followed by zero or more `[]` / `[N]` array suffixes (N has no
// leading zero). Anchored at BOTH ends so the engine cannot slide the match
// start: the previous unanchored suffix strip `/(\[[0-9]*\])+$/` backtracked
// quadratically (60 KB of "[0]…x" took 2 s, 480 KB took 132 s on one core).
const TYPE_REFERENCE_RE =
  /^([A-Za-z_$][A-Za-z0-9_$]*)((?:\[(?:[1-9][0-9]*)?\])*)$/;
const SIZED_ATOMIC_RE = /^(bytes|u?int)([1-9][0-9]*)$/;

/**
 * True iff `type` is an EIP-712 atomic type: `address`, `bool`, `string`,
 * `bytes`, `bytes1`…`bytes32`, `uint8`…`uint256` / `int8`…`int256` (step 8).
 *
 * The Solidity aliases `uint` / `int` are deliberately NOT accepted — EIP-712
 * forbids them, and tolerating them would hash a type string no verifying
 * contract uses. Nothing here is a value check; viem still validates the
 * message against the declared types before signing.
 */
function isAtomicType(type: string): boolean {
  if (
    type === "address" ||
    type === "bool" ||
    type === "string" ||
    type === "bytes"
  ) {
    return true;
  }
  const m = SIZED_ATOMIC_RE.exec(type);
  if (!m) return false;
  const size = Number(m[2]);
  return m[1] === "bytes" ? size <= 32 : size % 8 === 0 && size <= 256;
}

function describeNames(names: readonly string[]): string {
  const shown = names.slice(0, MAX_NAMES_IN_ERROR).map(describeValue);
  const rest = names.length - shown.length;
  return rest > 0
    ? `[${shown.join(", ")}, …(+${rest} more)]`
    : `[${shown.join(", ")}]`;
}

/**
 * Validate every non-domain struct and return the struct → referenced-struct
 * edges. Struct names, field names and field types are all checked here, so
 * a single-struct schema gets exactly the same scrutiny as a nested one.
 */
function collectStructEdges(
  types: Record<string, unknown>,
  nonDomain: readonly string[],
): Map<string, string[]> {
  const names = new Set(nonDomain);
  const edges = new Map<string, string[]>();
  let fieldCount = 0;
  for (const name of nonDomain) {
    if (
      name.length > MAX_SCHEMA_IDENTIFIER_LENGTH ||
      !IDENTIFIER_RE.test(name)
    ) {
      throw new PolicyViolation(
        `EIP-712 struct name ${describeValue(name)} is not a valid identifier`,
      );
    }
    if (isAtomicType(name)) {
      throw new PolicyViolation(
        `EIP-712 struct name ${describeValue(name)} shadows an atomic type`,
      );
    }
    const fields = types[name];
    if (!Array.isArray(fields)) {
      throw new PolicyViolation(
        `types[${describeValue(name)}] must be an array of field descriptors, got ${describeValue(fields)}`,
      );
    }
    fieldCount += fields.length;
    if (fieldCount > MAX_SCHEMA_FIELDS) {
      throw new PolicyViolation(
        `EIP-712 schema exceeds ${MAX_SCHEMA_FIELDS} fields`,
      );
    }
    const children: string[] = [];
    const seen = new Set<string>();
    for (const field of fields) {
      if (typeof field !== "object" || field === null) {
        throw new PolicyViolation(
          `EIP-712 struct ${describeValue(name)} has a non-object field descriptor: ${describeValue(field)}`,
        );
      }
      const fieldName = (field as Record<string, unknown>).name;
      const fieldType = (field as Record<string, unknown>).type;
      if (
        typeof fieldName !== "string" ||
        fieldName.length > MAX_SCHEMA_IDENTIFIER_LENGTH ||
        !IDENTIFIER_RE.test(fieldName)
      ) {
        throw new PolicyViolation(
          `EIP-712 struct ${describeValue(name)} has a field whose name is not a valid identifier: ${describeValue(fieldName)}`,
        );
      }
      if (seen.has(fieldName)) {
        throw new PolicyViolation(
          `EIP-712 struct ${describeValue(name)} declares field ${describeValue(fieldName)} twice`,
        );
      }
      seen.add(fieldName);
      if (
        typeof fieldType !== "string" ||
        fieldType.length > MAX_SCHEMA_IDENTIFIER_LENGTH
      ) {
        throw new PolicyViolation(
          `EIP-712 field ${name}.${fieldName} has an invalid type: ${describeValue(fieldType)}`,
        );
      }
      const match = TYPE_REFERENCE_RE.exec(fieldType);
      if (!match) {
        throw new PolicyViolation(
          `EIP-712 field ${name}.${fieldName} has a malformed type reference: ${describeValue(fieldType)}`,
        );
      }
      const base = match[1] as string;
      if (names.has(base)) {
        children.push(base);
      } else if (!isAtomicType(base)) {
        throw new PolicyViolation(
          `EIP-712 field ${name}.${fieldName} references unknown type ${describeValue(base)}`,
        );
      }
    }
    edges.set(name, children);
  }
  return edges;
}

/**
 * Return the unique root of the non-domain struct dependency graph.
 *
 * Every struct is validated first (identifier names, atomic or declared
 * field types, size bounds). Nested structs and arrays of structs are
 * supported; ambiguous, disconnected or cyclic graphs are rejected before
 * the normal signing policy is applied.
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
  if (nonDomain.length > MAX_SCHEMA_STRUCTS) {
    throw new PolicyViolation(
      `EIP-712 schema exceeds ${MAX_SCHEMA_STRUCTS} structs`,
    );
  }

  const edges = collectStructEdges(types, nonDomain);

  // The primary type is the one struct nothing else references.
  const referenced = new Set<string>();
  for (const children of edges.values()) {
    for (const child of children) referenced.add(child);
  }
  const roots = nonDomain.filter((name) => !referenced.has(name));
  if (roots.length === 0) {
    throw new PolicyViolation(
      "EIP-712 types contains a cyclic dependency (every struct is referenced by another)",
    );
  }
  if (roots.length > 1) {
    throw new PolicyViolation(
      `EIP-712 types contains multiple non-EIP712Domain structs without a unique primary type: ${describeNames(roots)}`,
    );
  }
  const root = roots[0] as string;

  // Depth-first walk from the root: rejects cycles, bounds depth (including
  // shared subtrees reached again through a longer path) and confirms every
  // declared struct is reachable.
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const heights = new Map<string, number>();
  const visit = (name: string, depth: number): number => {
    if (depth > MAX_SCHEMA_DEPTH) {
      throw new PolicyViolation(
        `EIP-712 nested schema exceeds depth ${MAX_SCHEMA_DEPTH}`,
      );
    }
    if (visiting.has(name)) {
      throw new PolicyViolation("EIP-712 types contains a cyclic dependency");
    }
    if (visited.has(name)) {
      const height = heights.get(name) as number;
      if (depth + height - 1 > MAX_SCHEMA_DEPTH) {
        throw new PolicyViolation(
          `EIP-712 nested schema exceeds depth ${MAX_SCHEMA_DEPTH}`,
        );
      }
      return height;
    }
    visiting.add(name);
    let height = 1;
    for (const child of edges.get(name) ?? []) {
      height = Math.max(height, 1 + visit(child, depth + 1));
    }
    visiting.delete(name);
    visited.add(name);
    heights.set(name, height);
    return height;
  };
  visit(root, 1);
  if (visited.size !== nonDomain.length) {
    throw new PolicyViolation("EIP-712 types contains disconnected structs");
  }
  return root;
}

function checksumOrNone(addr: unknown): string | undefined {
  if (typeof addr !== "string") return undefined;
  try {
    return toChecksumAddress(addr as `0x${string}`);
  } catch {
    return undefined;
  }
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
      throw new Error(`invalid literal for int(): ${describeValue(value)}`);
    }
    return Math.trunc(Number(trimmed));
  }
  throw new Error(`${describeValue(value)} is not int-coercible`);
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
      `EIP-712 domain chainId is not integer-coercible: ${describeValue(domain.chainId)}`,
      { primaryType },
    );
  }

  const verifying = checksumOrNone(domain.verifyingContract);
  if (verifying === undefined) {
    throw new PolicyViolation(
      `EIP-712 domain verifyingContract is not a valid address: ${describeValue(domain.verifyingContract)}`,
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
      `types['${primaryType}'] must be an array of field descriptors, got ${describeValue(fields)}`,
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
