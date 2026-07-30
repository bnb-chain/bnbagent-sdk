/**
 * Altana x402 integration (`src/wallets/altana/x402.ts` + the provider's
 * x402 surface): capability gating, the 402 pay loop (route selection,
 * caps, budget rollback, header plumbing), the >= 0.4.0 SDK gate, and the
 * one-time admin setup (checker approval, bounded Permit2 allowance).
 *
 * `@altananetwork/sdk` is mocked at the module level like the other altana
 * suites; HTTP is mocked via the payer's `fetchImpl` seam with real
 * `Response` objects.
 */

import { decodeFunctionData, getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { erc20Abi } from "../src/abis/erc20.js";
import { X402_PAY } from "../src/wallets/capabilities.js";
import { UnsupportedWalletOperation } from "../src/wallets/errors.js";
import {
  X402AmountExceededError,
  X402BudgetExhaustedError,
  X402NoPayableRouteError,
} from "../src/x402/errors.js";

const PERMIT2 = getAddress("0x000000000022D473030F116dDEE9F6B43aC78BA3");

const sdkMocks = vi.hoisted(() => {
  const executeMock = vi.fn();
  const createWalletMock = vi.fn();
  const signerFromPrivateKeyMock = vi.fn();
  const signX402PaymentMock = vi.fn();
  const signOrderTypedDataMock = vi.fn();
  const approveSignatureCheckerMock = vi.fn();
  const revokeSignatureCheckerMock = vi.fn();
  const approveTokenForPermit2Mock = vi.fn();
  // Flipped off to model a pre-x402 (0.3.3) install.
  const x402Surface = { enabled: true };
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: vi.fn(),
    revokeSession: vi.fn(),
    // signX402Payment is module-level in the real 0.4.0 SDK (positional
    // params), so it is NOT on the client mock.
    ...(x402Surface.enabled
      ? {
          signOrderTypedData: signOrderTypedDataMock,
          approveSignatureChecker: approveSignatureCheckerMock,
          revokeSignatureChecker: revokeSignatureCheckerMock,
          approveTokenForPermit2: approveTokenForPermit2Mock,
        }
      : {}),
  }));
  return {
    executeMock,
    createWalletMock,
    signerFromPrivateKeyMock,
    signX402PaymentMock,
    signOrderTypedDataMock,
    approveSignatureCheckerMock,
    revokeSignatureCheckerMock,
    approveTokenForPermit2Mock,
    createClientMock,
    x402Surface,
  };
});

vi.mock("@altananetwork/sdk", async () => {
  const { privateKeyToAccount: toAccount } = await import("viem/accounts");
  sdkMocks.signerFromPrivateKeyMock.mockImplementation(
    (privateKey: `0x${string}`) => {
      const account = toAccount(privateKey);
      return {
        type: "privateKey",
        address: account.address,
        publicKey: account.publicKey,
        signDigest: async () => `0x${"11".repeat(65)}`,
      };
    },
  );
  return {
    createClient: sdkMocks.createClientMock,
    signerFromPrivateKey: sdkMocks.signerFromPrivateKeyMock,
    BNB: { chainId: 56 },
    PERMIT2_ADDRESS: "0x000000000022D473030F116dDEE9F6B43aC78BA3",
    // Module-level in 0.4.0: signX402Payment(session, requirement, opts?).
    signX402Payment: (...args: unknown[]) =>
      sdkMocks.signX402PaymentMock(...args),
  };
});

const { AltanaWalletProvider } = await import(
  "../src/wallets/altana/provider.js"
);
type AltanaSessionT = import("../src/wallets/altana/types.js").AltanaSession;
type AltanaSignerT = import("../src/wallets/altana/types.js").AltanaSigner;

const ADMIN_PK: `0x${string}` = `0x${"a1".repeat(32)}`;
const SESSION_PK: `0x${string}` = `0x${"b2".repeat(32)}`;
const WALLET = getAddress(`0x${"11".repeat(20)}`);
const USDC = getAddress("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d");
const PAY_TO = getAddress(`0x${"33".repeat(20)}`);
const CALLS_ID: `0x${string}` = `0x${"ca".repeat(32)}`;
const EXPIRY = 4_102_444_800;

function fakeSession(): AltanaSessionT {
  const account = privateKeyToAccount(SESSION_PK);
  const signer = {
    type: "privateKey",
    address: account.address,
    publicKey: account.publicKey,
    signDigest: async () => `0x${"22".repeat(65)}` as const,
  };
  return {
    walletAddress: WALLET,
    signer: signer as unknown as AltanaSignerT,
    publicKey: account.publicKey,
    permissions: { calls: [{ to: USDC }], spend: [] },
    expiry: EXPIRY,
  };
}

function sessionProvider() {
  return new AltanaWalletProvider({ session: fakeSession() });
}

