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

import { type PublicClient, decodeFunctionData, getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import { erc20Abi } from "../src/abis/erc20.js";
import { listAssets } from "../src/networks/assets.js";
import {
  PERMIT2_ALLOWANCE_ABI,
  findDelegationBlock,
} from "../src/wallets/altana/permit2.js";
import {
  PAYMENT_SIGNATURE_HEADER,
  X_PAYMENT_HEADER,
  normalizeX402PaymentHeader,
} from "../src/wallets/altana/x402.js";
import { X402_PAY } from "../src/wallets/capabilities.js";
import { UnsupportedWalletOperation } from "../src/wallets/errors.js";
import { SessionBudgetTracker } from "../src/x402/budget.js";
import {
  X402AmountExceededError,
  X402BudgetExhaustedError,
  X402NoPayableRouteError,
  X402RecipientMismatchError,
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
  const revokeSessionMock = vi.fn();
  // Flipped off to model a pre-x402 (0.3.3) install.
  const x402Surface = { enabled: true };
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: vi.fn(),
    revokeSession: revokeSessionMock,
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
    revokeSessionMock,
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
const B402_PROXY = getAddress(`0x${"44".repeat(20)}`);
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

/** Read client for the checker approval: current block 4 200, receipt in 4 242. */
function blockClient(): PublicClient {
  return {
    getBlockNumber: vi.fn(async () => 4_200n),
    getTransactionReceipt: vi.fn(async () => ({ blockNumber: 4_242n })),
  } as unknown as PublicClient;
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
  sdkMocks.revokeSessionMock.mockReset();
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
  sdkMocks.revokeSessionMock.mockResolvedValue(confirmed);
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
    // (never the cheaper eip3009 decoy, never a re-fetched challenge), plus
    // the challenge's top-level x402Version (no resource: this challenge
    // carries none). 0.4.0 shape: module-level signX402Payment(session,
    // requirement).
    expect(sdkMocks.signX402PaymentMock).toHaveBeenCalledTimes(1);
    const [session, requirement] = sdkMocks.signX402PaymentMock.mock.calls[0];
    expect(requirement).toEqual({ ...PERMIT2_ENTRY, x402Version: 1 });
    expect(session.walletAddress).toBe(WALLET);
    // Retry carries the SDK-produced header verbatim (opaque headers pass
    // normalization untouched) on BOTH header names — part of the b402
    // merchant population reads only PAYMENT-SIGNATURE.
    expect(calls).toHaveLength(2);
    const sentHeaders = calls[1].init?.headers as Record<string, string>;
    expect(sentHeaders[X_PAYMENT_HEADER]).toBe("xp-header");
    expect(sentHeaders[PAYMENT_SIGNATURE_HEADER]).toBe("xp-header");
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

  it("shares one session budget across payer instances", async () => {
    const shared = new SessionBudgetTracker({ [USDC]: 6_000n });
    const firstFetch = fetchQueue(json402(), jsonOk({ n: 1 }));
    const secondFetch = fetchQueue(json402());
    const first = sessionProvider().makeX402Payer({
      fetchImpl: firstFetch.impl,
      sessionBudget: shared,
    });
    const second = sessionProvider().makeX402Payer({
      fetchImpl: secondFetch.impl,
      sessionBudget: shared,
    });

    await first.request("https://api.example/paid", { maxPayment: 10_000n });
    await expect(
      second.request("https://api.example/paid", { maxPayment: 10_000n }),
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

describe("AltanaX402Payer.requestExact", () => {
  const exactResource = {
    url: "https://api.example/paid",
    description: "paid",
    mimeType: "application/json",
  };
  const exactEntry = {
    scheme: "exact",
    network: "eip155:56" as const,
    amount: "5000",
    asset: USDC,
    payTo: PAY_TO,
    maxTimeoutSeconds: 300,
    extra: {
      assetTransferMethod: "permit2-exact",
      name: "USD Coin",
      version: "1",
      signerAddress: WALLET,
      spenderAddress: B402_PROXY,
    },
  };
  const expectedRoute = {
    x402Version: 2 as const,
    scheme: "exact" as const,
    network: "eip155:56" as const,
    asset: USDC,
    amount: 5000n,
    payTo: PAY_TO,
    maxTimeoutSeconds: 300,
    transferMethod: "permit2-exact" as const,
    name: "USD Coin",
    version: "1",
    spenderAddress: B402_PROXY,
    trustedSpenders: [B402_PROXY],
    resource: exactResource,
  };

  it("validates and signs one exact Permit2 route from the same challenge", async () => {
    const challenge = {
      x402Version: 2,
      resource: exactResource,
      accepts: [exactEntry],
    };
    const { impl } = fetchQueue(json402(challenge), jsonOk({ data: "paid" }));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    const result = await payer.requestExact("https://api.example/paid", {
      expectedRoute,
      maxPayment: 5000n,
    });

    expect(result).toMatchObject({
      paid: true,
      success: true,
      amount: 5000n,
      asset: USDC,
      network: "eip155:56",
      payTo: PAY_TO,
      transferMethod: "permit2-exact",
      spenderAddress: B402_PROXY,
    });
    if (result.paid && result.transferMethod === "permit2-exact") {
      expectTypeOf(result.spenderAddress).toEqualTypeOf<string>();
    }
    expect(sdkMocks.signX402PaymentMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        ...exactEntry,
        x402Version: 2,
        resource: challenge.resource,
      }),
    );
  });

  it.each([
    ["amount", { amount: "5001" }],
    ["scheme", { scheme: "permit2-exact" }],
    ["missing scheme", { scheme: undefined }],
    ["unsafe numeric amount", { amount: 5000 }],
    ["conflicting top-level method", { transferMethod: "eip3009" }],
    ["x402Version", {}, 1],
    ["timeout", { maxTimeoutSeconds: 301 }],
    [
      "spender",
      {
        extra: {
          ...exactEntry.extra,
          spenderAddress: PAY_TO,
        },
      },
    ],
    [
      "domain",
      { extra: { ...exactEntry.extra, name: "forged", version: "2" } },
    ],
  ])(
    "rejects %s drift before signing",
    async (_label, entryPatch, version = 2) => {
      const { impl } = fetchQueue(
        json402({
          x402Version: version,
          resource: exactResource,
          accepts: [{ ...exactEntry, ...entryPatch }],
        }),
      );
      const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

      await expect(
        payer.requestExact("https://api.example/paid", {
          expectedRoute,
          maxPayment: 5000n,
        }),
      ).rejects.toThrow(X402NoPayableRouteError);
      expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
    },
  );

  it("rejects any signed resource drift before signing", async () => {
    const { impl } = fetchQueue(
      json402({
        x402Version: 2,
        resource: { ...exactResource, description: "drifted" },
        accepts: [exactEntry],
      }),
    );
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    await expect(
      payer.requestExact("https://api.example/paid", {
        expectedRoute,
        maxPayment: 5000n,
      }),
    ).rejects.toThrow(X402NoPayableRouteError);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("ignores undefined object properties when comparing canonical resources", async () => {
    const resource = { url: exactResource.url };
    const { impl } = fetchQueue(
      json402({ x402Version: 2, resource, accepts: [exactEntry] }),
      jsonOk({ data: "paid" }),
    );
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    const result = await payer.requestExact("https://api.example/paid", {
      expectedRoute: {
        ...expectedRoute,
        resource: { ...resource, description: undefined },
      },
      maxPayment: 5000n,
    });

    expect(result).toMatchObject({ paid: true, amount: 5000n });
    expect(sdkMocks.signX402PaymentMock).toHaveBeenCalledTimes(1);
  });

  it.each([false, 0, ""])(
    "returns a verbatim paid:false result for an unchallenged 2xx body %#",
    async (body) => {
      const response = new Response(
        typeof body === "string" ? body : JSON.stringify(body),
        { status: 200 },
      );
      const { impl } = fetchQueue(response);
      const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

      const result = await payer.requestExact("https://api.example/paid", {
        expectedRoute,
        maxPayment: 5000n,
      });

      expect(result).toEqual({ paid: false, cacheHit: true, response: body });
      expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
    },
  );

  it("does not reserve session budget for a paid:false cache hit", async () => {
    const { impl } = fetchQueue(
      jsonOk({ cached: true }),
      json402({
        x402Version: 2,
        resource: exactResource,
        accepts: [exactEntry],
      }),
      jsonOk({ paid: true }),
    );
    const payer = sessionProvider().makeX402Payer({
      fetchImpl: impl,
      sessionBudget: { [USDC]: 5000n },
    });

    await expect(
      payer.requestExact("https://api.example/paid", {
        expectedRoute,
        maxPayment: 5000n,
      }),
    ).resolves.toMatchObject({ paid: false, cacheHit: true });
    await expect(
      payer.requestExact("https://api.example/paid", {
        expectedRoute,
        maxPayment: 5000n,
      }),
    ).resolves.toMatchObject({ paid: true, amount: 5000n });
  });

  it("rejects an unchallenged non-2xx response", async () => {
    const { impl } = fetchQueue(new Response("down", { status: 503 }));
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    await expect(
      payer.requestExact("https://api.example/paid", {
        expectedRoute,
        maxPayment: 5000n,
      }),
    ).rejects.toThrow(/expected HTTP 402 or 2xx, got 503/);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
  });

  it("rejects an expected proxy outside the explicit trust root", async () => {
    const { impl } = fetchQueue(
      json402({ x402Version: 2, accepts: [exactEntry] }),
    );
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });

    await expect(
      payer.requestExact("https://api.example/paid", {
        expectedRoute: { ...expectedRoute, trustedSpenders: [PAY_TO] },
        maxPayment: 5000n,
      }),
    ).rejects.toThrow(X402NoPayableRouteError);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();
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
    await provider.approveX402SignatureChecker(session, {
      client: blockClient(),
    });
    expect(sdkMocks.approveSignatureCheckerMock).toHaveBeenCalledTimes(1);
    const args = sdkMocks.approveSignatureCheckerMock.mock.calls[0][0];
    expect(getAddress(args.checker)).toBe(PERMIT2);
    expect(args.session).toBe(session);
    expect(args.wallet.address).toBe(provider.address);
  });

  it("approveX402SignatureChecker reports the approval block for the later Permit2 cleanup (SRC-1609)", async () => {
    const client = blockClient();

    const result = await adminProvider().approveX402SignatureChecker(
      fakeSession(),
      { client },
    );

    expect(result.blockNumber).toBe(4_242n);
    expect(client.getTransactionReceipt).toHaveBeenCalledWith({
      hash: `0x${"fe".repeat(32)}`,
    });
  });

  it("approveX402SignatureChecker falls back to the block before submitting when the receipt cannot be read (audit M03)", async () => {
    const client = blockClient();
    vi.mocked(client.getTransactionReceipt).mockRejectedValueOnce(
      new Error("rpc down"),
    );

    const result = await adminProvider().approveX402SignatureChecker(
      fakeSession(),
      { client },
    );

    // the approval already landed; the earlier block only widens the scan
    expect(result.status).toBe("CONFIRMED");
    expect(result.blockNumber).toBe(4_200n);
  });

  it("approveX402SignatureChecker submits nothing when it cannot read the current block (audit M03)", async () => {
    const client = blockClient();
    vi.mocked(client.getBlockNumber).mockRejectedValueOnce(
      new Error("rpc down"),
    );

    await expect(
      adminProvider().approveX402SignatureChecker(fakeSession(), { client }),
    ).rejects.toThrow(
      /could not read the current block.*nothing was submitted/,
    );
    expect(sdkMocks.approveSignatureCheckerMock).not.toHaveBeenCalled();
  });

  it("revokeX402SignatureChecker forwards a custom checker", async () => {
    const provider = adminProvider();
    await provider.revokeX402SignatureChecker(fakeSession(), {
      checker: USDC,
      permit2Reset: false,
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
      adminProvider().approveX402SignatureChecker(fakeSession(), {
        client: blockClient(),
      }),
    ).rejects.toThrow(/FAILED.*approveSignatureChecker/);
  });

  it("setPermit2Allowance delegates a BOUNDED amount to the SDK's approveTokenForPermit2", async () => {
    const provider = adminProvider();
    await provider.setPermit2Allowance(USDC, 123_456n, {
      feeToken: USDC,
      skipPermit2Cleanup: true,
    });
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
      sessionProvider().setPermit2Allowance(USDC, 1n, {
        skipPermit2Cleanup: true,
      }),
    ).rejects.toThrow(/admin/);
  });
});

// ── Permit2 allowance cleanup (SRC-1609) ─────────────────────────────────

const ATTACKER = getAddress(`0x${"a7".repeat(20)}`);
const ROUTER = getAddress(`0x${"b8".repeat(20)}`);
const U_TOKEN = getAddress("0xcE24439F2D9C6a2289F741120FE202248B666666");
const MAX_UINT160 = 2n ** 160n - 1n;

type FakeLog =
  | {
      event: "Permit" | "Approval";
      block: bigint;
      token: `0x${string}`;
      spender: `0x${string}`;
    }
  | {
      event: "SignatureCheckerApprovalSet";
      block: bigint;
      isApproved: boolean;
    };

/** IthacaAccount keyHash of SESSION_PK's secp256k1 session key. */
const SESSION_KEY_HASH =
  "0xea55f7f59da1d578e72484420b656338d05351eee17640290f654e39bb58cf6d";

/**
 * A read client over fixed Permit2 logs, the wallet's checker approvals,
 * stored allowances, the wallet's outer Permit2 approvals by token, and
 * the block of its EIP-7702 delegation (it has code from then on).
 */
function permit2Chain(
  logs: FakeLog[],
  allowances: Record<string, bigint>,
  outer: Record<string, bigint> = {},
  delegatedAt = 0n,
) {
  const getLogs = vi.fn(
    async (q: {
      address: `0x${string}`;
      event: { name: string };
      args: {
        owner?: `0x${string}`;
        token?: `0x${string}`[];
        keyHash?: `0x${string}`;
        checker?: `0x${string}`;
      };
      fromBlock: bigint;
      toBlock: bigint;
    }) =>
      logs
        .filter(
          (l) =>
            l.event === q.event.name &&
            l.block >= q.fromBlock &&
            l.block <= q.toBlock &&
            (q.args.token === undefined ||
              ("token" in l && q.args.token.includes(l.token))),
        )
        .map((l) =>
          "isApproved" in l
            ? { args: { ...q.args, isApproved: l.isApproved } }
            : {
                args: {
                  owner: q.args.owner,
                  token: l.token,
                  spender: l.spender,
                },
              },
        ),
  );
  const readContract = vi.fn(
    async (q: {
      address: `0x${string}`;
      args: readonly `0x${string}`[];
    }) =>
      // ERC-20 allowance(owner, Permit2) vs Permit2 allowance(owner, token, spender)
      q.args.length === 2
        ? (outer[getAddress(q.address)] ?? 0n)
        : ([
            allowances[`${q.args[1]}:${q.args[2]}`] ?? 0n,
            281_474_976_710_655,
            0,
          ] as const),
  );
  const getBlockNumber = vi.fn(async () => 12_500n);
  const getCode = vi.fn(async (q: { blockNumber: bigint }) =>
    q.blockNumber >= delegatedAt ? "0xef0100deadbeef" : undefined,
  );
  const getProof = vi.fn(async (q: { blockNumber: bigint }) => ({
    storageHash:
      q.blockNumber >= delegatedAt ? `0x${"5a".repeat(32)}` : EMPTY_ROOT,
  }));
  const client = {
    getLogs,
    readContract,
    getBlockNumber,
    getCode,
    getProof,
  } as unknown as PublicClient;
  return { client, getLogs, readContract, getBlockNumber, getCode, getProof };
}

const EMPTY_ROOT =
  "0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421";

const LOGS: FakeLog[] = [
  { event: "SignatureCheckerApprovalSet", block: 90n, isApproved: true },
  { event: "Permit", block: 100n, token: U_TOKEN, spender: ATTACKER },
  { event: "Approval", block: 12_000n, token: U_TOKEN, spender: ROUTER },
  { event: "Permit", block: 12_001n, token: U_TOKEN, spender: ATTACKER },
  { event: "Permit", block: 7_000n, token: USDC, spender: ATTACKER },
];
const ALLOWANCES = {
  [`${U_TOKEN}:${ATTACKER}`]: MAX_UINT160,
  [`${U_TOKEN}:${ROUTER}`]: 5n,
  // the USDC permit was already spent / locked down
};

describe("Permit2 allowance cleanup (SRC-1609)", () => {
  it("listPermit2Allowances scans Permit/Approval logs of this wallet in chunks and keeps live pairs", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);
    const provider = adminProvider();

    const found = await provider.listPermit2Allowances({
      client: chain.client,
      fromBlock: 0n,
    });

    expect(found).toEqual([
      {
        token: U_TOKEN,
        spender: ATTACKER,
        amount: MAX_UINT160,
        expiration: 281_474_976_710_655,
        nonce: 0,
      },
      {
        token: U_TOKEN,
        spender: ROUTER,
        amount: 5n,
        expiration: 281_474_976_710_655,
        nonce: 0,
      },
    ]);
    const ranges = chain.getLogs.mock.calls.map(([q]) => [
      q.event.name,
      q.fromBlock,
      q.toBlock,
    ]);
    expect(ranges).toEqual([
      ["Permit", 0n, 4_999n],
      ["Approval", 0n, 4_999n],
      ["Permit", 5_000n, 9_999n],
      ["Approval", 5_000n, 9_999n],
      ["Permit", 10_000n, 12_500n],
      ["Approval", 10_000n, 12_500n],
    ]);
    for (const [q] of chain.getLogs.mock.calls) {
      expect(getAddress(q.address)).toBe(PERMIT2);
      expect(q.args.owner).toBe(provider.address);
    }
    // three distinct pairs read once each (the repeated permit is deduped)
    expect(chain.readContract).toHaveBeenCalledTimes(3);
  });

  it("listPermit2Allowances narrows by token and works in session mode", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);
    const found = await sessionProvider().listPermit2Allowances({
      client: chain.client,
      fromBlock: 0n,
      toBlock: 9_999n,
      tokens: [USDC],
      chunkSize: 10_000n,
    });
    expect(found).toEqual([]);
    const [q] = chain.getLogs.mock.calls[0] ?? [];
    expect(q?.args).toEqual({ owner: WALLET, token: [USDC] });
    expect(chain.getLogs).toHaveBeenCalledTimes(2);
  });

  it("lockdownPermit2Allowances submits Permit2 lockdown (and optional outer approve 0) as one batch", async () => {
    const provider = adminProvider();
    const pairs = [
      { token: U_TOKEN, spender: ATTACKER },
      { token: U_TOKEN, spender: ROUTER },
    ];

    await provider.lockdownPermit2Allowances(pairs, {
      zeroOuterAllowance: true,
    });

    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
    const { calls } = sdkMocks.executeMock.mock.calls[0][0] as {
      calls: { to: `0x${string}`; data: `0x${string}` }[];
    };
    expect(calls).toHaveLength(2);
    expect(getAddress(calls[0].to)).toBe(PERMIT2);
    expect(calls[0].data.slice(0, 10)).toBe("0xcc53287f"); // lockdown((address,address)[])
    const lockdown = decodeFunctionData({
      abi: PERMIT2_ALLOWANCE_ABI,
      data: calls[0].data,
    });
    expect(lockdown.args?.[0]).toEqual(pairs);
    expect(calls[1].to).toBe(U_TOKEN);
    const approve = decodeFunctionData({ abi: erc20Abi, data: calls[1].data });
    expect(approve.functionName).toBe("approve");
    expect(approve.args).toEqual([PERMIT2, 0n]);

    await expect(provider.lockdownPermit2Allowances([])).resolves.toBeNull();
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
    await expect(
      sessionProvider().lockdownPermit2Allowances(pairs),
    ).rejects.toThrow(/admin/);
  });

  it("revokeX402SignatureChecker with permit2Cleanup revokes first, then locks down what is still live", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);
    const provider = adminProvider();

    const result = await provider.revokeX402SignatureChecker(fakeSession(), {
      permit2Cleanup: { client: chain.client, fromBlock: 0n },
    });

    expect(result.status).toBe("CONFIRMED");
    expect(result.permit2Cleanup?.allowances.map((a) => a.spender)).toEqual([
      ATTACKER,
      ROUTER,
    ]);
    expect(result.permit2Cleanup?.lockdown?.status).toBe("CONFIRMED");
    expect(
      sdkMocks.revokeSignatureCheckerMock.mock.invocationCallOrder[0],
    ).toBeLessThan(chain.getLogs.mock.invocationCallOrder[0] ?? 0);
    const { calls } = sdkMocks.executeMock.mock.calls[0][0] as {
      calls: { data: `0x${string}` }[];
    };
    expect(calls).toHaveLength(1);
    expect(
      decodeFunctionData({ abi: PERMIT2_ALLOWANCE_ABI, data: calls[0].data })
        .args?.[0],
    ).toEqual([
      { token: U_TOKEN, spender: ATTACKER },
      { token: U_TOKEN, spender: ROUTER },
    ]);
  });

  it("revokeSession with permit2Cleanup and nothing live submits no lockdown", async () => {
    const chain = permit2Chain(LOGS, {});
    const result = await adminProvider().revokeSession(fakeSession(), {
      permit2Cleanup: { client: chain.client, fromBlock: 0n },
    });
    expect(sdkMocks.revokeSessionMock).toHaveBeenCalledTimes(1);
    expect(result.permit2Cleanup).toEqual({
      allowances: [],
      kept: [],
      lockdown: null,
    });
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  it("keepSpenders leaves chosen allowances, and their token's outer approval, in place", async () => {
    const chain = permit2Chain(
      LOGS,
      { ...ALLOWANCES, [`${USDC}:${ATTACKER}`]: 7n },
      { [U_TOKEN]: 50n, [USDC]: 9n },
    );

    const result = await adminProvider().revokeX402SignatureChecker(
      fakeSession(),
      {
        permit2Cleanup: {
          client: chain.client,
          fromBlock: 0n,
          keepSpenders: [ROUTER.toLowerCase() as `0x${string}`],
        },
      },
    );

    const cleanup = result.permit2Cleanup;
    expect(cleanup?.kept.map((a) => [a.token, a.spender])).toEqual([
      [U_TOKEN, ROUTER],
    ]);
    expect(cleanup?.allowances.map((a) => [a.token, a.spender])).toEqual([
      [U_TOKEN, ATTACKER],
      [USDC, ATTACKER],
    ]);
    const { calls } = sdkMocks.executeMock.mock.calls[0][0] as {
      calls: { to: `0x${string}`; data: `0x${string}` }[];
    };
    expect(
      decodeFunctionData({ abi: PERMIT2_ALLOWANCE_ABI, data: calls[0].data })
        .args?.[0],
    ).toEqual([
      { token: U_TOKEN, spender: ATTACKER },
      { token: USDC, spender: ATTACKER },
    ]);
    // U still backs the kept router, so only USDC's outer approval is zeroed
    expect(calls.slice(1).map((c) => c.to)).toEqual([USDC]);
  });

  it("reports a failed cleanup without hiding that the revoke went through", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);
    chain.getLogs.mockRejectedValueOnce(
      new Error("eth_getLogs range too large"),
    );
    await expect(
      adminProvider().revokeX402SignatureChecker(fakeSession(), {
        permit2Cleanup: { client: chain.client, fromBlock: 0n },
      }),
    ).rejects.toThrow(
      new RegExp(
        `revokeX402SignatureChecker succeeded \\(callsId ${CALLS_ID}\\), but the Permit2 cleanup failed: eth_getLogs range too large`,
      ),
    );
    expect(sdkMocks.revokeSignatureCheckerMock).toHaveBeenCalledTimes(1);
  });

  it("checks for the session's Permit2 checker approval on the wallet before trusting the scan", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);
    const provider = adminProvider();

    await provider.revokeSession(fakeSession(), {
      permit2Cleanup: { client: chain.client, fromBlock: 0n },
    });

    const [q] = chain.getLogs.mock.calls[0] ?? [];
    expect(q?.event.name).toBe("SignatureCheckerApprovalSet");
    expect(q?.address).toBe(provider.address);
    expect(q?.args).toEqual({ keyHash: SESSION_KEY_HASH, checker: PERMIT2 });
    // a cached latest block could end the scan before the newest logs
    for (const args of chain.getBlockNumber.mock.calls) {
      expect(args).toEqual([{ cacheTime: 0 }]);
    }
  });

  it.each([
    // fromBlock after the approval (block 90), before the permit (block 100)
    ["a fromBlock later than the approval", LOGS, 95n],
    // only the revoke's own isApproved=false event is visible
    [
      "an RPC that serves no approval",
      [
        {
          event: "SignatureCheckerApprovalSet",
          block: 95n,
          isApproved: false,
        },
      ] satisfies FakeLog[],
      0n,
    ],
  ])(
    "fails the cleanup instead of an empty success for %s (audit M03)",
    async (_label, logs, fromBlock) => {
      const chain = permit2Chain(logs, ALLOWANCES);

      await expect(
        adminProvider().revokeX402SignatureChecker(fakeSession(), {
          permit2Cleanup: { client: chain.client, fromBlock },
        }),
      ).rejects.toThrow(
        new RegExp(
          `revokeX402SignatureChecker succeeded \\(callsId ${CALLS_ID}\\), but the Permit2 cleanup failed: the scan from block ${fromBlock} shows no Permit2 signature-checker approval`,
        ),
      );
      expect(sdkMocks.revokeSignatureCheckerMock).toHaveBeenCalledTimes(1);
      expect(sdkMocks.executeMock).not.toHaveBeenCalled();
    },
  );
});

