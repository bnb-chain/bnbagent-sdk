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
