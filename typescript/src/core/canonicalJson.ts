import { keccak256, toBytes } from "viem";

/**
 * Recursively sorts object keys (arrays keep their order) so that the
 * resulting structure serializes identically to Python's
 * `json.dumps(x, sort_keys=True)` traversal order.
 */
function sortValue(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortValue);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(v as object).sort()) {
      out[k] = sortValue((v as Record<string, unknown>)[k]);
    }
    return out;
  }
  if (typeof v === "number" && !Number.isFinite(v)) {
    throw new TypeError(
      `canonicalJson: non-finite numbers are not allowed (got ${v})`,
    );
  }
  return v;
}

/**
 * Canonical JSON serialization that is byte-identical to Python's
 * `json.dumps(x, sort_keys=True, separators=(",", ":"))` with the default
 * `ensure_ascii=True`.
 *
 * This is the cross-language interop primitive: every hash computed over
 * JSON structures (deliverable manifests, negotiation quotes, agent URIs)
 * must be computed over this exact string representation so that the
 * TypeScript and Python SDKs agree on the resulting digest.
 *
 * - Object keys are sorted recursively (arrays preserve element order).
 * - Output is compact: no whitespace around separators.
 * - Every character code point > 0x7e (including surrogate pairs for
 *   astral characters) is escaped as a lowercase `\uXXXX` sequence, matching
 *   Python's `ensure_ascii=True` behavior exactly.
 *
 * Known cross-SDK number constraint: JavaScript has a single `number` type,
 * so there is no way to tell "integer 2" apart from "float 2.0" the way
 * Python does. `JSON.stringify(2)` and `JSON.stringify(2.0)` both produce
 * `"2"`, whereas Python's `json.dumps(2.0)` produces `"2.0"` — a whole-number
 * float on the Python side can never round-trip through this function.
 * `-0` also normalizes to `0` (sign is lost), matching `JSON.stringify`.
 * Because of this, wire schemas shared between the TypeScript and Python
 * SDKs represent amounts/prices/other decimal-sensitive values as strings,
 * never as native floats, so hashes and payloads stay byte-identical.
 *
 * Non-finite numbers (`NaN`, `Infinity`, `-Infinity`) have no JSON
 * representation. `JSON.stringify` silently coerces them to `null`, which
 * would silently diverge from Python's `json.dumps` (which emits the bare
 * `NaN`/`Infinity`/`-Infinity` tokens). To avoid that silent divergence,
 * this function throws a `TypeError` instead.
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value)).replace(
    /[\u007f-\uffff]/g,
    (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

/** keccak256 of the UTF-8 bytes of `text`, matching `Web3.keccak(text=...)`. */
export function keccakOfText(text: string): `0x${string}` {
  return keccak256(toBytes(text));
}

/** keccak256 of `value`'s canonical JSON representation. */
export function keccakOfCanonicalJson(value: unknown): `0x${string}` {
  return keccakOfText(canonicalJson(value));
}
