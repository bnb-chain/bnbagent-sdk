/**
 * Tests for the x402 package: X402Signer, SessionBudgetTracker,
 * paymentOptionFromCli / quoteFromCli.
 *
 * Ports `python/tests/test_x402_signer.py`. TWAK integration
 * (`python/bnbagent/x402/twak.py`) is out of scope — the capability-gate
 * test uses a plain duck-typed object reporting no `sign.typed_data`
 * capability instead of a real TWAKProvider.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getAddress as toChecksumAddress } from "viem";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { BSC_MAINNET_CHAIN_ID, getAddress } from "../src/networks/index.js";
import { PolicyViolation } from "../src/signing/index.js";
import { SIGN_TYPED_DATA } from "../src/wallets/capabilities.js";
import { UnsupportedWalletOperation } from "../src/wallets/errors.js";
import { EVMWalletProvider } from "../src/wallets/index.js";
import type { SignatureResult } from "../src/wallets/walletProvider.js";
import { SessionBudgetTracker } from "../src/x402/budget.js";
import {
  X402AmountExceededError,
  X402BudgetExhaustedError,
  X402PolicyError,
  X402RecipientMismatchError,
} from "../src/x402/errors.js";
import { paymentOptionFromCli, quoteFromCli } from "../src/x402/payer.js";
import type { TypedDataSigner } from "../src/x402/signer.js";
import { X402Signer } from "../src/x402/signer.js";

const PW = "test-secure-password-123";
const PK = `0x${"a".repeat(64)}` as const;

const U_MAINNET = getAddress(BSC_MAINNET_CHAIN_ID).paymentToken;

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

let wdir: string;
let wallet: EVMWalletProvider;
let signer: X402Signer;

beforeEach(() => {
  const root = mkdtempSync(join(tmpdir(), "bnbagent-x402-test-"));
  wdir = join(root, "wallets");
  wallet = new EVMWalletProvider({
    password: PW,
    privateKey: PK,
    walletsDir: wdir,
  });
  signer = new X402Signer(wallet, {
    maxValuePerCall: { [U_MAINNET]: 1_000_000n },
    sessionBudget: { [U_MAINNET]: 5_000_000n },
  });
});

afterEach(() => {
  rmSync(wdir, { recursive: true, force: true });
});

function payload(
  opts: {
    to?: string;
    value?: bigint;
    fromAddr?: string;
  } = {},
) {
  const now = Math.floor(Date.now() / 1000);
  return {
    domain: {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    },
    types: {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      TransferWithAuthorization: TWA_FIELDS,
    },
    message: {
      from: opts.fromAddr ?? `0x${"a".repeat(40)}`,
      to: opts.to ?? `0x${"b".repeat(40)}`,
      value: opts.value ?? 500_000n,
      validAfter: now - 60,
      validBefore: now + 60,
      nonce: `0x${"c".repeat(64)}`,
    },
  };
}

// ── Happy path ─────────────────────────────────────────────────────────

describe("X402Signer — happy path", () => {
  it("signs and increments spent for the U token within budget", async () => {
    const p = payload({ fromAddr: signer.walletAddress });
    const signed = await signer.signPayment({ ...p, expectedTo: p.message.to });
    expect(signed.signature).toBeDefined();
    expect(signer.budget.spent(U_MAINNET)).toBe(p.message.value);
  });
});

// ── Recipient mismatch ───────────────────────────────────────────────────

describe("X402Signer — recipient mismatch", () => {
  it("rejects when expectedTo differs, budget untouched", async () => {
    const p = payload({ to: `0x${"b".repeat(40)}` });
    await expect(
      signer.signPayment({ ...p, expectedTo: `0x${"9".repeat(40)}` }),
    ).rejects.toThrow(X402RecipientMismatchError);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("recipient check is case-insensitive", async () => {
    // message.to must be a viem-valid address (all-lowercase or properly
    // EIP-55 checksummed) since it flows through the wallet's typed-data
    // hashing; expectedTo is a plain caller-supplied string that never
    // touches viem's checksum validation, only a case-insensitive string
    // compare — so mismatched casing between the two exercises the guard
    // without producing an invalid on-chain address.
    const rawTo = `0x${"a".repeat(40)}`; // all-lowercase — viem-valid unchecksummed
    const checksummedTo = toChecksumAddress(rawTo as `0x${string}`);
    expect(checksummedTo).not.toBe(rawTo); // sanity: casing actually differs
    const p = payload({ to: rawTo, fromAddr: signer.walletAddress });
    const signed = await signer.signPayment({
      ...p,
      expectedTo: checksummedTo,
    });
    expect(signed.signature).toBeDefined();
  });

  it("rejects when message.to is missing", async () => {
    const p = payload();
    // biome-ignore lint/performance/noDelete: test needs a genuinely absent key
    delete (p.message as Record<string, unknown>).to;
    await expect(
      signer.signPayment({ ...p, expectedTo: `0x${"b".repeat(40)}` }),
    ).rejects.toThrow(/missing or not an address/);
  });
});

// ── Signer binding (message.from) ────────────────────────────────────────

describe("X402Signer — signer binding", () => {
  it("rejects a forged from before budget reserve", async () => {
    const p = payload({ fromAddr: `0x${"d".repeat(40)}` });
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(/does not match wallet/);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("rejects when message.from is missing", async () => {
    const p = payload({ fromAddr: signer.walletAddress });
    // biome-ignore lint/performance/noDelete: test needs a genuinely absent key
    delete (p.message as Record<string, unknown>).from;
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(/missing or not an address/);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });
});

// ── Per-call value cap ────────────────────────────────────────────────────

describe("X402Signer — per-call cap", () => {
  it("rejects when value exceeds maxValuePerCall", async () => {
    const p = payload({ value: 2_000_000n }); // cap is 1_000_000
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(/exceeds max_value_per_call/);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });
});

// ── Negative value (SRC-1314) ─────────────────────────────────────────────
// The per-call cap and the session budget are both one-sided comparisons
// (`value > cap`, `cur + amount > cap`), valid only under a `value >= 0`
// precondition that nothing used to assert. A negative value slipped past both
// and drove the session counter negative, neutralising the budget.

const POISONED_TWA_FIELDS = TWA_FIELDS.map((f) =>
  f.name === "value" ? { ...f, type: "int256" } : f,
);

describe("X402Signer — negative value (SRC-1314)", () => {
  it("rejects a negative value, budget untouched", async () => {
    const p = payload({ value: -1n });
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(/non-negative/);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("rejects a huge negative value, budget untouched", async () => {
    const p = payload({ value: -(10n ** 30n) });
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(X402AmountExceededError);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("rejects the full exploit chain: int256 schema plus a negative value", async () => {
    // Both defects had to align — under the canonical uint256 schema the
    // encoder rejects and the rollback path restores the counter, so the
    // corruption was transient. int256 made it persistent *and* produced a
    // real signature.
    const p = payload({
      value: -(10n ** 30n),
      fromAddr: signer.walletAddress,
    });
    await expect(
      signer.signPayment({
        ...p,
        types: {
          EIP712Domain: EIP712DOMAIN_FIELDS,
          TransferWithAuthorization: POISONED_TWA_FIELDS,
        },
        expectedTo: p.message.to,
      }),
    ).rejects.toThrow();
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("rejects an int256 schema through the x402 path even for a positive, in-cap value", async () => {
    // The field-shape pin standing alone: the amount guards have nothing to
    // object to here, so this only passes if the wallet's SigningPolicy is
    // genuinely reached — and the budget must be rolled back on the way out.
    const p = payload({ value: 500_000n, fromAddr: signer.walletAddress });
    await expect(
      signer.signPayment({
        ...p,
        types: {
          EIP712Domain: EIP712DOMAIN_FIELDS,
          TransferWithAuthorization: POISONED_TWA_FIELDS,
        },
        expectedTo: p.message.to,
      }),
    ).rejects.toThrow(X402PolicyError);
    expect(signer.budget.spent(U_MAINNET)).toBe(0n);
  });

  it("leaves the advertised session cap fully enforced after a rejected poison", async () => {
    const p = payload({
      value: -(10n ** 30n),
      fromAddr: signer.walletAddress,
    });
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(X402AmountExceededError);

    // 5 x 1_000_000 exactly exhausts the 5_000_000 session budget.
    for (let i = 0; i < 5; i++) {
      const q = payload({ value: 1_000_000n, fromAddr: signer.walletAddress });
      await signer.signPayment({ ...q, expectedTo: q.message.to });
    }
    expect(signer.budget.spent(U_MAINNET)).toBe(5_000_000n);

    const r = payload({ value: 1n, fromAddr: signer.walletAddress });
    await expect(
      signer.signPayment({ ...r, expectedTo: r.message.to }),
    ).rejects.toThrow(X402BudgetExhaustedError);
  });
});

// ── Session budget ────────────────────────────────────────────────────────

describe("X402Signer — session budget", () => {
  it("accumulates across calls", async () => {
    for (let i = 0; i < 5; i++) {
      const p = payload({ value: 500_000n, fromAddr: signer.walletAddress });
      await signer.signPayment({ ...p, expectedTo: p.message.to });
    }
    expect(signer.budget.spent(U_MAINNET)).toBe(2_500_000n);
  });

  it("blocks the next call once it would exceed the cap, exactly at cap", async () => {
    for (let i = 0; i < 5; i++) {
      const p = payload({ value: 1_000_000n, fromAddr: signer.walletAddress });
      await signer.signPayment({ ...p, expectedTo: p.message.to });
    }
    expect(signer.budget.spent(U_MAINNET)).toBe(5_000_000n);
    const p = payload({ value: 1n, fromAddr: signer.walletAddress });
    await expect(
      signer.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(X402BudgetExhaustedError);
  });

  it("does not consume budget when the underlying wallet's policy rejects", async () => {
    const badToken = `0x${"1".repeat(40)}` as `0x${string}`;
    const badSigner = new X402Signer(wallet, {
      maxValuePerCall: { [badToken]: 1_000_000n },
      sessionBudget: { [badToken]: 5_000_000n },
    });
    const p = payload({ value: 500_000n, fromAddr: badSigner.walletAddress });
    p.domain.verifyingContract = badToken; // not in wallet's SigningPolicy allowlist
    await expect(
      badSigner.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(X402PolicyError);
    expect(badSigner.budget.spent(toChecksumAddress(badToken))).toBe(0n);
  });
});

// ── PolicyViolation propagation ──────────────────────────────────────────

describe("X402Signer — PolicyViolation propagation", () => {
  it("wraps a wallet PolicyViolation as X402PolicyError with cause chained", async () => {
    const permitSigner = new X402Signer(wallet, {
      maxValuePerCall: { [U_MAINNET]: 1_000_000n },
    });
    const permitPayload = {
      domain: {
        name: "United Stables",
        version: "1",
        chainId: BSC_MAINNET_CHAIN_ID,
        verifyingContract: U_MAINNET,
      },
      types: {
        EIP712Domain: EIP712DOMAIN_FIELDS,
        Permit: [
          { name: "owner", type: "address" },
          { name: "spender", type: "address" },
          { name: "value", type: "uint256" },
          { name: "nonce", type: "uint256" },
          { name: "deadline", type: "uint256" },
        ],
      },
      message: {
        owner: `0x${"a".repeat(40)}`,
        spender: `0x${"b".repeat(40)}`,
        to: `0x${"b".repeat(40)}`,
        from: permitSigner.walletAddress,
        value: 500_000n,
        nonce: 0,
        deadline: 2_000_000_000,
      },
    };
    let caught: X402PolicyError | undefined;
    try {
      await permitSigner.signPayment({
        ...permitPayload,
        expectedTo: `0x${"b".repeat(40)}`,
      });
    } catch (e) {
      caught = e as X402PolicyError;
    }
    expect(caught).toBeInstanceOf(X402PolicyError);
    expect(caught?.cause).toBeInstanceOf(PolicyViolation);
    expect((caught?.cause as PolicyViolation).primaryType).toBe("Permit");
  });
});

// ── Rollback on non-policy failure ───────────────────────────────────────

describe("X402Signer — rollback on non-policy failure", () => {
  it("rolls back the reservation when the wallet throws a non-policy error", async () => {
    class Boom extends Error {}
    const duck: TypedDataSigner = {
      address: wallet.address,
      signTypedData: async () => {
        throw new Boom("transport failed");
      },
    };
    const s = new X402Signer(duck, {
      maxValuePerCall: { [U_MAINNET]: 1_000_000n },
      sessionBudget: { [U_MAINNET]: 1_000_000n },
    });
    const p = payload({ value: 500_000n, fromAddr: s.walletAddress });
    await expect(
      s.signPayment({ ...p, expectedTo: p.message.to }),
    ).rejects.toThrow(Boom);
    expect(s.budget.spent(U_MAINNET)).toBe(0n);
  });
});

// ── Composition-time capability gate ─────────────────────────────────────

describe("X402Signer — composition-time capability gate", () => {
  it("throws UnsupportedWalletOperation at construction for a wallet without sign.typed_data, zero side effects", () => {
    let supportsCalls = 0;
    let signCalls = 0;
    const noTypedData: TypedDataSigner = {
      address: `0x${"e".repeat(40)}`,
      signTypedData: async () => {
        signCalls++;
        throw new Error("should never be called");
      },
      supports: (cap: string) => {
        supportsCalls++;
        return cap !== SIGN_TYPED_DATA;
      },
    };
    expect(() => new X402Signer(noTypedData)).toThrow(
      UnsupportedWalletOperation,
    );
    expect(() => new X402Signer(noTypedData)).toThrow(/delegated x402 payer/);
    // Two throwing constructions above = two supports() calls total, but
    // signTypedData is never invoked — the gate fires before any payment
    // flow, with zero side effects on the wallet.
    expect(supportsCalls).toBe(2);
    expect(signCalls).toBe(0);
  });

  it("passes a duck-typed signer without supports() and can sign", async () => {
    const duck: TypedDataSigner = {
      address: `0x${"a".repeat(40)}`,
      signTypedData: async (): Promise<SignatureResult> => ({
        messageHash: `0x${"0".repeat(64)}`,
        r: `0x${"0".repeat(64)}`,
        s: `0x${"0".repeat(64)}`,
        v: 27n,
        signature: `0x${"1".repeat(130)}`,
      }),
    };
    const s = new X402Signer(duck, {
      maxValuePerCall: { [U_MAINNET]: 1_000_000n },
      sessionBudget: { [U_MAINNET]: 5_000_000n },
    });
    const p = payload({ fromAddr: s.walletAddress });
    const signed = await s.signPayment({ ...p, expectedTo: p.message.to });
    expect(signed.signature).toBeDefined();
    expect(s.budget.spent(U_MAINNET)).toBe(p.message.value);
  });
});

// ── Interleaved-async budget correctness ─────────────────────────────────

describe("X402Signer — interleaved-async budget correctness", () => {
  it("lets exactly one of two concurrent signPayment calls through", async () => {
    const cap = 1_000_000n;
    let signCalls = 0;
    const slow: TypedDataSigner = {
      address: wallet.address,
      signTypedData: async (domain, types, message) => {
        signCalls++;
        // Widen the interleave window: yield control back to the event
        // loop so a second, concurrently-started signPayment call gets a
        // chance to run before this one resolves.
        await new Promise((resolve) => setTimeout(resolve, 20));
        return wallet.signTypedData(domain, types, message);
      },
    };
    const s = new X402Signer(slow, {
      maxValuePerCall: { [U_MAINNET]: cap },
      sessionBudget: { [U_MAINNET]: cap }, // exactly one call fits
    });

    const attempt = async () => {
      const p = payload({ value: cap, fromAddr: s.walletAddress });
      try {
        const res = await s.signPayment({ ...p, expectedTo: p.message.to });
        return { outcome: "signed" as const, res };
      } catch (e) {
        if (e instanceof X402BudgetExhaustedError) {
          return { outcome: "rejected" as const, e };
        }
        throw e;
      }
    };

    const [a, b] = await Promise.all([attempt(), attempt()]);
    const outcomes = [a.outcome, b.outcome].sort();
    expect(outcomes).toEqual(["rejected", "signed"]);
    expect(s.budget.spent(U_MAINNET)).toBe(cap);
    expect(signCalls).toBe(1);
  });
});

// ── SessionBudgetTracker unit tests ──────────────────────────────────────

describe("SessionBudgetTracker", () => {
  it("caches capFor / spent with checksum normalization", () => {
    const lower = U_MAINNET.toLowerCase();
    const tracker = new SessionBudgetTracker({ [lower]: 10n });
    expect(tracker.capFor(U_MAINNET)).toBe(10n);
    expect(tracker.capFor(lower)).toBe(10n);
    expect(tracker.spent(U_MAINNET)).toBe(0n);
  });

  it("returns null cap for an unconfigured token", () => {
    const tracker = new SessionBudgetTracker();
    expect(tracker.capFor(U_MAINNET)).toBeNull();
  });

  it("reserve increments spent and throws once it would exceed the cap", () => {
    const tracker = new SessionBudgetTracker({ [U_MAINNET]: 100n });
    tracker.reserve(U_MAINNET, 60n);
    expect(tracker.spent(U_MAINNET)).toBe(60n);
    tracker.reserve(U_MAINNET, 40n);
    expect(tracker.spent(U_MAINNET)).toBe(100n);
    expect(() => tracker.reserve(U_MAINNET, 1n)).toThrow(
      X402BudgetExhaustedError,
    );
    expect(() => tracker.reserve(U_MAINNET, 1n)).toThrow(
      /would exceed session budget/,
    );
    expect(tracker.spent(U_MAINNET)).toBe(100n); // rejected reserve didn't mutate
  });

  it("rollback decrements and floors at zero without throwing", () => {
    const tracker = new SessionBudgetTracker({ [U_MAINNET]: 100n });
    tracker.reserve(U_MAINNET, 30n);
    tracker.rollback(U_MAINNET, 10n);
    expect(tracker.spent(U_MAINNET)).toBe(20n);
    expect(() => tracker.rollback(U_MAINNET, 1000n)).not.toThrow();
    expect(tracker.spent(U_MAINNET)).toBe(0n);
  });

  it("unlimited (uncapped) token never throws on reserve", () => {
    const tracker = new SessionBudgetTracker();
    expect(() => tracker.reserve(U_MAINNET, 10n ** 30n)).not.toThrow();
  });

  // SRC-1314: the "counter must never go negative" invariant was installed on
  // rollback() (where it is merely defensive) but not on reserve() (where it is
  // load-bearing). Assert it directly on the tracker.
  it("reserve rejects a negative amount without mutating spent", () => {
    const tracker = new SessionBudgetTracker({ [U_MAINNET]: 1_000n });
    expect(() => tracker.reserve(U_MAINNET, -1n)).toThrow(
      X402BudgetExhaustedError,
    );
    expect(() => tracker.reserve(U_MAINNET, -1n)).toThrow(/non-negative/);
    expect(tracker.spent(U_MAINNET)).toBe(0n);
  });

  it("reserve rejects a negative amount even for an uncapped token", () => {
    // No cap to exceed, but the counter must still not go negative — a later
    // cap change would otherwise inherit the debt.
    const tracker = new SessionBudgetTracker();
    expect(() => tracker.reserve(U_MAINNET, -1n)).toThrow(/non-negative/);
    expect(tracker.spent(U_MAINNET)).toBe(0n);
  });
});

// ── payer.ts mapper unit tests ────────────────────────────────────────────

describe("paymentOptionFromCli", () => {
  it("maps a full camelCase entry", () => {
    const opt = paymentOptionFromCli({
      network: "eip155:56",
      scheme: "exact",
      asset: U_MAINNET,
      tokenName: "U",
      amount: "500000",
      payTo: `0x${"b".repeat(40)}`,
      transferMethod: "eip3009",
      maxTimeoutSeconds: 60,
      preferred: true,
      requiresApproval: true,
      description: "test route",
    });
    expect(opt).toEqual({
      network: "eip155:56",
      scheme: "exact",
      asset: U_MAINNET,
      tokenName: "U",
      amount: 500_000n,
      payTo: `0x${"b".repeat(40)}`,
      transferMethod: "eip3009",
      maxTimeoutSeconds: 60,
      preferred: true,
      requiresApproval: true,
      description: "test route",
    });
  });

  it("defaults missing optionals", () => {
    const opt = paymentOptionFromCli({
      network: "eip155:56",
      asset: U_MAINNET,
      amount: 1,
      payTo: `0x${"b".repeat(40)}`,
    });
    expect(opt.scheme).toBe("exact");
    expect(opt.preferred).toBe(false);
    expect(opt.requiresApproval).toBe(false);
    expect(opt.maxTimeoutSeconds).toBeNull();
    expect(opt.tokenName).toBeUndefined();
  });

  it("throws when network is missing", () => {
    expect(() =>
      paymentOptionFromCli({
        asset: U_MAINNET,
        amount: 1,
        payTo: `0x${"b".repeat(40)}`,
      }),
    ).toThrow(/network/);
  });

  it("throws when asset is missing", () => {
    expect(() =>
      paymentOptionFromCli({
        network: "eip155:56",
        amount: 1,
        payTo: `0x${"b".repeat(40)}`,
      }),
    ).toThrow(/asset/);
  });

  it("throws when payTo is missing", () => {
    expect(() =>
      paymentOptionFromCli({
        network: "eip155:56",
        asset: U_MAINNET,
        amount: 1,
      }),
    ).toThrow(/payTo/);
  });

  it("throws when amount is missing", () => {
    expect(() =>
      paymentOptionFromCli({
        network: "eip155:56",
        asset: U_MAINNET,
        payTo: `0x${"b".repeat(40)}`,
      }),
    ).toThrow(/amount/);
  });

  it("still maps a fully-valid entry correctly (round-trip)", () => {
    const opt = paymentOptionFromCli({
      network: "eip155:56",
      scheme: "exact",
      asset: U_MAINNET,
      tokenName: "U",
      amount: "500000",
      payTo: `0x${"b".repeat(40)}`,
      transferMethod: "eip3009",
      maxTimeoutSeconds: 60,
      preferred: true,
      requiresApproval: true,
      description: "test route",
    });
    expect(opt).toEqual({
      network: "eip155:56",
      scheme: "exact",
      asset: U_MAINNET,
      tokenName: "U",
      amount: 500_000n,
      payTo: `0x${"b".repeat(40)}`,
      transferMethod: "eip3009",
      maxTimeoutSeconds: 60,
      preferred: true,
      requiresApproval: true,
      description: "test route",
    });
  });
});

describe("quoteFromCli", () => {
  it("maps resource + accepts, defaults url to empty string", () => {
    const data = {
      resource: {
        url: "https://example.com/paid",
        description: "d",
        mimeType: "application/json",
      },
      accepts: [
        {
          network: "eip155:56",
          asset: U_MAINNET,
          amount: "1",
          payTo: `0x${"b".repeat(40)}`,
        },
      ],
      summary: "one route",
    };
    const quote = quoteFromCli(data);
    expect(quote.url).toBe("https://example.com/paid");
    expect(quote.description).toBe("d");
    expect(quote.mimeType).toBe("application/json");
    expect(quote.accepts).toHaveLength(1);
    expect(quote.accepts[0]?.amount).toBe(1n);
    expect(quote.summary).toBe("one route");
    expect(quote.raw).toBe(data);
  });

  it("defaults url to empty string and accepts to [] when absent", () => {
    const quote = quoteFromCli({});
    expect(quote.url).toBe("");
    expect(quote.accepts).toEqual([]);
  });
});
