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

const AMOUNT_PATTERN = /^(-?)(\d+)(?:\.(\d*))?$/;

/**
 * Convert a human-readable amount (e.g. `"1.5"`) to raw on-chain units.
 *
 * Excess fractional digits beyond `decimals` are truncated (not rounded),
 * matching Python's `int()` truncation semantics.
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
  const [, sign, wholeDigits, fracDigits = ""] = match;

  const truncatedFrac = fracDigits.slice(0, decimals);
  const paddedFrac = truncatedFrac.padEnd(decimals, "0");

  const magnitude = BigInt(wholeDigits + paddedFrac);
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
