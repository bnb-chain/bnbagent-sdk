/**
 * Token amount conversion between human-readable and raw on-chain units.
 *
 * The SDK's protocol clients work in raw integer units (wei-style, `10**decimals`).
 * These helpers convert at the boundary where humans (or configs) speak decimal
 * amounts. `bigint`-based on purpose — `number` arithmetic corrupts 18-decimal
 * amounts the moment a fraction is involved (`1.1 * 10**18` is already wrong
 * in floating point). Parsing the decimal string directly and doing the
 * scaling with `bigint` math keeps the conversion exact, matching Python's
 * `Decimal`-based implementation.
 */

const AMOUNT_PATTERN = /^(-?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/**
 * Convert a human-readable amount (e.g. `"1.5"`) to raw on-chain units.
 *
 * Excess fractional digits beyond `decimals` are truncated (not rounded),
 * matching Python's `int()` truncation semantics.
 *
 * Accepts scientific notation (`"1e-7"`, `"2.5e3"`), matching Python's
 * `Decimal(str(amount))` — `String(1e-7) === "1e-7"` and
 * `String(1e21) === "1e+21"`, so number inputs can surface exponential
 * form even though the caller never wrote one. The exponent is folded in
 * by shifting the decimal point with pure string/bigint arithmetic; no
 * floating-point math is involved anywhere in this function.
 */
export function toRaw(
  amount: string | number | bigint,
  decimals: number,
): bigint {
  const text = String(amount);
  const match = AMOUNT_PATTERN.exec(text);
  if (!match) {
    throw new TypeError(`toRaw: invalid amount string ${JSON.stringify(text)}`);
  }
  const [, sign, wholeDigits, fracDigits = "", expText] = match;

  // Concatenate whole + fractional digits into one digit string, and treat
  // the decimal point as initially sitting right after `wholeDigits`. An
  // exponent shifts that point right (positive) or left (negative).
  const digits = wholeDigits + fracDigits;
  const pointPos = wholeDigits.length + (expText ? Number(expText) : 0);

  let normWhole: string;
  let normFrac: string;
  if (pointPos <= 0) {
    // Point is at or before the start: everything becomes fractional,
    // padded with leading zeros for the shift.
    normWhole = "0";
    normFrac = "0".repeat(-pointPos) + digits;
  } else if (pointPos >= digits.length) {
    // Point is at or past the end: everything is whole, padded with
    // trailing zeros for the shift.
    normWhole = digits + "0".repeat(pointPos - digits.length);
    normFrac = "";
  } else {
    normWhole = digits.slice(0, pointPos);
    normFrac = digits.slice(pointPos);
  }

  const truncatedFrac = normFrac.slice(0, decimals);
  const paddedFrac = truncatedFrac.padEnd(decimals, "0");

  const magnitude = BigInt(normWhole + paddedFrac);
  return sign === "-" ? -magnitude : magnitude;
}

/**
 * Convert raw on-chain units to a human-readable decimal string.
 *
 * Always plain decimal notation (never scientific), with trailing
 * fractional zeros trimmed and a bare `"0"` for zero.
 */
export function fromRaw(raw: bigint | number, decimals: number): string {
  const value = typeof raw === "bigint" ? raw : BigInt(raw);

  const sign = value < 0n ? "-" : "";
  const magnitude = value < 0n ? -value : value;

  const divisor = 10n ** BigInt(decimals);
  const whole = magnitude / divisor;
  const remainder = magnitude % divisor;

  if (remainder === 0n) {
    return `${sign}${whole.toString()}`;
  }

  const fracDigits = remainder
    .toString()
    .padStart(decimals, "0")
    .replace(/0+$/, "");
  return `${sign}${whole.toString()}.${fracDigits}`;
}
