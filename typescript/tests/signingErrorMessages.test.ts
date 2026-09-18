import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { getAddress } from "../src/networks/index.js";
import {
  PolicyViolation,
  SigningPolicy,
  check,
  inferPrimaryType,
} from "../src/signing/index.js";
import { EIP3009_CANONICAL_FIELDS } from "../src/signing/policy.js";
import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";
import { resolveExpectedEip3009Route } from "../src/x402/assets.js";
import { X402PolicyError, X402SignerError } from "../src/x402/errors.js";
import { type SignPaymentOptions, X402Signer } from "../src/x402/signer.js";

const hostile = `\nFAKELOG\r\t\x1b[31m\u2028\u2029${"A".repeat(2 * 1024 * 1024)}`;
const token = getAddress(56).paymentToken;
const domain = { chainId: 56, verifyingContract: token };
const types = {
  TransferWithAuthorization: EIP3009_CANONICAL_FIELDS.map(([name, type]) => ({
    name,
    type,
  })),
};
const now = 1_700_000_000;
const message = { validAfter: now - 60, validBefore: now + 300 };
const policy = SigningPolicy.strictDefault();

function expectSafeMessage(error: Error): void {
  expect(Buffer.byteLength(error.message)).toBeLessThan(1024);
  // Compare with the escaped form without putting control characters in logs.
  for (const control of ["\n", "\r", "\t", "\x1b", "\u2028", "\u2029"]) {
    expect(error.message.includes(control)).toBe(false);
  }
}

describe("signing error messages", () => {
  it.each([
    ["", '""'],
    ["bad\nvalue\x00", '"bad\\nvalue\\u0000"'],
    ["A".repeat(64), `"${"A".repeat(64)}"`],
    ["A".repeat(65), `"${"A".repeat(64)}…(65 chars)"`],
  ])("preserves useful diagnostics for %j", (value, shown) => {
    expect(() => inferPrimaryType({ Root: value })).toThrow(
      `types["Root"] must be an array of field descriptors, got ${shown}`,
    );
  });

  it.each([
    ["struct value", { Root: hostile }],
    ["field descriptor", { Root: [hostile] }],
    ["struct name", { [hostile]: [] }],
    ["field name", { Root: [{ name: hostile, type: "address" }] }],
    ["field type", { Root: [{ name: "value", type: hostile }] }],
    ["array field name", { Root: [{ name: [hostile], type: "address" }] }],
    ["array field type", { Root: [{ name: "value", type: [hostile] }] }],
    ["object value", { Root: { toString: null } }],
  ])("bounds and escapes rejected %s", (_label, schema) => {
    let caught: unknown;
    try {
      inferPrimaryType(schema as Record<string, unknown>);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(PolicyViolation);
    expectSafeMessage(caught as Error);
  });

  it("does not invoke object conversion hooks to describe a rejected value", () => {
    const value = {
      toString() {
        throw new Error("untrusted toString called");
      },
      toJSON() {
        throw new Error("untrusted toJSON called");
      },
    };
    expect(() => inferPrimaryType({ Root: value })).toThrow(PolicyViolation);
  });

  it.each(["chainId", "verifyingContract", "validBefore", "validAfter"])(
    "bounds and escapes rejected %s values",
    (key) => {
      const isDomain = key in domain;
      let caught: unknown;
      try {
        check(
          policy,
          isDomain ? { ...domain, [key]: hostile } : domain,
          types,
          isDomain ? message : { ...message, [key]: hostile },
          { now },
        );
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(PolicyViolation);
      expectSafeMessage(caught as Error);
      expect((caught as Error).message).toContain("\\nFAKELOG");
      expect((caught as Error).message).toContain("chars)");
    },
  );
});

describe("x402 rejection messages", () => {
  // The signer binds every call to a catalog EIP-3009 route, so the baseline
  // payload is derived from the route itself rather than restated here.
  const route = resolveExpectedEip3009Route("eip155:56", "U");
  const x402Domain = {
    name: route.name,
    version: route.version,
    chainId: route.chainId,
    verifyingContract: route.address,
  };
  const x402Types = {
    EIP712Domain: [
      { name: "name", type: "string" },
      { name: "version", type: "string" },
      { name: "chainId", type: "uint256" },
      { name: "verifyingContract", type: "address" },
    ],
    ...types,
  };
  let directory: string;
  let signer: X402Signer;

  beforeAll(() => {
    directory = mkdtempSync(join(tmpdir(), "bnbagent-error-messages-"));
    const wallet = new EVMWalletProvider({
      password: "local-error-message-test-only",
      privateKey: `0x${"1".repeat(64)}`,
      walletsDir: directory,
    });
    signer = new X402Signer(wallet, { sessionBudget: { [token]: 10n } });
  });

  afterAll(() => rmSync(directory, { recursive: true, force: true }));

  it.each([
    "types",
    "chainId",
    "verifyingContract",
    "validBefore",
    "validAfter",
    "to",
    "from",
    "value",
    "expectedTo",
  ])(
    "keeps the error and cause safe for %s and spends no budget",
    async (key) => {
      const to = `0x${"2".repeat(40)}`;
      const options: SignPaymentOptions = {
        domain: { ...x402Domain },
        types: { ...x402Types },
        message: {
          ...message,
          from: signer.walletAddress,
          to,
          value: 1,
          nonce: `0x${"3".repeat(64)}`,
        },
        expectedRoute: route,
        expectedTo: to,
      };
      if (key === "types") {
        options.types = {
          Root: hostile,
        } as unknown as SignPaymentOptions["types"];
      } else if (key in x402Domain) {
        options.domain[key] = hostile;
      } else if (key === "expectedTo") {
        options.expectedTo = hostile;
      } else {
        options.message[key] = hostile;
      }
      let caught: unknown;
      try {
        await signer.signPayment(options);
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(X402SignerError);
      const error = caught as Error;
      expectSafeMessage(error);
      if (["types", "chainId"].includes(key)) {
        // The EIP-3009 route binding rejects a substituted domain or primary
        // type before the wallet is reached, so no untrusted value is
        // described at all and there is nothing to chain as a cause.
        expect(error).toBeInstanceOf(X402PolicyError);
        expect(error.cause).toBeUndefined();
      }
      if (["validBefore", "validAfter"].includes(key)) {
        expect(error.cause).toBeInstanceOf(PolicyViolation);
        expect((error.cause as Error).message).toBe(error.message);
      }
      if (error.cause !== undefined) {
        expect(error.cause).toBeInstanceOf(Error);
        expectSafeMessage(error.cause as Error);
      }
      if (["verifyingContract", "value"].includes(key)) {
        expect(error.cause).toBeInstanceOf(Error);
        expect((error.cause as Error).cause).toBeUndefined();
      }
      expect(signer.budget.spent(token)).toBe(0n);
    },
  );
});