const USDT = getAddress("0x55d398326f99059fF775485246999027B3197955");
const OTHER_TOKEN = getAddress(`0x${"0d".repeat(20)}`);

/** The `approve(Permit2, amount)` and `lockdown` calls of an execute batch. */
function batchCalls(index = 0) {
  const { calls } = sdkMocks.executeMock.mock.calls[index][0] as {
    calls: { to: `0x${string}`; data: `0x${string}` }[];
  };
  return calls.map((c) =>
    c.data.startsWith("0xcc53287f")
      ? {
          lockdown: decodeFunctionData({
            abi: PERMIT2_ALLOWANCE_ABI,
            data: c.data,
          }).args?.[0],
        }
      : {
          approve: c.to,
          args: decodeFunctionData({ abi: erc20Abi, data: c.data }).args,
        },
  );
}

describe("outer Permit2 approval reset on revoke (SRC-1609, plan B)", () => {
  it("revokeX402SignatureChecker sets every catalog token's non-zero outer Permit2 approval to 0 after the revoke", async () => {
    const chain = permit2Chain([], {}, { [U_TOKEN]: 50n, [USDT]: 0n });

    const result = await adminProvider().revokeX402SignatureChecker(
      fakeSession(),
      { client: chain.client, feeToken: USDC },
    );

    expect(result.status).toBe("CONFIRMED");
    expect(result.permit2Reset?.tokens).toEqual([U_TOKEN]);
    expect(result.permit2Reset?.batch?.status).toBe("CONFIRMED");
    expect(result.permit2Cleanup).toBeUndefined();
    expect(batchCalls()).toEqual([{ approve: U_TOKEN, args: [PERMIT2, 0n] }]);
    expect(sdkMocks.executeMock.mock.calls[0][0].feeToken).toBe(USDC);
    // the kill switch lands first; the reset reads only current approvals
    expect(
      sdkMocks.revokeSignatureCheckerMock.mock.invocationCallOrder[0],
    ).toBeLessThan(sdkMocks.executeMock.mock.invocationCallOrder[0] ?? 0);
    expect(chain.getLogs).not.toHaveBeenCalled();
    const read = chain.readContract.mock.calls.map(([q]) =>
      getAddress(q.address),
    );
    expect(read).toEqual(expect.arrayContaining([U_TOKEN, USDC, USDT]));
  });

  it("revokeSession resets them too, and submits nothing when no approval is left", async () => {
    const chain = permit2Chain([], {});

    const result = await adminProvider().revokeSession(fakeSession(), {
      client: chain.client,
    });

    expect(sdkMocks.revokeSessionMock).toHaveBeenCalledTimes(1);
    expect(result.permit2Reset).toEqual({
      tokens: [],
      covered: listAssets(56).map((a) => getAddress(a.address)),
      batch: null,
    });
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  it("reports the catalog-only scope: a non-catalog token approved to Permit2 is neither read nor reset by default (audit L11)", async () => {
    const chain = permit2Chain(
      [],
      { [`${OTHER_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      { [U_TOKEN]: 50n, [OTHER_TOKEN]: 1_000n },
    );

    const result = await adminProvider().revokeSession(fakeSession(), {
      client: chain.client,
    });

    expect(result.permit2Reset?.tokens).toEqual([U_TOKEN]);
    expect(result.permit2Reset?.covered).not.toContain(OTHER_TOKEN);
    expect(batchCalls()).toEqual([{ approve: U_TOKEN, args: [PERMIT2, 0n] }]);
  });

  it("permit2Reset.extraTokens extends the reset to other tokens without a log scan (audit L11)", async () => {
    const chain = permit2Chain(
      [],
      {},
      { [U_TOKEN]: 50n, [OTHER_TOKEN]: 1_000n },
    );

    const result = await adminProvider().revokeX402SignatureChecker(
      fakeSession(),
      { client: chain.client, permit2Reset: { extraTokens: [OTHER_TOKEN] } },
    );

    expect(result.permit2Reset?.tokens).toEqual([U_TOKEN, OTHER_TOKEN]);
    expect(result.permit2Reset?.covered).toContain(OTHER_TOKEN);
    expect(batchCalls()).toEqual([
      { approve: U_TOKEN, args: [PERMIT2, 0n] },
      { approve: OTHER_TOKEN, args: [PERMIT2, 0n] },
    ]);
    expect(chain.getLogs).not.toHaveBeenCalled();
  });

  it("with permit2Cleanup, also resets the outer approval of every token the scan found an allowance for (audit L11)", async () => {
    const chain = permit2Chain(
      [
        { event: "SignatureCheckerApprovalSet", block: 90n, isApproved: true },
        { event: "Permit", block: 100n, token: OTHER_TOKEN, spender: ATTACKER },
      ],
      { [`${OTHER_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      { [OTHER_TOKEN]: 1_000n },
    );

    const result = await adminProvider().revokeSession(fakeSession(), {
      permit2Cleanup: { client: chain.client, fromBlock: 90n },
    });

    expect(result.permit2Reset?.tokens).toEqual([OTHER_TOKEN]);
    expect(batchCalls()).toEqual([
      { lockdown: [{ token: OTHER_TOKEN, spender: ATTACKER }] },
      { approve: OTHER_TOKEN, args: [PERMIT2, 0n] },
    ]);
  });

  it("permit2Reset: false leaves the outer approvals alone", async () => {
    const chain = permit2Chain([], {}, { [U_TOKEN]: 50n });

    const result = await adminProvider().revokeSession(fakeSession(), {
      client: chain.client,
      permit2Reset: false,
    });

    expect(result.permit2Reset).toBeUndefined();
    expect(chain.readContract).not.toHaveBeenCalled();
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  it("joins a requested lockdown in one batch with the reset of permit2Reset.extraTokens, and spares a kept spender's token", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES, {
      [U_TOKEN]: 50n,
      [USDC]: 9n,
      [OTHER_TOKEN]: 3n,
    });

    const result = await adminProvider().revokeSession(fakeSession(), {
      permit2Cleanup: {
        client: chain.client,
        fromBlock: 0n,
        tokens: [U_TOKEN, USDC],
        keepSpenders: [ROUTER],
      },
      permit2Reset: { extraTokens: [OTHER_TOKEN] },
    });

    expect(result.permit2Cleanup?.kept.map((a) => a.spender)).toEqual([ROUTER]);
    // U still backs the kept router; USDC and the extra token are reset
    expect(result.permit2Reset?.tokens).toEqual([USDC, OTHER_TOKEN]);
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
    expect(batchCalls()).toEqual([
      { lockdown: [{ token: U_TOKEN, spender: ATTACKER }] },
      { approve: USDC, args: [PERMIT2, 0n] },
      { approve: OTHER_TOKEN, args: [PERMIT2, 0n] },
    ]);
    expect(result.permit2Cleanup?.lockdown).toBe(result.permit2Reset?.batch);
  });

  it("reports a failed reset without hiding that the revoke went through", async () => {
    const chain = permit2Chain([], {});
    chain.readContract.mockRejectedValueOnce(new Error("rpc down"));

    await expect(
      adminProvider().revokeSession(fakeSession(), { client: chain.client }),
    ).rejects.toThrow(
      new RegExp(
        `revokeSession succeeded \\(callsId ${CALLS_ID}\\), but the Permit2 cleanup failed: rpc down.*setPermit2Allowance\\(token, 0n\\)`,
      ),
    );
    expect(sdkMocks.revokeSessionMock).toHaveBeenCalledTimes(1);
  });

  it("skips the reset when the SDK has no x402 surface (no session can sign for Permit2)", async () => {
    sdkMocks.x402Surface.enabled = false;
    const chain = permit2Chain([], {}, { [U_TOKEN]: 50n });

    const result = await adminProvider().revokeSession(fakeSession(), {
      client: chain.client,
    });

    expect(result.status).toBe("CONFIRMED");
    expect(result.permit2Reset).toBeUndefined();
    expect(chain.readContract).not.toHaveBeenCalled();
  });
});

describe("setPermit2Allowance re-arm guard (SRC-1609, plan B)", () => {
  it("refuses a non-zero approval without permit2Cleanup or skipPermit2Cleanup, before submitting anything", async () => {
    const provider = adminProvider();

    await expect(
      provider.setPermit2Allowance(U_TOKEN, 100n, {} as never),
    ).rejects.toThrow(/re-arms every Permit2 allowance.*Nothing was submitted/);
    await expect(
      provider.setPermit2Allowance(U_TOKEN, 100n, {
        skipPermit2Cleanup: true,
        permit2Cleanup: { fromBlock: 0n },
      } as never),
    ).rejects.toThrow(/either permit2Cleanup or skipPermit2Cleanup/);
    expect(sdkMocks.approveTokenForPermit2Mock).not.toHaveBeenCalled();
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();

    // zeroing needs no choice
    await provider.setPermit2Allowance(U_TOKEN, 0n);
    expect(sdkMocks.approveTokenForPermit2Mock).toHaveBeenCalledTimes(1);
  });

  it("with permit2Cleanup, locks down the token's stored allowances and approves in one batch", async () => {
    const chain = permit2Chain(LOGS, ALLOWANCES);

    const result = await adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
      permit2Cleanup: { client: chain.client, fromBlock: 0n },
      feeToken: USDC,
    });

    expect(result.permit2Cleanup?.allowances.map((a) => a.spender)).toEqual([
      ATTACKER,
      ROUTER,
    ]);
    expect(batchCalls()).toEqual([
      {
        lockdown: [
          { token: U_TOKEN, spender: ATTACKER },
          { token: U_TOKEN, spender: ROUTER },
        ],
      },
      { approve: U_TOKEN, args: [PERMIT2, 100n] },
    ]);
    expect(sdkMocks.executeMock.mock.calls[0][0].feeToken).toBe(USDC);
    expect(sdkMocks.approveTokenForPermit2Mock).not.toHaveBeenCalled();
    const queries = chain.getLogs.mock.calls.map(([q]) => q);
    // any session's Permit2 checker approval of this wallet counts
    expect(queries[0]?.event.name).toBe("SignatureCheckerApprovalSet");
    expect(queries[0]?.args).toEqual({ checker: PERMIT2 });
    for (const q of queries.slice(1)) {
      expect(q.args.token).toEqual([U_TOKEN]);
    }
  });

  // an earlier session approved at 90 signed PermitSingle(U -> ATTACKER) at
  // 100; a new session is approved at 12 400; the wallet was delegated at 50
  const EARLIER_SESSION_LOGS: FakeLog[] = [
    { event: "SignatureCheckerApprovalSet", block: 90n, isApproved: true },
    { event: "Permit", block: 100n, token: U_TOKEN, spender: ATTACKER },
    { event: "SignatureCheckerApprovalSet", block: 12_400n, isApproved: true },
  ];

  it("refuses a fromBlock after the wallet's delegation, such as the new session's approval block, so an earlier session's allowance is not re-armed (audit L10)", async () => {
    const chain = permit2Chain(
      EARLIER_SESSION_LOGS,
      { [`${U_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      {},
      50n,
    );

    await expect(
      adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
        permit2Cleanup: { client: chain.client, fromBlock: 12_400n },
      }),
    ).rejects.toThrow(
      /already had code or storage at block 12399.*Omit fromBlock to scan from the wallet's first EIP-7702 delegation.*Nothing was submitted/,
    );
    expect(chain.getCode).toHaveBeenCalledWith(
      expect.objectContaining({
        address: privateKeyToAccount(ADMIN_PK).address,
        blockNumber: 12_399n,
      }),
    );
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
    expect(sdkMocks.approveTokenForPermit2Mock).not.toHaveBeenCalled();
  });

  it("without fromBlock, scans from the wallet's first delegation and locks the earlier session's allowance down with the re-approval (audit L10)", async () => {
    const chain = permit2Chain(
      EARLIER_SESSION_LOGS,
      { [`${U_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      {},
      50n,
    );

    const result = await adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
      permit2Cleanup: { client: chain.client },
    });

    expect(result.permit2Cleanup?.allowances.map((a) => a.spender)).toEqual([
      ATTACKER,
    ]);
    expect(batchCalls()).toEqual([
      { lockdown: [{ token: U_TOKEN, spender: ATTACKER }] },
      { approve: U_TOKEN, args: [PERMIT2, 100n] },
    ]);
    expect(chain.getLogs.mock.calls[0]?.[0].fromBlock).toBe(50n);
  });

  it("accepts an explicit fromBlock at the wallet's delegation", async () => {
    const chain = permit2Chain(
      EARLIER_SESSION_LOGS,
      { [`${U_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      {},
      50n,
    );

    const result = await adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
      permit2Cleanup: { client: chain.client, fromBlock: 50n },
    });

    expect(result.permit2Cleanup?.allowances.map((a) => a.spender)).toEqual([
      ATTACKER,
    ]);
  });

  it("submits nothing when the wallet's first delegation cannot be found (audit L10)", async () => {
    const chain = permit2Chain(EARLIER_SESSION_LOGS, {}, {}, 50n);
    chain.getProof.mockRejectedValueOnce(
      new Error("method eth_getProof not supported"),
    );

    await expect(
      adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
        permit2Cleanup: { client: chain.client },
      }),
    ).rejects.toThrow(
      /could not find the wallet's first EIP-7702 delegation \(method eth_getProof not supported\).*Nothing was submitted/,
    );
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  it("submits nothing when the wallet's code before fromBlock cannot be read (audit L10)", async () => {
    const chain = permit2Chain(EARLIER_SESSION_LOGS, {}, {}, 50n);
    chain.getCode.mockRejectedValueOnce(new Error("missing trie node"));

    await expect(
      adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
        permit2Cleanup: { client: chain.client, fromBlock: 50n },
      }),
    ).rejects.toThrow(
      /could not read the wallet's code and storage at block 49 \(missing trie node\).*eth_getProof.*Nothing was submitted/,
    );
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });

  /** Delegated at 50, delegation cleared at 200 and set again at 9 000; storage kept. */
  function redelegatedChain() {
    const chain = permit2Chain(
      EARLIER_SESSION_LOGS,
      { [`${U_TOKEN}:${ATTACKER}`]: MAX_UINT160 },
      {},
      50n,
    );
    chain.getCode.mockImplementation(async (q: { blockNumber: bigint }) =>
      (q.blockNumber >= 50n && q.blockNumber < 200n) || q.blockNumber >= 9_000n
        ? "0xef0100deadbeef"
        : undefined,
    );
    return chain;
  }

  it("refuses a fromBlock right after a cleared delegation was set again: the account's storage shows the earlier one (audit L10)", async () => {
    const chain = redelegatedChain();

    await expect(
      adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
        permit2Cleanup: { client: chain.client, fromBlock: 9_000n },
      }),
    ).rejects.toThrow(/already had code or storage at block 8999/);
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();

    // without fromBlock, the scan starts at the first delegation instead
    const result = await adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
      permit2Cleanup: { client: chain.client },
    });
    expect(result.permit2Cleanup?.allowances.map((a) => a.spender)).toEqual([
      ATTACKER,
    ]);
  });

  it("findDelegationBlock binary-searches the first block with code or storage, across a cleared delegation", async () => {
    const chain = permit2Chain([], {}, {}, 7_777n);

    await expect(findDelegationBlock(chain.client, WALLET)).resolves.toBe(
      7_777n,
    );
    // log2(12 500) reads plus the two bounds
    expect(chain.getCode.mock.calls.length).toBeLessThanOrEqual(16);

    await expect(
      findDelegationBlock(redelegatedChain().client, WALLET),
    ).resolves.toBe(50n);
    await expect(
      findDelegationBlock(permit2Chain([], {}, {}, 0n).client, WALLET),
    ).resolves.toBe(0n);
    await expect(
      findDelegationBlock(permit2Chain([], {}, {}, 99_999n).client, WALLET),
    ).rejects.toThrow(/has no code or storage at the latest block 12500/);
  });

  it("with permit2Cleanup, submits nothing when the scan shows no Permit2 checker approval", async () => {
    const chain = permit2Chain(
      LOGS.filter((l) => l.event !== "SignatureCheckerApprovalSet"),
      ALLOWANCES,
    );

    await expect(
      adminProvider().setPermit2Allowance(U_TOKEN, 100n, {
        permit2Cleanup: { client: chain.client, fromBlock: 0n },
      }),
    ).rejects.toThrow(
      /shows no Permit2 signature-checker approval for any session of this wallet.*skipPermit2Cleanup: true.*Nothing was submitted/,
    );
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
    expect(sdkMocks.approveTokenForPermit2Mock).not.toHaveBeenCalled();
  });
});

// ── real-wire challenge shape (CMC B402, field-verified 2026-07-14) ──────

const U_BSC = getAddress("0xcE24439F2D9C6a2289F741120FE202248B666666");
const CMC_RESOURCE = {
  url: "https://pro-api.example/x402",
  description: "quotes",
  mimeType: "application/json",
};
const CMC_CHALLENGE = {
  x402Version: 2,
  resource: CMC_RESOURCE,
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
    // The signed requirement carries the challenge's top-level x402Version
    // and resource — b402 merchants reject envelopes missing either.
    const [, requirement] = sdkMocks.signX402PaymentMock.mock.calls[0];
    expect(requirement).toEqual({
      ...CMC_CHALLENGE.accepts[1],
      x402Version: 2,
      resource: CMC_RESOURCE,
    });
  });

  it("expectedPayTo pins the recipient: a mismatching payTo refuses before signing", async () => {
    const { impl } = fetchQueue(json402(CMC_CHALLENGE));
    const payer = sessionProvider().makeX402Payer({
      fetchImpl: impl,
      expectedPayTo: `0x${"77".repeat(20)}`,
    });
    await expect(
      payer.request("https://pro-api.example/x402", { maxPayment: 10n ** 17n }),
    ).rejects.toThrow(X402RecipientMismatchError);
    expect(sdkMocks.signX402PaymentMock).not.toHaveBeenCalled();

    const { impl: impl2 } = fetchQueue(
      json402(CMC_CHALLENGE),
      jsonOk({ ok: true }),
    );
    const pinned = sessionProvider().makeX402Payer({
      fetchImpl: impl2,
      expectedPayTo: PAY_TO.toUpperCase().replace("0X", "0x"), // case-insensitive
    });
    const result = await pinned.request("https://pro-api.example/x402", {
      maxPayment: 10n ** 17n,
    });
    expect(result.success).toBe(true);
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

// ── b402 envelope normalization (JSON-level, signature bytes untouched) ──

const SIG_98 = `0x${"ab".repeat(98)}`;

function b64(envelope: unknown): string {
  return Buffer.from(JSON.stringify(envelope), "utf8").toString("base64");
}

function decode(header: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(header, "base64").toString("utf8"));
}

describe("normalizeX402PaymentHeader", () => {
  const challengeMeta = { x402Version: 2, resource: CMC_RESOURCE };

  it("is a strict no-op on a compliant (>=0.7.0) envelope — byte-identical header", () => {
    const compliant = b64({
      x402Version: 2,
      resource: CMC_RESOURCE,
      scheme: "exact",
      network: "eip155:56",
      accepted: {},
      payload: {
        signature: SIG_98,
        from: WALLET,
        permit: {
          permitted: {},
          spender: PAY_TO,
          nonce: "1",
          deadline: "2",
          witness: {},
        },
        permit2Authorization: {
          permitted: {},
          from: WALLET,
          spender: PAY_TO,
          nonce: "1",
          deadline: "2",
          witness: {},
        },
      },
    });
    expect(normalizeX402PaymentHeader(compliant, challengeMeta)).toBe(
      compliant,
    );
  });

  it("injects missing resource and x402Version from the challenge", () => {
    const legacy = b64({
      scheme: "exact",
      payload: { signature: SIG_98, authorization: { from: WALLET } },
    });
    const fixed = decode(normalizeX402PaymentHeader(legacy, challengeMeta));
    expect(fixed.resource).toEqual(CMC_RESOURCE);
    expect(fixed.x402Version).toBe(2);
    // Signature bytes untouched.
    expect((fixed.payload as Record<string, unknown>).signature).toBe(SIG_98);
  });

  it("adds the b402 permit2Authorization spelling alongside the legacy permit+from dialect", () => {
    const permit = {
      permitted: { token: U_BSC, amount: "10" },
      spender: PAY_TO,
      nonce: "7",
      deadline: "99",
      witness: { extra: true },
    };
    const legacy = b64({
      x402Version: 2,
      resource: CMC_RESOURCE,
      payload: { signature: SIG_98, from: WALLET, permit },
    });
    const fixed = decode(normalizeX402PaymentHeader(legacy, challengeMeta));
    const payload = fixed.payload as Record<string, unknown>;
    expect(payload.permit2Authorization).toEqual({
      permitted: permit.permitted,
      from: WALLET,
      spender: permit.spender,
      nonce: permit.nonce,
      deadline: permit.deadline,
      witness: permit.witness,
    });
    // Legacy keys are retained (Altana's own x402-server still parses them).
    expect(payload.permit).toEqual(permit);
    expect(payload.from).toBe(WALLET);
    expect(payload.signature).toBe(SIG_98);
  });

  it("returns non-JSON headers verbatim rather than corrupting them", () => {
    expect(normalizeX402PaymentHeader("xp-header", challengeMeta)).toBe(
      "xp-header",
    );
    const jsonArray = Buffer.from("[1,2]", "utf8").toString("base64");
    expect(normalizeX402PaymentHeader(jsonArray, challengeMeta)).toBe(
      jsonArray,
    );
  });

  it("through the payer: a pre-0.7.0 envelope is normalized before the paid retry", async () => {
    // The mock signer emits an envelope missing resource/x402Version and
    // speaking only the legacy permit+from dialect — the 0.5.x/0.6.x wire.
    const permit = {
      permitted: { token: U_BSC, amount: "10000000000000000" },
      spender: `0x${"30".repeat(20)}`,
      nonce: "1",
      deadline: "9999",
      witness: {},
    };
    sdkMocks.signX402PaymentMock.mockResolvedValue({
      header: b64({
        scheme: "exact",
        network: "eip155:56",
        payload: { signature: SIG_98, from: WALLET, permit },
      }),
    });
    const { impl, calls } = fetchQueue(
      json402(CMC_CHALLENGE),
      jsonOk({ ok: true }),
    );
    const payer = sessionProvider().makeX402Payer({ fetchImpl: impl });
    await payer.request("https://pro-api.example/x402", {
      maxPayment: 10n ** 17n,
    });
    const sentHeaders = calls[1].init?.headers as Record<string, string>;
    expect(sentHeaders[X_PAYMENT_HEADER]).toBe(
      sentHeaders[PAYMENT_SIGNATURE_HEADER],
    );
    const envelope = decode(sentHeaders[X_PAYMENT_HEADER]);
    expect(envelope.resource).toEqual(CMC_RESOURCE);
    expect(envelope.x402Version).toBe(2);
    const payload = envelope.payload as Record<string, unknown>;
    expect((payload.permit2Authorization as Record<string, unknown>).from).toBe(
      WALLET,
    );
    expect(payload.signature).toBe(SIG_98);
  });
});
