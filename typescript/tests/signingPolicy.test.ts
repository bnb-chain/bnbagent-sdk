import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
  getAddress,
} from "../src/networks/index.js";
import {
  EIP3009_TYPES,
  PERMIT_UNBOUNDED_TYPES,
  PolicyViolation,
  SigningPolicy,
  check,
  inferPrimaryType,
} from "../src/signing/index.js";

/** Ports python/tests/test_signing_policy.py. */

const U_MAINNET = getAddress(BSC_MAINNET_CHAIN_ID).paymentToken;
const U_TESTNET = getAddress(BSC_TESTNET_CHAIN_ID).paymentToken;

const EIP712DOMAIN_FIELDS = [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" },
];
const TWA_FIELDS = [
  { name: "from", type: "address" },
  { name: "to", type: "address" },
  { name: "value", type: "uint256" },
  { name: "validAfter", type: "uint256" },
  { name: "validBefore", type: "uint256" },
  { name: "nonce", type: "bytes32" },
];
const PERMIT_FIELDS = [
  { name: "owner", type: "address" },
  { name: "spender", type: "address" },
  { name: "value", type: "uint256" },
  { name: "nonce", type: "uint256" },
  { name: "deadline", type: "uint256" },
];

const NOW = 1_700_000_000; // frozen time for deterministic validity checks

function twaMsg(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    from: `0x${"a".repeat(40)}`,
    to: `0x${"b".repeat(40)}`,
    value: 1_000_000,
    validAfter: NOW - 60,
    validBefore: NOW + 300,
    nonce: `0x${"c".repeat(64)}`,
    ...overrides,
  };
}

function twaCall(
  policy: SigningPolicy,
  opts: {
    domainOverrides?: Record<string, unknown>;
    messageOverrides?: Record<string, unknown>;
    twaFields?: unknown;
    now?: number;
  } = {},
): string {
  const domain: Record<string, unknown> = {
    name: "United Stables",
    version: "1",
    chainId: BSC_MAINNET_CHAIN_ID,
    verifyingContract: U_MAINNET,
    ...opts.domainOverrides,
  };
  const types = {
    EIP712Domain: EIP712DOMAIN_FIELDS,
    TransferWithAuthorization: opts.twaFields ?? TWA_FIELDS,
  };
  const msg = twaMsg(opts.messageOverrides);
  return check(policy, domain, types, msg, { now: opts.now ?? NOW });
}

// ── strictDefault behavior ──────────────────────────────────────────────

