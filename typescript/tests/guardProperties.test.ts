/**
 * Property tests for the x402 guard surfaces.
 *
 * Port of `python/tests/test_guard_properties.py`. These state the invariants
 * the guards exist to uphold and let fast-check search for a counterexample, so
 * the *class* of bug behind SRC-1314 (a one-sided bound check on an
 * externally-influenced number) cannot come back through a surface nobody wrote
 * a unit test for.
 *
 * Two invariants, deliberately decoupled from any individual check so that new
 * guards get covered without editing this file:
 *
 * 1. A rejected call must not change any counter.
 * 2. An accepted amount must land inside `[0, cap]`.
 *
 * A duck-typed `TypedDataSigner` stands in for a real `EVMWalletProvider`: key
 * derivation costs ~700ms, which a property test cannot afford per example, and
 * the guards under test run before the wallet is ever touched.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { BSC_MAINNET_CHAIN_ID, getAddress } from "../src/networks/index.js";
import type { SignatureResult } from "../src/wallets/walletProvider.js";
import { SessionBudgetTracker } from "../src/x402/budget.js";
import {
  X402BudgetExhaustedError,
  X402SignerError,
} from "../src/x402/errors.js";
import type { TypedDataSigner } from "../src/x402/signer.js";
import { X402Signer } from "../src/x402/signer.js";

const U = getAddress(BSC_MAINNET_CHAIN_ID).paymentToken;

/**
 * Values an attacker can put in a 402 response body. `bigint` has no width, so
 * "too large for uint256" and "negative" are both reachable.
 */
const hostileAmount = fc.oneof(
  fc.bigInt({ min: -(10n ** 30n), max: 10n ** 30n }),
  fc.constantFrom(0n, -1n, 1n, 2n ** 256n, -(2n ** 256n), 2n ** 256n - 1n),
);

const cap = fc.bigInt({ min: 0n, max: 10n ** 24n });

// ── SessionBudgetTracker ──────────────────────────────────────────────────

describe("SessionBudgetTracker invariants", () => {
  it("keeps the counter within [0, cap] for any sequence of amounts", () => {
    // This is the invariant SRC-1314 broke. A negative amount made
    // `cur + amount` smaller, so the one-sided `> cap` test passed and the
    // counter went negative — after which the cap stopped binding entirely.
    fc.assert(
      fc.property(
        cap,
        fc.array(hostileAmount, { maxLength: 8 }),
        (capValue, amounts) => {
          const t = new SessionBudgetTracker({ [U]: capValue });
          for (const amount of amounts) {
            const before = t.spent(U);
            try {
              t.reserve(U, amount);
            } catch (e) {
              if (!(e instanceof X402BudgetExhaustedError)) throw e;
              expect(t.spent(U)).toBe(before); // rejected → unchanged
              continue;
            }
            expect(t.spent(U) >= 0n).toBe(true);
            expect(t.spent(U) <= capValue).toBe(true);
          }
        },
      ),
      { numRuns: 300 },
    );
  });

  it("never lets an uncapped counter go negative", () => {
    // No cap means unlimited spend, not a counter that can be driven negative.
    // A negative counter is latent damage: the moment a cap is introduced, or
    // the tracker is shared with a capped path, it inherits the debt.
    fc.assert(
      fc.property(fc.array(hostileAmount, { maxLength: 8 }), (amounts) => {
        const t = new SessionBudgetTracker();
        for (const amount of amounts) {
          try {
            t.reserve(U, amount);
          } catch (e) {
            if (!(e instanceof X402BudgetExhaustedError)) throw e;
            continue;
          }
          expect(t.spent(U) >= 0n).toBe(true);
        }
      }),
      { numRuns: 300 },
    );
  });

  it("never lets rollback drive the counter out of range", () => {
    fc.assert(
      fc.property(
        cap,
        fc.bigInt({ min: 0n, max: 10n ** 24n }),
        hostileAmount,
        (capValue, reserved, rolledBack) => {
          const t = new SessionBudgetTracker({ [U]: capValue });
          try {
            t.reserve(U, reserved);
          } catch {
            /* over cap is fine here */
          }
          t.rollback(U, rolledBack);
          expect(t.spent(U) >= 0n).toBe(true);
        },
      ),
      { numRuns: 300 },
    );
  });
});