function adminProvider() {
  return new AltanaWalletProvider({ privateKey: ADMIN_PK });
}

// One raw permit2/B402 entry + decoys: wrong chain, and a cheaper
// eip3009 route on-chain that must LOSE to permit2 (BSC's peg USDC is not
// ERC-1271-aware, so the eip3009 rail cannot settle there).
const PERMIT2_ENTRY = {
  scheme: "permit2-exact",
  network: "eip155:56",
  maxAmountRequired: "5000",
  asset: USDC,
  payTo: PAY_TO,
  maxTimeoutSeconds: 300,
  extra: { name: "USDC" },
};
const CHALLENGE = {
  x402Version: 1,
  error: "payment required",
  accepts: [
    {
      scheme: "exact",
      network: "eip155:1",
      maxAmountRequired: "1",
      asset: USDC,
      payTo: PAY_TO,
    },
    PERMIT2_ENTRY,
    {
      scheme: "exact",
      network: "eip155:56",
      maxAmountRequired: "4000",
      asset: USDC,
      payTo: PAY_TO,
      transferMethod: "eip3009",
    },
  ],
};

function json402(body: unknown = CHALLENGE): Response {
  return new Response(JSON.stringify(body), { status: 402 });
}

function jsonOk(body: unknown, headers?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), { status: 200, headers });
}

/** Sequenced fetch mock; records (url, init) per call. */
function fetchQueue(...responses: Response[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const impl = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      const next = responses.shift();
      if (!next) {
        throw new Error("fetchQueue exhausted");
      }
      return next;
    },
  );
  return { impl: impl as unknown as typeof fetch, calls };
}

beforeEach(() => {
  sdkMocks.executeMock.mockReset();
  sdkMocks.createWalletMock.mockReset();
  sdkMocks.signX402PaymentMock.mockReset();
  sdkMocks.approveSignatureCheckerMock.mockReset();
  sdkMocks.revokeSignatureCheckerMock.mockReset();
  sdkMocks.approveTokenForPermit2Mock.mockReset();
  sdkMocks.createClientMock.mockClear();
  sdkMocks.x402Surface.enabled = true;
  sdkMocks.createWalletMock.mockImplementation(
    async ({ signer }: { signer: { address: `0x${string}` } }) => ({
      address: signer.address,
      signer,
    }),
  );
  sdkMocks.signX402PaymentMock.mockResolvedValue({ header: "xp-header" });
  const confirmed = {
    callsId: CALLS_ID,
    status: "CONFIRMED",
    transactionHash: `0x${"fe".repeat(32)}`,
  };
  sdkMocks.executeMock.mockResolvedValue(confirmed);
  sdkMocks.approveSignatureCheckerMock.mockResolvedValue(confirmed);
  sdkMocks.revokeSignatureCheckerMock.mockResolvedValue(confirmed);
  sdkMocks.approveTokenForPermit2Mock.mockResolvedValue(confirmed);
});

describe("x402 capability gating", () => {
  it("session mode declares x402.pay; admin mode does not", () => {
    expect(sessionProvider().supports(X402_PAY)).toBe(true);
    expect(adminProvider().supports(X402_PAY)).toBe(false);
  });

  it("makeX402Payer on an admin provider refuses with the session path", () => {
    expect(() => adminProvider().makeX402Payer()).toThrow(
      UnsupportedWalletOperation,
    );
    expect(() => adminProvider().makeX402Payer()).toThrow(/session/);
  });
});

