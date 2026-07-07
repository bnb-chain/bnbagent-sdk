import { describe, expect, it } from "vitest";
import { fromRaw, toRaw } from "../src/utils/amounts";

describe("toRaw", () => {
  it("converts a string decimal", () => {
    expect(toRaw("1.5", 18)).toBe(1_500_000_000_000_000_000n);
  });

  it("converts an integer", () => {
    expect(toRaw(2, 18)).toBe(2n * 10n ** 18n);
  });

  it("is float-precision safe", () => {
    // 1.1 is not exactly representable as a float; String(1.1) === "1.1" so
    // the bigint math below stays exact instead of inheriting float error.
    expect(toRaw(1.1, 18)).toBe(1_100_000_000_000_000_000n);
  });

  it("converts zero", () => {
    expect(toRaw("0", 18)).toBe(0n);
  });

  it("converts the smallest representable fraction", () => {
    expect(toRaw("0.000000000000000001", 18)).toBe(1n);
  });

  it("supports six decimals", () => {
    expect(toRaw("1.5", 6)).toBe(1_500_000n);
  });

  it("truncates excess fractional digits (Python int() semantics)", () => {
    expect(toRaw("1.23456789", 4)).toBe(12345n);
  });

  it("accepts bigint input", () => {
    expect(toRaw(3n, 18)).toBe(3n * 10n ** 18n);
  });

  it("handles negative amounts", () => {
    expect(toRaw("-1.5", 18)).toBe(-1_500_000_000_000_000_000n);
  });

  it("handles a trailing dot with no fractional digits", () => {
    expect(toRaw("5.", 18)).toBe(5n * 10n ** 18n);
  });

  it("rejects malformed input", () => {
    expect(() => toRaw("abc", 18)).toThrow();
    expect(() => toRaw("1.2.3", 18)).toThrow();
    expect(() => toRaw("", 18)).toThrow();
  });

  // Regression: String(1e-7) === "1e-7" and String(1e21) === "1e+21" — a
  // `number` input can surface exponential notation even though the caller
  // never wrote one. Python's `Decimal(str(amount))` accepts this form, so
  // the TS parser must too. Cross-checked against:
  //   python3 -c "from decimal import Decimal; print(int(Decimal(str(1e-07)) * 10**18))"
  //   -> 100000000000
  it("accepts scientific notation from a float that stringifies exponentially", () => {
    expect(toRaw(1e-7, 18)).toBe(100_000_000_000n);
  });

  it("accepts scientific notation in a string (lowercase e)", () => {
    expect(toRaw("1e-7", 18)).toBe(100_000_000_000n);
  });

  it("accepts scientific notation in a string (uppercase E)", () => {
    expect(toRaw("1E-7", 18)).toBe(100_000_000_000n);
  });

  it("accepts a large positive exponent", () => {
    // python3 -c "from decimal import Decimal; print(int(Decimal(str(1e21)) * 10**18))"
    // -> 1000000000000000000000000000000000000000
    expect(toRaw(1e21, 18)).toBe(10n ** 39n);
  });

  it("accepts a fractional mantissa with a positive exponent", () => {
    // python3 -c "from decimal import Decimal; print(int(Decimal('2.5e3') * 10**6))"
    // -> 2500000000
    expect(toRaw("2.5e3", 6)).toBe(2_500_000_000n);
  });

  it("accepts a negative exponent on a negative amount", () => {
    // python3 -c "from decimal import Decimal; print(int(Decimal(str(-1e-07)) * 10**18))"
    // -> -100000000000
    expect(toRaw(-1e-7, 18)).toBe(-100_000_000_000n);
  });
});

describe("fromRaw", () => {
  it("converts a whole number", () => {
    expect(fromRaw(2n * 10n ** 18n, 18)).toBe("2");
  });

  it("converts a fractional amount", () => {
    expect(fromRaw(1_500_000_000_000_000_000n, 18)).toBe("1.5");
  });

  it("converts one wei with no scientific notation", () => {
    expect(fromRaw(1n, 18)).toBe("0.000000000000000001");
  });

  it("converts zero to a bare '0'", () => {
    expect(fromRaw(0n, 18)).toBe("0");
  });

  it("never emits scientific notation for tiny values", () => {
    expect(fromRaw(1n, 18).toLowerCase()).not.toContain("e");
  });

  it("trims trailing zeros", () => {
    expect(fromRaw(1_100_000_000_000_000_000n, 18)).toBe("1.1");
  });

  it("accepts number input", () => {
    expect(fromRaw(2 * 10 ** 6, 6)).toBe("2");
  });

  it("handles negative raw amounts", () => {
    expect(fromRaw(-1_500_000_000_000_000_000n, 18)).toBe("-1.5");
  });

  it("round-trips a negative amount through toRaw", () => {
    expect(fromRaw(toRaw("-1.5", 18), 18)).toBe("-1.5");
  });
});

describe("round trip", () => {
  it.each(["0.1", "1", "1.5", "123.456789", "0.000000000000000001"])(
    "round-trips %s through toRaw/fromRaw at 18 decimals",
    (human) => {
      expect(fromRaw(toRaw(human, 18), 18)).toBe(human);
    },
  );
});