describe("SigningPolicy.strictDefault", () => {
  it("allows U-mainnet TransferWithAuthorization", () => {
    const p = SigningPolicy.strictDefault();
    expect(twaCall(p)).toBe("TransferWithAuthorization");
  });

  it("allows U-testnet TransferWithAuthorization", () => {
    const p = SigningPolicy.strictDefault();
    const pt = twaCall(p, {
      domainOverrides: {
        chainId: BSC_TESTNET_CHAIN_ID,
        verifyingContract: U_TESTNET,
      },
    });
    expect(pt).toBe("TransferWithAuthorization");
  });

  it("rejects unknown verifyingContract", () => {
    const p = SigningPolicy.strictDefault();
    let caught: PolicyViolation | undefined;
    try {
      twaCall(p, {
        domainOverrides: { verifyingContract: `0x${"1".repeat(40)}` },
      });
    } catch (e) {
      caught = e as PolicyViolation;
    }
    expect(caught).toBeInstanceOf(PolicyViolation);
    expect(caught?.message).toContain("not in allowlist");
    expect(caught?.primaryType).toBe("TransferWithAuthorization");
    expect(caught?.chainId).toBe(BSC_MAINNET_CHAIN_ID);
  });

  it("rejects unknown chainId", () => {
    const p = SigningPolicy.strictDefault();
    expect(() => twaCall(p, { domainOverrides: { chainId: 1 } })).toThrow(
      /not in allowlist/,
    );
  });

  it("rejects EIP-2612 Permit (U-token supports it on-chain, denylist must block)", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = { EIP712Domain: EIP712DOMAIN_FIELDS, Permit: PERMIT_FIELDS };
    const msg = {
      owner: `0x${"a".repeat(40)}`,
      spender: `0x${"b".repeat(40)}`,
      value: 2n ** 256n - 1n,
      nonce: 0,
      deadline: 2_000_000_000,
    };
    let caught: PolicyViolation | undefined;
    try {
      check(p, domain, types, msg, { now: NOW });
    } catch (e) {
      caught = e as PolicyViolation;
    }
    expect(caught?.message).toContain("denylisted");
    expect(caught?.primaryType).toBe("Permit");
  });

  it("rejects Permit2 PermitSingle", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "Permit2",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: `0x${"2".repeat(40)}`,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      PermitSingle: PERMIT_FIELDS,
    };
    expect(() => check(p, domain, types, {}, { now: NOW })).toThrow(
      /denylisted/,
    );
  });

  it("denylist takes precedence over allowlist", () => {
    const p = SigningPolicy.strictDefault().extend({
      primaryTypeAllowlist: ["Permit"],
    });
    expect(p.primaryTypeAllowlist.has("Permit")).toBe(true);
    expect(p.primaryTypeDenylist.has("Permit")).toBe(true);
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = { EIP712Domain: EIP712DOMAIN_FIELDS, Permit: PERMIT_FIELDS };
    expect(() => check(p, domain, types, {}, { now: NOW })).toThrow(
      /denylisted/,
    );
  });
});

// ── Validity window ──────────────────────────────────────────────────────

describe("validity window", () => {
  it("rejects validity window too long", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, {
        messageOverrides: { validAfter: NOW - 600, validBefore: NOW + 600 },
      }),
    ).toThrow("validity window 1200s exceeds max 600s");
  });

  it("rejects validBefore too far in the future", () => {
    const p = SigningPolicy.strictDefault();
    // window itself fine (300s) but validBefore is 1500s in the future
    expect(() =>
      twaCall(p, {
        messageOverrides: { validAfter: NOW + 1200, validBefore: NOW + 1500 },
      }),
    ).toThrow(/exceeds max 900s/);
  });

  it("rejects validBefore <= validAfter", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, {
        messageOverrides: { validAfter: NOW + 100, validBefore: NOW + 100 },
      }),
    ).toThrow(/must be >/);
  });

  it("rejects already-expired validBefore", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, {
        messageOverrides: { validAfter: NOW - 600, validBefore: NOW - 300 },
      }),
    ).toThrow(/already expired/);
  });

  it("rejects missing validity fields when required", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    };
    const msg = {
      from: `0x${"a".repeat(40)}`,
      to: `0x${"b".repeat(40)}`,
      value: 1,
    };
    expect(() => check(p, domain, types, msg, { now: NOW })).toThrow(
      /requires validBefore/,
    );
  });
});

// ── EIP-3009 field-shape pinning (SRC-1314) ──────────────────────────────
// The allowlist is name-scoped and `types` is caller-supplied (for x402 it
// comes straight out of an untrusted 402 response body). Keeping the
// allowlisted name while rewriting the field shape changes what the encoder
// accepts, which is how a value-domain guard got silently removed.