describe("AltanaX402Payer.request", () => {
  it("pays the permit2 route: signs the validated raw entry, retries with X-PAYMENT, reports settlement", async () => {
    const settlement = Buffer.from(
      JSON.stringify({ transaction: `0x${"ab".repeat(32)}` }),
    ).toString("base64");
    const { impl, calls } = fetchQueue(
      json402(),
      jsonOk({ data: "paid" }, { "X-PAYMENT-RESPONSE": settlement }),
    );
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    const result = await payer.request("https://api.example/paid", {
      maxPayment: 10_000n,
    });

    expect(result.success).toBe(true);
    expect(result.response).toEqual({ data: "paid" });
    expect(result.amount).toBe(5000n);
    expect(result.asset).toBe(USDC);
    expect(result.payTo).toBe(PAY_TO);
    expect(result.transaction).toBe(`0x${"ab".repeat(32)}`);
    // The signed requirement is the raw permit2 entry this payer validated
    // (never the cheaper eip3009 decoy, never a re-fetched challenge).
    // 0.4.0 shape: module-level signX402Payment(session, requirement).
    expect(sdkMocks.signX402PaymentMock).toHaveBeenCalledTimes(1);
    const [session, requirement] = sdkMocks.signX402PaymentMock.mock.calls[0];
    expect(requirement).toEqual(PERMIT2_ENTRY);
    expect(session.walletAddress).toBe(WALLET);
    // Retry carries the SDK-produced header verbatim.
    expect(calls).toHaveLength(2);
    expect(
      (calls[1].init?.headers as Record<string, string>)["X-PAYMENT"],
    ).toBe("xp-header");
  });

  it("returns without paying when the endpoint does not challenge", async () => {
    const { impl, calls } = fetchQueue(jsonOk({ cached: true }));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    const result = await payer.request("https://api.example/paid", {
      maxPayment: 1n,
    });
    expect(result).toEqual({ success: true, response: { cached: true } });
    expect(calls).toHaveLength(1);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("rejects a route above maxPayment before signing", async () => {
    const { impl } = fetchQueue(json402());
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 4_999n }),
    ).rejects.toThrow(X402AmountExceededError);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("rejects a negative quoted amount before signing (SRC-1314 class)", async () => {
    // The quoted amount is untrusted and is handed to budget.reserve(). This
    // path has no ABI encoder downstream, so a negative that got past the
    // precheck would poison the session counter for good — the payment
    // succeeds, so nothing rolls it back.
    const negative = {
      ...CHALLENGE,
      accepts: [{ ...PERMIT2_ENTRY, maxAmountRequired: "-1000000000000" }],
    };
    const { impl } = fetchQueue(json402(negative));
    const provider = sessionProvider();
    const payer = provider.makeX402Payer({ fetchImpl: impl });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402AmountExceededError);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("throws NoPayableRoute when no accepts entry is on this chain", async () => {
    const offChain = {
      ...CHALLENGE,
      accepts: [{ ...PERMIT2_ENTRY, network: "eip155:8453" }],
    };
    const { impl } = fetchQueue(json402(offChain));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402NoPayableRouteError);
  });

  it("enforces the cumulative sessionBudget across requests", async () => {
    const { impl } = fetchQueue(json402(), jsonOk({ n: 1 }), json402());
    const payer = sessionProvider().makeX402Payer({
      fetchImpl: impl,
      sessionBudget: { [USDC]: 6_000n },
    });
    await payer.request("https://api.example/paid", { maxPayment: 10_000n });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402BudgetExhaustedError);
    expect(sdkMocks.signX402PaymentMock).toHaveBeenCalledTimes(1);
  });

  it("rolls the budget back when the paid retry fails", async () => {
    const { impl } = fetchQueue(
      json402(),
      new Response("boom", { status: 500 }),
      json402(),
      jsonOk({ ok: true }),
    );
    const payer = sessionProvider().makeX402Payer({
      fetchImpl: impl,
      sessionBudget: { [USDC]: 5_000n },
    });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(/paid retry failed: HTTP 500/);
    // Reservation released: the full budget is available again.
    const result = await payer.request("https://api.example/paid", {
      maxPayment: 10_000n,
    });
    expect(result.success).toBe(true);
  });

  it("fails with the exact upgrade hint on a pre-x402 SDK install", async () => {
    sdkMocks.x402Surface.enabled = false;
    const { impl } = fetchQueue(json402());
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(/@altananetwork\/sdk' >= 0\.4\.0/);
  });
});

describe("AltanaX402Payer.quote", () => {
  it("parses a 402 challenge without paying", async () => {
    const { impl } = fetchQueue(json402());
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    const quote = await payer.quote("https://api.example/paid");
    expect(quote.accepts).toHaveLength(3);
    expect(quote.accepts[1].amount).toBe(5000n);
    expect(quote.accepts[1].tokenName).toBe("USDC");
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("returns empty accepts for a non-402 answer", async () => {
    const { impl } = fetchQueue(jsonOk({ free: true }));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    const quote = await payer.quote("https://api.example/free");
    expect(quote.accepts).toEqual([]);
    expect(quote.raw).toEqual({ status: 200 });
  });
});

describe("admin x402 setup", () => {
  it("approveQuoteSignatureChecker forwards the explicit Commerce checker", async () => {
    const provider = adminProvider();
    const session = fakeSession();

    await provider.approveQuoteSignatureChecker(session, USDC);

    const args = sdkMocks.approveSignatureCheckerMock.mock.calls[0][0];
    expect(args.checker).toBe(USDC);
    expect(args.session).toBe(session);
    expect(args.wallet.address).toBe(provider.address);
  });

  it("approveX402SignatureChecker defaults the checker to Permit2", async () => {
    const provider = adminProvider();
    const session = fakeSession();
    await provider.approveX402SignatureChecker(session);
    expect(sdkMocks.approveSignatureCheckerMock).toHaveBeenCalledTimes(1);
    const args = sdkMocks.approveSignatureCheckerMock.mock.calls[0][0];
    expect(getAddress(args.checker)).toBe(PERMIT2);
    expect(args.session).toBe(session);
    expect(args.wallet.address).toBe(provider.address);
  });

  it("revokeX402SignatureChecker forwards a custom checker", async () => {
    const provider = adminProvider();
    await provider.revokeX402SignatureChecker(fakeSession(), {
      checker: USDC,
    });
    const args = sdkMocks.revokeSignatureCheckerMock.mock.calls[0][0];
    expect(args.checker).toBe(USDC);
  });

  it("surfaces a FAILED relay result as an error", async () => {
    sdkMocks.approveSignatureCheckerMock.mockResolvedValue({
      callsId: CALLS_ID,
      status: "FAILED",
    });
    await expect(
      adminProvider().approveX402SignatureChecker(fakeSession()),
    ).rejects.toThrow(/FAILED.*approveSignatureChecker/);
  });

  it("setPermit2Allowance delegates a BOUNDED amount to the SDK's approveTokenForPermit2", async () => {
    const provider = adminProvider();
    await provider.setPermit2Allowance(USDC, 123_456n, { feeToken: USDC });
    expect(sdkMocks.approveTokenForPermit2Mock).toHaveBeenCalledTimes(1);
    const args = sdkMocks.approveTokenForPermit2Mock.mock.calls[0][0];
    expect(args.token).toBe(USDC);
    expect(args.amount).toBe(123_456n); // bounded, never unlimited
    expect(args.feeToken).toBe(USDC);
    expect(args.wallet.address).toBe(provider.address);
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  it("session-mode providers cannot run the admin setup", async () => {
    await expect(
      sessionProvider().approveX402SignatureChecker(fakeSession()),
    ).rejects.toThrow(/admin/);
    await expect(
      sessionProvider().setPermit2Allowance(USDC, 1n),
    ).rejects.toThrow(/admin/);
  });
});

// ── real-wire challenge shape (CMC B402, field-verified 2026-07-14) ──────

const U_BSC = getAddress("0xcE24439F2D9C6a2289F741120FE202248B666666");
const CMC_CHALLENGE = {
  x402Version: 2,
  accepts: [
    {
      scheme: "exact",
      network: "eip155:56",
      asset: USDC,
      payTo: PAY_TO,
      maxTimeoutSeconds: 30,
      extra: { name: "USD Coin", version: "1", assetTransferMethod: "eip3009" },
      amount: "20000000000000000",
    },
    {
      scheme: "exact",
      network: "eip155:56",
      asset: U_BSC,
      payTo: PAY_TO,
      maxTimeoutSeconds: 30,
      extra: {
        name: "United Stables",
        version: "1",
        assetTransferMethod: "permit2-exact",
        spenderAddress: `0x${"30".repeat(20)}`,
      },
      amount: "10000000000000000",
    },
    {
      scheme: "exact",
      network: "eip155:8453",
      asset: `0x${"83".repeat(20)}`,
      payTo: PAY_TO,
      maxTimeoutSeconds: 30,
      extra: { name: "USD Coin", version: "2", assetTransferMethod: "eip3009" },
      amount: "10000",
    },
  ],
};

describe("real B402 wire shape (extra.assetTransferMethod, expectedAsset pin)", () => {
  it("ranks the permit2-exact rail via extra.assetTransferMethod and signs that raw entry", async () => {
    const { impl } = fetchQueue(json402(CMC_CHALLENGE), jsonOk({ ok: true }));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    const result = await payer.request("https://pro-api.example/x402", {
      maxPayment: 10n ** 17n,
    });
    // The eip3009 route is FIRST in the challenge; the permit2-exact U
    // route must still win (rail read from extra.assetTransferMethod).
    expect(result.asset).toBe(U_BSC);
    const [, requirement] = sdkMocks.signX402PaymentMock.mock.calls[0];
    expect(requirement).toEqual(CMC_CHALLENGE.accepts[1]);
  });

  it("expectedAsset pins the token: only matching routes are payable", async () => {
    const { impl } = fetchQueue(json402(CMC_CHALLENGE), jsonOk({ ok: true }));
    const pinned = sessionProvider().makeX402Payer({
      fetchImpl: impl,
      expectedAsset: USDC,
    });
    const result = await pinned.request("https://pro-api.example/x402", {
      maxPayment: 10n ** 17n,
    });
    expect(result.asset).toBe(USDC);

    const { impl: impl2 } = fetchQueue(json402(CMC_CHALLENGE));
    const impossible = sessionProvider().makeX402Payer({
      fetchImpl: impl2,
      expectedAsset: `0x${"99".repeat(20)}`,
    });
    await expect(
      impossible.request("https://pro-api.example/x402", {
        maxPayment: 10n ** 17n,
      }),
    ).rejects.toThrow(X402NoPayableRouteError);
  });
});
