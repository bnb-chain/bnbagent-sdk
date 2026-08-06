/**
 * Tests that EVMWalletProvider.signTypedData enforces SigningPolicy.
 *
 * Port of `python/tests/test_wallet_policy_gating.py` (deferred from Task 13
 * until EVMWalletProvider existed to test against): complements
 * `signingPolicy.test.ts` (which tests the policy data structure) by
 * verifying the wallet-side wiring — default fail-closed, permissive
 * opt-out, and the `_DANGEROUS_signTypedDataNoPolicy` escape hatch.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getAddress as toChecksumAddress } from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BSC_MAINNET_CHAIN_ID, getAddress } from "../src/networks/index.js";
import { PolicyViolation, SigningPolicy } from "../src/signing/index.js";
import { EVMWalletProvider } from "../src/wallets/index.js";

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

beforeEach(() => {
  const root = mkdtempSync(join(tmpdir(), "bnbagent-wallet-gating-test-"));
  wdir = join(root, "wallets");
});

afterEach(() => {
  rmSync(wdir, { recursive: true, force: true });
});

/**
 * Build a U-token TransferWithAuthorization sign request within the strict
 * default validity bounds. Uses "now +- 60" by default so the
 * future-validity cap (900s) is not the failing factor.
 */
function uTokenTwaPayload(
  wallet: EVMWalletProvider,
  opts: { validAfter?: number; validBefore?: number } = {},
): [
  Record<string, unknown>,
  Record<string, { name: string; type: string }[]>,
  Record<string, unknown>,
] {
  const now = Math.floor(Date.now() / 1000);
  const validAfter = opts.validAfter ?? now - 60;
  const validBefore = opts.validBefore ?? now + 60;
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
  const message = {
    from: wallet.address,
    to: `0x${"b".repeat(40)}`,
    value: 1_000_000,
    validAfter,
    validBefore,
    nonce: `0x${"c".repeat(64)}`,
  };
  return [domain, types, message];
}

// ── Default wallet (strictDefault) ──────────────────────────────────────

describe("EVMWalletProvider — strictDefault policy", () => {
  it("the single most important happy path: default config + U-token TWA works", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const [domain, types, msg] = uTokenTwaPayload(wallet);
    const signed = await wallet.signTypedData(domain, types, msg);
    expect(signed.signature).toBeDefined();
    expect(signed.messageHash).toBeDefined();
  });

  it("rejects an unknown verifyingContract", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const [domain, types, msg] = uTokenTwaPayload(wallet);
    domain.verifyingContract = `0x${"1".repeat(40)}`;
    let caught: PolicyViolation | undefined;
    try {
      await wallet.signTypedData(domain, types, msg);
    } catch (e) {
      caught = e as PolicyViolation;
    }
    expect(caught).toBeInstanceOf(PolicyViolation);
    expect(caught?.message).toContain("not in allowlist");
    expect(caught?.primaryType).toBe("TransferWithAuthorization");
    expect(caught?.chainId).toBe(BSC_MAINNET_CHAIN_ID);
  });

  it("rejects EIP-2612 Permit even though U-token supports it on-chain (denylist)", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: BSC_MAINNET_CHAIN_ID,
      verifyingContract: U_MAINNET,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      Permit: [
        { name: "owner", type: "address" },
        { name: "spender", type: "address" },
        { name: "value", type: "uint256" },
        { name: "nonce", type: "uint256" },
        { name: "deadline", type: "uint256" },
      ],
    };
    const msg = {
      owner: wallet.address,
      spender: `0x${"b".repeat(40)}`,
      value: 2n ** 256n - 1n,
      nonce: 0,
      deadline: 2_000_000_000,
    };
    await expect(wallet.signTypedData(domain, types, msg)).rejects.toThrow(
      /denylisted/,
    );
  });

  it("rejects an excessive validity window", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const now = Math.floor(Date.now() / 1000);
    const [domain, types, msg] = uTokenTwaPayload(wallet, {
      validAfter: now,
      validBefore: now + 1200,
    });
    await expect(wallet.signTypedData(domain, types, msg)).rejects.toThrow(
      "validity window 1200s exceeds max 600s",
    );
  });
});

// ── Permissive opt-out ──────────────────────────────────────────────────

describe("EVMWalletProvider — permissive policy", () => {
  it("accepts an unknown domain/type combination", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
      signingPolicy: SigningPolicy.permissive(),
    });
    const domain = {
      name: "Whatever",
      version: "1",
      chainId: 999,
      verifyingContract: `0x${"f".repeat(40)}` as `0x${string}`,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      ExoticType: [{ name: "x", type: "uint256" }],
    };
    const signed = await wallet.signTypedData(domain, types, { x: 1 });
    expect(signed.signature).toBeDefined();
  });
});

// ── _DANGEROUS escape hatch ─────────────────────────────────────────────

describe("EVMWalletProvider — _DANGEROUS_signTypedDataNoPolicy", () => {
  it("signs anything and emits a console.warn with POLICY BYPASS", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const domain = {
      name: "Whatever",
      version: "1",
      chainId: 999,
      verifyingContract: `0x${"f".repeat(40)}` as `0x${string}`,
    };
    const types = {
      EIP712Domain: EIP712DOMAIN_FIELDS,
      Anything: [{ name: "x", type: "uint256" }],
    };
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const signed = await wallet._DANGEROUS_signTypedDataNoPolicy(
        domain,
        types,
        { x: 1 },
      );
      expect(signed.signature).toBeDefined();
      const matches = warnSpy.mock.calls.filter(([msg]) =>
        String(msg).includes("_DANGEROUS_sign_typed_data_no_policy"),
      );
      expect(matches.length).toBeGreaterThan(0);
      expect(String(matches[0]?.[0])).toContain("POLICY BYPASS");
    } finally {
      warnSpy.mockRestore();
    }
  });
});

// ── signingPolicy property + extend integration ─────────────────────────

describe("EVMWalletProvider — signingPolicy property + extend", () => {
  it("an extended policy accepts a caller-supplied domain", async () => {
    const customContract = `0x${"2".repeat(40)}`;
    const customChecksummed = toChecksumAddress(
      customContract as `0x${string}`,
    );
    const extended = SigningPolicy.strictDefault().extend({
      domainAllowlist: [[BSC_MAINNET_CHAIN_ID, customChecksummed]],
    });
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
      signingPolicy: extended,
    });
    const [domain, types, msg] = uTokenTwaPayload(wallet);
    domain.verifyingContract = customChecksummed;
    const signed = await wallet.signTypedData(domain, types, msg);
    expect(signed.signature).toBeDefined();
    expect(
      wallet.signingPolicy.domainAllowlist.has(
        `${BSC_MAINNET_CHAIN_ID}:${customChecksummed}`,
      ),
    ).toBe(true);
  });
});