describe("EIP-3009 field-shape pinning", () => {
  const withField = (name: string, key: string, value: string) =>
    TWA_FIELDS.map((f) => (f.name === name ? { ...f, [key]: value } : f));
  const SHAPE_ERR = /canonical EIP-3009 field shape/;

  it("rejects value declared as int256", () => {
    // The exploit primitive: int256 makes a negative `value` encodable. The
    // message here carries an ordinary positive value, so this is rejected on
    // field shape alone rather than depending on any amount check.
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, { twaFields: withField("value", "type", "int256") }),
    ).toThrow(SHAPE_ERR);
  });

  it("rejects a narrowed value type", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, { twaFields: withField("value", "type", "uint128") }),
    ).toThrow(SHAPE_ERR);
  });

  it("rejects reordered fields (order feeds the typeHash)", () => {
    const p = SigningPolicy.strictDefault();
    const swapped = [TWA_FIELDS[1], TWA_FIELDS[0], ...TWA_FIELDS.slice(2)];
    expect(() => twaCall(p, { twaFields: swapped })).toThrow(SHAPE_ERR);
  });

  it("rejects an extra field", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, {
        twaFields: [...TWA_FIELDS, { name: "memo", type: "bytes32" }],
      }),
    ).toThrow(SHAPE_ERR);
  });

  it("rejects a missing field", () => {
    const p = SigningPolicy.strictDefault();
    expect(() => twaCall(p, { twaFields: TWA_FIELDS.slice(0, -1) })).toThrow(
      SHAPE_ERR,
    );
  });

  it("rejects a renamed field", () => {
    const p = SigningPolicy.strictDefault();
    expect(() =>
      twaCall(p, { twaFields: withField("value", "name", "amount") }),
    ).toThrow(SHAPE_ERR);
  });

  it("rejects a non-array field descriptor", () => {
    const p = SigningPolicy.strictDefault();
    expect(() => twaCall(p, { twaFields: { from: "address" } })).toThrow(
      /must be an array of field descriptors/,
    );
  });

  it("carries diagnostics on a shape violation", () => {
    const p = SigningPolicy.strictDefault();
    try {
      twaCall(p, { twaFields: withField("value", "type", "int256") });
      throw new Error("expected PolicyViolation");
    } catch (e) {
      expect(e).toBeInstanceOf(PolicyViolation);
      const v = e as PolicyViolation;
      expect(v.primaryType).toBe("TransferWithAuthorization");
      expect(v.chainId).toBe(BSC_MAINNET_CHAIN_ID);
      expect(v.verifyingContract).toBe(U_MAINNET);
    }
  });

  it("still accepts the canonical shape", () => {
    // Guard against the pin being too tight to sign the real thing.
    const p = SigningPolicy.strictDefault();
    expect(twaCall(p)).toBe("TransferWithAuthorization");
  });

  it("accepts ReceiveWithAuthorization, which shares the canonical shape", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      ReceiveWithAuthorization: TWA_FIELDS,
    };
    expect(check(p, domain, types, twaMsg(), { now: NOW })).toBe(
      "ReceiveWithAuthorization",
    );
  });

  it("does not shape-pin unknown primary types", () => {
    // Pinning applies only to structs we know canonically — permissive() and
    // extend() callers signing custom types keep working.
    const p = SigningPolicy.permissive({ allowInProduction: true });
    const domain = {
      name: "Custom",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      MyCustomStruct: [{ name: "whatever", type: "int256" }],
    };
    expect(check(p, domain, types, { whatever: -5 }, { now: NOW })).toBe(
      "MyCustomStruct",
    );
  });
});

// ── Structure / domain shape ─────────────────────────────────────────────

