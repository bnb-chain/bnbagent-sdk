import { recoverTypedDataAddress } from "viem";
import { describe, expect, it } from "vitest";
import {
  PolicyViolation,
  SigningPolicy,
  check,
  inferPrimaryType,
} from "../src/signing/index.js";
import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";

const types = {
  PermitWitnessTransferFrom: [
    { name: "permitted", type: "TokenPermissions" },
    { name: "spender", type: "address" },
    { name: "nonce", type: "uint256" },
    { name: "deadline", type: "uint256" },
    { name: "witness", type: "Witness" },
  ],
  TokenPermissions: [
    { name: "token", type: "address" },
    { name: "amount", type: "uint256" },
  ],
  Witness: [{ name: "recipient", type: "address" }],
};
const permit2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const domain = {
  name: "Permit2",
  chainId: 56,
  verifyingContract: permit2,
} as const;
const message = {
  permitted: {
    token: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    amount: 1000000n,
  },
  spender: "0x1111111111111111111111111111111111111111",
  nonce: 1n,
  deadline: 1900000000n,
  witness: { recipient: "0x2222222222222222222222222222222222222222" },
};

describe("nested EIP-712 signing", () => {
  it("infers the unique root regardless of declaration order", () => {
    expect(inferPrimaryType(types)).toBe("PermitWitnessTransferFrom");
    expect(
      inferPrimaryType(Object.fromEntries(Object.entries(types).reverse())),
    ).toBe("PermitWitnessTransferFrom");
    expect(
      inferPrimaryType({
        Root: [{ name: "items", type: "Child[][2]" }],
        Child: [],
      }),
    ).toBe("Root");
  });
  it("rejects ambiguous, cyclic and disconnected type graphs", () => {
    for (const graph of [
      { A: [], B: [] },
      { A: [{ name: "b", type: "B" }], B: [{ name: "a", type: "A" }] },
      {
        Root: [],
        A: [{ name: "b", type: "B" }],
        B: [{ name: "a", type: "A" }],
      },
    ])
      expect(() => inferPrimaryType(graph)).toThrow();
  });
  it("bounds untrusted schema size and depth before signing", () => {
    const chain = (length: number) =>
      Object.fromEntries(
        Array.from({ length }, (_, i) => [
          `T${i}`,
          i === length - 1 ? [] : [{ name: "next", type: `T${i + 1}` }],
        ]),
      );
    expect(inferPrimaryType(chain(64))).toBe("T0");
    for (const schema of [
      chain(65),
      chain(20000),
      {
        Root: [{ name: "child", type: "Child" }],
        Child: Array.from({ length: 4096 }, (_, i) => ({
          name: `f${i}`,
          type: "uint256",
        })),
      },
    ])
      expect(() => inferPrimaryType(schema)).toThrow(PolicyViolation);
    // A shared subtree visited earlier through a shorter path must still count.
    const shared = chain(64);
    shared.T0 = [
      { name: "short", type: "Leaf" },
      { name: "next", type: "T1" },
    ];
    shared.T63 = [{ name: "long", type: "Leaf" }];
    shared.Leaf = [];
    expect(() => inferPrimaryType(shared)).toThrow("depth 64");
  });
  it("rejects a hostile array-suffix type string in linear time (ReDoS regression)", () => {
    // The unanchored suffix strip `/(\[[0-9]*\])+$/` needed >2 minutes for the
    // first input; the anchored parser plus the identifier length cap must
    // reject both immediately.
    const started = performance.now();
    expect(() =>
      inferPrimaryType({
        A: [{ name: "x", type: `${"[0]".repeat(160_000)}x` }],
        B: [],
      }),
    ).toThrow(PolicyViolation);
    expect(() =>
      inferPrimaryType({
        A: [{ name: "x", type: `Child${"[1]".repeat(80)}x` }],
        Child: [],
      }),
    ).toThrow(PolicyViolation);
    expect(performance.now() - started).toBeLessThan(1_000);
  });
  it("accepts every EIP-712 atomic type, scalar and array", () => {
    const atomic = [
      "address",
      "bool",
      "string",
      "bytes",
      ...Array.from({ length: 32 }, (_, i) => `bytes${i + 1}`),
      ...Array.from({ length: 32 }, (_, i) => `uint${(i + 1) * 8}`),
      ...Array.from({ length: 32 }, (_, i) => `int${(i + 1) * 8}`),
    ];
    const fields = atomic.flatMap((type, i) => [
      { name: `a${i}`, type },
      { name: `b${i}`, type: `${type}[]` },
      { name: `c${i}`, type: `${type}[3][]` },
    ]);
    expect(
      inferPrimaryType({
        Root: [{ name: "leaves", type: "Leaf[]" }],
        Leaf: fields,
      }),
    ).toBe("Root");
    expect(inferPrimaryType({ Only: fields })).toBe("Only");
  });
  it("rejects type spellings EIP-712 does not define", () => {
    for (const type of [
      "uint",
      "int",
      "uint7",
      "uint257",
      "uint08",
      "bytes0",
      "bytes33",
      "uint256[0]",
      "uint256[01]",
      "uint256 []",
      " uint256",
      "Child[abc]",
      "EIP712Domain",
      "Missing",
    ])
      expect(() =>
        inferPrimaryType({
          Root: [
            { name: "v", type },
            { name: "c", type: "Child" },
          ],
          Child: [],
        }),
      ).toThrow(/type/);
  });
  it("validates single-struct schemas with the same rules", () => {
    expect(() => inferPrimaryType({ Foo: "garbage" })).toThrow(
      /must be an array of field descriptors/,
    );
    expect(() =>
      inferPrimaryType({ Foo: [{ name: "x", type: "NotAType" }] }),
    ).toThrow(/unknown type/);
    expect(() =>
      inferPrimaryType({ Foo: [{ name: "self", type: "Foo[]" }] }),
    ).toThrow(/cyclic/);
    expect(() =>
      inferPrimaryType({
        Foo: Array.from({ length: 4097 }, (_, i) => ({
          name: `f${i}`,
          type: "uint256",
        })),
      }),
    ).toThrow(/4096 fields/);
  });
  it("rejects malformed struct and field names", () => {
    const long = "a".repeat(257);
    for (const schema of [
      // struct name shadows an atomic type
      {
        address: [{ name: "x", type: "uint256" }],
        Root: [{ name: "a", type: "address" }],
      },
      { "Bad Name": [] },
      { [long]: [] },
      { Root: [{ type: "uint256" }] },
      { Root: [{ name: 123, type: "uint256" }] },
      { Root: [{ name: "x y", type: "uint256" }] },
      { Root: [{ name: long, type: "uint256" }] },
      {
        Root: [
          { name: "x", type: "uint256" },
          { name: "x", type: "address" },
        ],
      },
      { Root: ["uint256 x"] },
      { Root: [null] },
    ])
      expect(() => inferPrimaryType(schema as Record<string, unknown>)).toThrow(
        PolicyViolation,
      );
  });
  it("escapes and caps untrusted names in error messages", () => {
    expect(() => inferPrimaryType({ "Evil\nname": [] })).toThrow(
      /"Evil\\nname"/,
    );
    const many = Object.fromEntries(
      Array.from({ length: 50 }, (_, i) => [`R${i}`, []]),
    );
    expect(() => inferPrimaryType(many)).toThrow(/\+42 more/);
  });
  it("retains denylist precedence for newly supported nested Permit2 types", () => {
    const policy = SigningPolicy.strictDefault().extend({
      domainAllowlist: [[56, permit2]],
      primaryTypeAllowlist: ["PermitSingle"],
    });
    expect(() =>
      check(
        policy,
        domain,
        {
          PermitSingle: [{ name: "details", type: "PermitDetails" }],
          PermitDetails: [{ name: "amount", type: "uint160" }],
        },
        {},
      ),
    ).toThrow("denylisted");
  });
  it("signs through the public wallet API only after explicit policy opt-in", async () => {
    const wallet = new EVMWalletProvider({
      password: "test-only",
      privateKey: "11".repeat(32),
      persist: false,
      signingPolicy: SigningPolicy.strictDefault().extend({
        domainAllowlist: [[56, permit2]],
        primaryTypeAllowlist: ["PermitWitnessTransferFrom"],
      }),
    });
    const result = await wallet.signTypedData(domain, types, message);
    expect(
      await recoverTypedDataAddress({
        domain,
        types,
        message,
        primaryType: "PermitWitnessTransferFrom",
        signature: result.signature as `0x${string}`,
      }),
    ).toBe(wallet.address);
    const strict = new EVMWalletProvider({
      password: "test-only",
      privateKey: "11".repeat(32),
      persist: false,
    });
    await expect(strict.signTypedData(domain, types, message)).rejects.toThrow(
      "allowlist",
    );
    await expect(
      wallet.signTypedData({ ...domain, chainId: 1 }, types, message),
    ).rejects.toThrow("allowlist");
  });
});