// ── X402Signer amount guards ──────────────────────────────────────────────

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

const TO = `0x${"b".repeat(40)}`;

function fakeSigner(): TypedDataSigner & { signed: bigint[] } {
  const signed: bigint[] = [];
  return {
    signed,
    address: `0x${"a".repeat(40)}`,
    async signTypedData(
      _domain: Record<string, unknown>,
      _types: Record<string, { name: string; type: string }[]>,
      message: Record<string, unknown>,
    ): Promise<SignatureResult> {
      signed.push(BigInt(message.value as bigint));
      return {
        messageHash: `0x${"0".repeat(64)}`,
        r: `0x${"0".repeat(64)}`,
        s: `0x${"0".repeat(64)}`,
        v: 27n,
        signature: `0x${"1".repeat(130)}`,
      };
    },
  };
}

function payload(from: string, value: bigint) {
  const now = Math.floor(Date.now() / 1000);
  return {
    domain: {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U,
    },
    types: {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    },
    message: {
      from,
      to: TO,
      value,
      validAfter: now - 60,
      validBefore: now + 60,
      nonce: `0x${"c".repeat(64)}`,
    },
    expectedTo: TO,
  };
}

describe("X402Signer amount-guard invariants", () => {
  it("only ever signs amounts satisfying every advertised cap", async () => {
    // Stated against the wallet rather than against the guards: this stays
    // true when a new guard is added, and it fails if any existing guard is
    // bypassable.
    await fc.assert(
      fc.asyncProperty(
        fc.bigInt({ min: 0n, max: 10n ** 12n }),
        cap,
        fc.array(hostileAmount, { maxLength: 6 }),
        async (perCall, session, values) => {
          const fake = fakeSigner();
          const signer = new X402Signer(fake, {
            maxValuePerCall: { [U]: perCall },
            sessionBudget: { [U]: session },
          });
          for (const value of values) {
            const before = signer.budget.spent(U);
            try {
              await signer.signPayment(payload(fake.address, value));
            } catch (e) {
              if (!(e instanceof X402SignerError)) throw e;
              expect(signer.budget.spent(U)).toBe(before);
              continue;
            }
            expect(signer.budget.spent(U) >= 0n).toBe(true);
          }
          for (const s of fake.signed) {
            expect(s >= 0n && s <= perCall).toBe(true);
          }
          expect(fake.signed.reduce((a, b) => a + b, 0n) <= session).toBe(true);
        },
      ),
      { numRuns: 150 },
    );
  });

  it("never lets a negative value reach the wallet", async () => {
    // Deliberately not asserting that non-negative values are accepted — an
    // over-cap positive is *supposed* to be refused, so "did it throw" is the
    // wrong question. The invariant is about what gets through.
    await fc.assert(
      fc.asyncProperty(hostileAmount, async (value) => {
        const fake = fakeSigner();
        const signer = new X402Signer(fake, {
          maxValuePerCall: { [U]: 10n ** 9n },
          sessionBudget: { [U]: 10n ** 12n },
        });
        try {
          await signer.signPayment(payload(fake.address, value));
        } catch (e) {
          if (!(e instanceof X402SignerError)) throw e;
        }
        if (value < 0n) expect(fake.signed).toEqual([]);
        expect(fake.signed.every((s) => s >= 0n)).toBe(true);
      }),
      { numRuns: 200 },
    );
  });
});

// ── Guards that silently vanish when unconfigured (audit finding E) ───────

describe("unconfigured guards", () => {
  it("enforces nothing numeric when constructed with no caps", async () => {
    // Documents the current, deliberate behaviour so a change is visible.
    // `new X402Signer(wallet)` applies no per-call cap and no session budget.
    // The recipient and signer-binding guards still run; the amount guards do
    // not exist. Anyone reading the class name would not guess this, which is
    // why the audit flags it.
    const fake = fakeSigner();
    const signer = new X402Signer(fake);
    await signer.signPayment(payload(fake.address, 10n ** 30n));
    expect(fake.signed).toEqual([10n ** 30n]);
    expect(signer.budget.capFor(U)).toBeNull();
  });
});