describe("structure checks", () => {
  it("rejects null chainId (present but null — treated as missing)", () => {
    const p = SigningPolicy.strictDefault();
    expect(() => twaCall(p, { domainOverrides: { chainId: null } })).toThrow(
      /missing chainId/,
    );
  });

  it("rejects missing chainId key entirely", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    };
    expect(() => check(p, domain, types, twaMsg(), { now: NOW })).toThrow(
      /missing chainId/,
    );
  });

  it("rejects missing verifyingContract", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    };
    expect(() => check(p, domain, types, twaMsg(), { now: NOW })).toThrow(
      /missing verifyingContract/,
    );
  });

  it("rejects multiple non-domain structs", () => {
    const p = SigningPolicy.strictDefault();
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
      Permit: PERMIT_FIELDS,
    };
    expect(() => check(p, domain, types, twaMsg(), { now: NOW })).toThrow(
      /multiple non-EIP712Domain/,
    );
  });

  it("rejects a hex-string chainId (must not silently coerce like JS Number())", () => {
    const p = SigningPolicy.strictDefault();
    // Number("0x38") === 56 in JS but Python's int("0x38") raises ValueError;
    // the policy must fail closed the same way rather than accepting a
    // disguised chain id.
    expect(() => twaCall(p, { domainOverrides: { chainId: "0x38" } })).toThrow(
      /not integer-coercible/,
    );
  });

  it("rejects an exponent-notation chainId string", () => {
    const p = SigningPolicy.strictDefault();
    expect(() => twaCall(p, { domainOverrides: { chainId: "5.6e1" } })).toThrow(
      /not integer-coercible/,
    );
  });
});

// ── Composition ──────────────────────────────────────────────────────────

describe("extend", () => {
  it("unions allowlists and leaves original policy untouched", () => {
    const p = SigningPolicy.strictDefault();
    const p2 = p.extend({
      primaryTypeAllowlist: ["Quote"],
      domainAllowlist: [[56, `0x${"9".repeat(40)}`]],
    });
    expect(p.primaryTypeAllowlist).toEqual(EIP3009_TYPES);
    expect(p2.primaryTypeAllowlist.has("Quote")).toBe(true);
    expect(p2.primaryTypeAllowlist.has("TransferWithAuthorization")).toBe(true);
    expect(p2.domainAllowlist.has(`56:0x${"9".repeat(40)}`)).toBe(true);
    expect(p2.domainAllowlist.has(`56:${U_MAINNET}`)).toBe(true); // original kept
  });

  it("overrides scalars without touching untouched scalars", () => {
    const p = SigningPolicy.strictDefault().extend({
      maxValidityWindowSeconds: 300,
    });
    expect(p.maxValidityWindowSeconds).toBe(300);
    expect(p.maxFutureValiditySeconds).toBe(900);
  });
});

describe("SigningPolicy.permissive", () => {
  it("passes unknown domain and unknown primary type", () => {
    const p = SigningPolicy.permissive();
    const domain = { chainId: 999, verifyingContract: `0x${"f".repeat(40)}` };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      SomethingExotic: [{ name: "x", type: "uint256" }],
    };
    const msg = { x: 1 };
    expect(check(p, domain, types, msg, { now: NOW })).toBe("SomethingExotic");
  });
});

// ── Error diagnostics ────────────────────────────────────────────────────

describe("PolicyViolation diagnostics", () => {
  it("carries structured fields and renders them in the message", () => {
    const p = SigningPolicy.strictDefault();
    const badAddr = `0x${"1".repeat(40)}`;
    let caught: PolicyViolation | undefined;
    try {
      twaCall(p, { domainOverrides: { verifyingContract: badAddr } });
    } catch (e) {
      caught = e as PolicyViolation;
    }
    expect(caught).toBeInstanceOf(PolicyViolation);
    expect(caught?.primaryType).toBe("TransferWithAuthorization");
    expect(caught?.chainId).toBe(BSC_MAINNET_CHAIN_ID);
    expect(caught?.verifyingContract).toBe(badAddr);
    const s = caught?.message ?? "";
    expect(s).toContain("TransferWithAuthorization");
    expect(s).toContain(String(BSC_MAINNET_CHAIN_ID));
    expect(s).toContain(badAddr);
  });
});

// ── inferPrimaryType ──────────────────────────────────────────────────────

describe("inferPrimaryType", () => {
  it("returns the single non-domain struct name", () => {
    expect(
      inferPrimaryType({ EIP712Domain: [], TransferWithAuthorization: [] }),
    ).toBe("TransferWithAuthorization");
  });

  it("rejects an empty (domain-only) type set", () => {
    expect(() => inferPrimaryType({ EIP712Domain: [] })).toThrow(
      /no non-EIP712Domain/,
    );
  });

  it("rejects multiple non-domain structs", () => {
    expect(() => inferPrimaryType({ EIP712Domain: [], A: [], B: [] })).toThrow(
      /multiple/,
    );
  });
});

// ── Bundle-level sanity ──────────────────────────────────────────────────

describe("type-set constants", () => {
  it("EIP3009_TYPES has the expected contents", () => {
    expect(EIP3009_TYPES).toEqual(
      new Set(["TransferWithAuthorization", "ReceiveWithAuthorization"]),
    );
  });

  it("PERMIT_UNBOUNDED_TYPES has the expected contents", () => {
    expect(PERMIT_UNBOUNDED_TYPES).toEqual(
      new Set(["Permit", "PermitSingle", "PermitBatch"]),
    );
  });
});

// ── Serialization ────────────────────────────────────────────────────────

describe("toDict / fromDict", () => {
  it("returns sorted, deterministic, JSON-friendly output", () => {
    const p = SigningPolicy.strictDefault();
    const d = p.toDict() as {
      domainAllowlist: [number, string][];
      primaryTypeAllowlist: string[];
    };
    expect(d.domainAllowlist).toEqual(
      [...d.domainAllowlist].sort(
        (a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0),
      ),
    );
    expect(d.primaryTypeAllowlist).toEqual([...d.primaryTypeAllowlist].sort());
    expect(Array.isArray(d.domainAllowlist)).toBe(true);
    expect(Array.isArray(d.primaryTypeAllowlist)).toBe(true);
    expect(
      d.domainAllowlist.every(
        (pair) => Array.isArray(pair) && pair.length === 2,
      ),
    ).toBe(true);
  });

  it("round-trips strictDefault", () => {
    const p = SigningPolicy.strictDefault();
    const p2 = SigningPolicy.fromDict(p.toDict());
    expect(p2.toDict()).toEqual(p.toDict());
  });

  it("round-trips an extended policy", () => {
    const p = SigningPolicy.strictDefault().extend({
      domainAllowlist: [[1, `0x${"9".repeat(40)}`]],
      primaryTypeAllowlist: ["MyOrder"],
      maxValidityWindowSeconds: 300,
    });
    const p2 = SigningPolicy.fromDict(p.toDict());
    expect(p2.toDict()).toEqual(p.toDict());
  });

  it("falls back to defaults for missing keys", () => {
    const p = SigningPolicy.fromDict({});
    expect(p.domainAllowlist).toEqual(new Set());
    expect(p.maxValidityWindowSeconds).toBe(600);
    expect(p.maxFutureValiditySeconds).toBe(900);
    expect(p.allowUnknownDomain).toBe(false);
  });

  it("rejects a malformed domain entry", () => {
    expect(() =>
      SigningPolicy.fromDict({ domainAllowlist: ["not-a-pair"] }),
    ).toThrow(
      /domain_allowlist\[0\] must be a \[chain_id, address\] pair, got "not-a-pair"/,
    );
  });

  it("rejects a hex-string maxValidityWindowSeconds (must not silently coerce like JS Number())", () => {
    // Number("0x999") === 2457 in JS but Python's int("0x999") raises
    // ValueError; fromDict must fail closed the same way.
    expect(() =>
      SigningPolicy.fromDict({ maxValidityWindowSeconds: "0x999" }),
    ).toThrow(/invalid literal for int\(\)/);
  });

  it("rejects an exponent-notation maxFutureValiditySeconds", () => {
    // Number("1e2") === 100 in JS but Python's int("1e2") raises ValueError.
    expect(() =>
      SigningPolicy.fromDict({ maxFutureValiditySeconds: "1e2" }),
    ).toThrow(/invalid literal for int\(\)/);
  });

  it("rejects a hex-string chainId in a domain pair", () => {
    expect(() =>
      SigningPolicy.fromDict({
        domainAllowlist: [["0x38", `0x${"9".repeat(40)}`]],
      }),
    ).toThrow(/invalid literal for int\(\)/);
  });

  it("valid round-trip still applies defaults when scalars are absent", () => {
    const p = SigningPolicy.fromDict({
      domainAllowlist: [[56, `0x${"9".repeat(40)}`]],
    });
    expect(p.maxValidityWindowSeconds).toBe(600);
    expect(p.maxFutureValiditySeconds).toBe(900);
    expect(p.domainAllowlist.has(`56:0x${"9".repeat(40)}`)).toBe(true);
  });
});

// ── Immutability ─────────────────────────────────────────────────────────

describe("immutability", () => {
  it("mutating a returned set does not affect the live policy", () => {
    const p = SigningPolicy.strictDefault();
    // A caller reaching past the ReadonlySet<string> type (e.g. via `as`)
    // must only ever mutate a throwaway copy, never the policy's own state
    // — otherwise a single bad cast could silently widen what a "strict"
    // policy accepts for every subsequent check() call.
    (p.primaryTypeDenylist as Set<string>).delete("Permit");
    (p.domainAllowlist as Set<string>).add("999:0xdeadbeef");
    expect(p.primaryTypeDenylist.has("Permit")).toBe(true);
    expect(p.domainAllowlist.has("999:0xdeadbeef")).toBe(false);

    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = { EIP712Domain: EIP712DOMAIN_FIELDS, Permit: PERMIT_FIELDS };
    expect(() => check(p, domain, types, {}, { now: NOW })).toThrow(
      /denylisted/,
    );
  });

  it("rejects reassignment of the instance itself", () => {
    const p = SigningPolicy.strictDefault();
    expect(Object.isFrozen(p)).toBe(true);
  });
});

// ── toString ───────────────────────────────────────────────────────────

describe("toString", () => {
  it("contains canonical sections", () => {
    const p = SigningPolicy.strictDefault();
    const s = p.toString();
    expect(s).toContain("SigningPolicy(");
    expect(s).toContain("domainAllowlist (2 entries)");
    expect(s).toContain("TransferWithAuthorization");
    expect(s).toContain("Permit");
    expect(s).toContain("allowUnknownDomain=false");
  });

  it("handles an empty policy cleanly", () => {
    const p = new SigningPolicy({ domainAllowlist: new Set() });
    const s = p.toString();
    expect(s).toContain("(none)");
    // empty primaryTypeAllowlist -> "(any)" marker for "no allowlist applied"
    expect(s).toContain("(any)");
  });
});

// ── permissive() env guard ────────────────────────────────────────────────

describe("SigningPolicy.permissive env guard", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    "prod",
    "production",
    "live",
    "mainnet-prod",
    "PROD",
    " Production ",
    "LIVE",
  ])("refuses in production-like ENV=%s", (envValue) => {
    vi.stubEnv("ENV", envValue);
    expect(() => SigningPolicy.permissive()).toThrow(/indicates production/);
  });

  it.each(["", "dev", "development", "test", "staging", "qa"])(
    "allows non-production ENV=%s",
    (envValue) => {
      vi.stubEnv("ENV", envValue);
      const p = SigningPolicy.permissive();
      expect(p.allowUnknownDomain).toBe(true);
    },
  );

  it("allows break-glass via allowInProduction", () => {
    vi.stubEnv("ENV", "production");
    const p = SigningPolicy.permissive({ allowInProduction: true });
    expect(p.allowUnknownDomain).toBe(true);
  });

  it("falls back to ENVIRONMENT when ENV is unset", () => {
    vi.stubEnv("ENV", "");
    vi.stubEnv("ENVIRONMENT", "prod");
    expect(() => SigningPolicy.permissive()).toThrow(/indicates production/);
  });
});
