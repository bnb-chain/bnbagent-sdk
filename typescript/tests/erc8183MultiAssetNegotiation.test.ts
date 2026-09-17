import {
  BaseError,
  ContractFunctionRevertedError,
  encodeErrorResult,
  getAddress,
} from "viem";
import { describe, expect, it, vi } from "vitest";
import type { ERC8183Client } from "../src/erc8183/client.js";
import {
  NegotiationHandler,
  NegotiationResponse,
  ReasonCode,
} from "../src/erc8183/negotiation.js";
import { AssetId, getAsset } from "../src/networks/assets.js";

const CHAIN_ID = 97;
const COMMERCE = getAddress(`0x${"ab".repeat(20)}`);
const TEST_U = getAsset(CHAIN_ID, AssetId.TEST_U);
const TEST_USDC = getAsset(CHAIN_ID, AssetId.TEST_USDC);
const TEST_USDT = getAsset(CHAIN_ID, AssetId.TEST_USDT);

function request(currency?: string, price?: unknown): Record<string, unknown> {
  const terms: Record<string, unknown> = {
    deliverables: "summary",
    quality_standards: "accurate",
  };
  if (currency !== undefined) terms.currency = currency;
  if (price !== undefined) terms.price = price;
  return { task_description: "Summarize", terms };
}

function client(enabled: Set<string>, defaultToken = TEST_U.address) {
  return {
    network: { chainId: CHAIN_ID },
    commerce: { address: COMMERCE },
    paymentToken: vi.fn(async () => defaultToken),
    isPaymentTokenSupported: vi.fn(async (token: string) =>
      enabled.has(token.toLowerCase()),
    ),
  } as unknown as ERC8183Client;
}

async function multi(erc8183Client: ERC8183Client) {
  return NegotiationHandler.fromErc8183ClientMulti(erc8183Client, {
    servicePrices: {
      [AssetId.TEST_USDC]: "100000",
      [AssetId.TEST_USDT]: "100000000000000000",
    },
  });
}

describe("multi-asset ERC-8183 negotiation", () => {
  it("round-trips optional supported_assets details and omits it for legacy wire", () => {
    const withDetails = NegotiationResponse.fromDict({
      accepted: false,
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [AssetId.TEST_USDC] },
    });
    expect(withDetails.toDict()).toEqual({
      accepted: false,
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [AssetId.TEST_USDC] },
    });
    expect(
      NegotiationResponse.fromDict({
        accepted: false,
        reason_code: ReasonCode.UNSUPPORTED,
      }).toDict(),
    ).toEqual({ accepted: false, reason_code: ReasonCode.UNSUPPORTED });
  });

  it("selects exact USDC/USDT offers without decimal conversion", async () => {
    const handler = await multi(
      client(
        new Set([
          TEST_USDC.address.toLowerCase(),
          TEST_USDT.address.toLowerCase(),
        ]),
      ),
    );
    const usdc = await handler.negotiate(
      request(TEST_USDC.address.toLowerCase()),
    );
    const usdt = await handler.negotiate(request(TEST_USDT.address));
    const canonicalWire = await handler.negotiate(request(AssetId.TEST_USDC));
    expect(usdc.response.terms).toMatchObject({
      currency: TEST_USDC.address,
      price: "100000",
    });
    expect(usdt.response.terms).toMatchObject({
      currency: TEST_USDT.address,
      price: "100000000000000000",
    });
    expect(canonicalWire.response).toMatchObject({
      accepted: false,
      reason_code: ReasonCode.UNSUPPORTED,
      details: {
        supported_assets: [AssetId.TEST_USDC, AssetId.TEST_USDT],
      },
    });
  });

  it("omitted currency uses the first configured active asset when U is not offered", async () => {
    const handler = await multi(
      client(
        new Set([
          TEST_USDC.address.toLowerCase(),
          TEST_USDT.address.toLowerCase(),
        ]),
      ),
    );
    const result = await handler.negotiate(request());
    expect(result.accepted).toBe(true);
    expect(result.response.terms).toMatchObject({
      currency: TEST_USDC.address,
      price: "100000",
    });
  });

  it("does not substitute another asset when configured U is currently unavailable", async () => {
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      client(new Set([TEST_USDC.address.toLowerCase()])),
      {
        servicePrices: {
          [AssetId.TEST_U]: "1",
          [AssetId.TEST_USDC]: "2",
        },
      },
    );
    const result = await handler.negotiate(request());
    expect(result.accepted).toBe(false);
    expect(result.response).toMatchObject({
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [AssetId.TEST_USDC] },
    });
  });

  it("omitted currency does not use Commerce paymentToken; USDC-only seller quotes USDC", async () => {
    const erc8183Client = client(
      new Set([TEST_USDC.address.toLowerCase()]),
      TEST_USDC.address,
    );
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      erc8183Client,
      { servicePrices: { [AssetId.TEST_USDC]: "1" } },
    );
    const omitted = await handler.negotiate(request());
    expect(omitted.accepted).toBe(true);
    expect(omitted.response.terms).toMatchObject({
      currency: TEST_USDC.address,
      price: "1",
    });
    expect((await handler.negotiate(request(TEST_USDC.address))).accepted).toBe(
      true,
    );
    expect(erc8183Client.paymentToken).not.toHaveBeenCalled();
  });

  it("refreshes each negotiation, removes only disabled assets, and fails closed on RPC error", async () => {
    const enabled = new Set([
      TEST_USDC.address.toLowerCase(),
      TEST_USDT.address.toLowerCase(),
    ]);
    const erc8183Client = client(enabled);
    const handler = await multi(erc8183Client);
    enabled.delete(TEST_USDT.address.toLowerCase());
    const rejected = await handler.negotiate(request(TEST_USDT.address));
    expect(rejected.response.details).toEqual({
      supported_assets: [AssetId.TEST_USDC],
    });
    expect((await handler.negotiate(request(TEST_USDC.address))).accepted).toBe(
      true,
    );

    vi.mocked(erc8183Client.isPaymentTokenSupported).mockRejectedValueOnce(
      new Error("rpc://secret-key"),
    );
    const dormant = await handler.negotiate(request(TEST_USDC.address));
    expect(dormant.response).toMatchObject({
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [] },
    });
    expect(JSON.stringify(dormant.response)).not.toContain("secret");
  });

  it("accepts canonical zero, rejects malformed request price and multi price override", async () => {
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      client(new Set([TEST_USDC.address.toLowerCase()])),
      { servicePrices: { [AssetId.TEST_USDC]: "0" } },
    );
    const zero = await handler.negotiate(request(TEST_USDC.address, "0"));
    expect(zero.response.terms).toMatchObject({ price: "0" });

    for (const malformed of [true, -1, 1.5, "00", "1e3", {}, []]) {
      const result = await handler.negotiate(
        request(TEST_USDC.address, malformed),
      );
      expect(result.response.reason_code).toBe(ReasonCode.AMBIGUOUS_TERMS);
    }
    expect(
      (await handler.negotiate(request(TEST_USDC.address), { price: "1" }))
        .response.reason_code,
    ).toBe(ReasonCode.AMBIGUOUS_TERMS);
  });

  it("rejects symbol, cross-chain and malformed configuration", async () => {
    const erc8183Client = client(new Set([TEST_USDC.address.toLowerCase()]));
    for (const assetId of ["USDC", AssetId.BINANCE_PEG_USDC, "UNKNOWN"]) {
      await expect(
        NegotiationHandler.fromErc8183ClientMulti(erc8183Client, {
          servicePrices: {
            [assetId]: "1",
          } as unknown as Partial<Record<AssetId, string>>,
        }),
      ).rejects.toThrow();
    }
    for (const price of ["00", "01", "-1", "1.0", "1e3"]) {
      await expect(
        NegotiationHandler.fromErc8183ClientMulti(erc8183Client, {
          servicePrices: { [AssetId.TEST_USDC]: price },
        }),
      ).rejects.toThrow(/non-negative integer/);
    }
  });

  it("supports both BSC 56 and 97 catalog-bound offers", async () => {
    const mainUsdc = getAsset(56, AssetId.BINANCE_PEG_USDC);
    const mainClient = {
      network: { chainId: 56 },
      commerce: { address: COMMERCE },
      isPaymentTokenSupported: vi.fn(async () => true),
    } as unknown as ERC8183Client;
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      mainClient,
      {
        servicePrices: { [AssetId.BINANCE_PEG_USDC]: "1000000000000000000" },
      },
    );
    expect((await handler.negotiate(request(mainUsdc.address))).accepted).toBe(
      true,
    );
  });

  it("keeps an explicit USD1 offer immutable and returns alternatives instead of switching", async () => {
    const usd1 = getAsset(56, AssetId.USD1);
    const mainU = getAsset(56, AssetId.U);
    const mainClient = {
      network: { chainId: 56 },
      commerce: { address: COMMERCE },
      isPaymentTokenSupported: vi.fn(async () => true),
    } as unknown as ERC8183Client;
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      mainClient,
      { servicePrices: { [AssetId.USD1]: "100000000000000000" } },
    );

    expect(
      (await handler.negotiate(request(usd1.address.toLowerCase()))).response
        .terms,
    ).toMatchObject({
      currency: usd1.address,
      price: "100000000000000000",
    });
    for (const currency of [
      mainU.address,
      "0x0000000000000000000000000000000000000000",
    ]) {
      expect(
        (await handler.negotiate(request(currency))).response,
      ).toMatchObject({
        accepted: false,
        reason_code: ReasonCode.UNSUPPORTED,
        details: { supported_assets: [AssetId.USD1] },
      });
    }
    expect((await handler.negotiate(request())).response).toMatchObject({
      accepted: true,
      terms: {
        currency: usd1.address,
        price: "100000000000000000",
      },
    });
  });
});

describe("single-token Commerce deployments", () => {
  // A deployment predating multi-token support has no
  // `isPaymentTokenSupported` selector, so its dispatcher reverts with empty
  // returndata — the exact shape viem reports from BSC.
  function dispatcherRevert(): Error {
    const reverted = new ContractFunctionRevertedError({
      abi: [],
      functionName: "isPaymentTokenSupported",
      message: "execution reverted",
    });
    return new BaseError(
      'The contract function "isPaymentTokenSupported" reverted.',
      { cause: reverted },
    );
  }

  function legacyClient(defaultToken = TEST_U.address) {
    return {
      network: { chainId: CHAIN_ID },
      commerce: { address: COMMERCE },
      paymentToken: vi.fn(async () => defaultToken),
      isPaymentTokenSupported: vi.fn(async () => {
        throw dispatcherRevert();
      }),
    } as unknown as ERC8183Client;
  }

  it("keeps selling the contract's own token instead of failing to build", async () => {
    const erc8183Client = legacyClient();
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      erc8183Client,
      {
        servicePrices: {
          [AssetId.TEST_U]: "1000000",
          [AssetId.TEST_USDC]: "100000",
        },
      },
    );

    expect(handler.isSingleTokenDeployment).toBe(true);
    expect(
      (await handler.negotiate(request(TEST_U.address))).response,
    ).toMatchObject({
      accepted: true,
      terms: { currency: TEST_U.address, price: "1000000" },
    });
  });

  it("names the asset it can still sell when refusing one the stack cannot hold", async () => {
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      legacyClient(),
      {
        servicePrices: {
          [AssetId.TEST_U]: "1000000",
          [AssetId.TEST_USDC]: "100000",
        },
      },
    );

    expect(
      (await handler.negotiate(request(TEST_USDC.address))).response,
    ).toMatchObject({
      accepted: false,
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [AssetId.TEST_U] },
    });
  });

  it("offers nothing when the seller configured no asset the stack can hold", async () => {
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      legacyClient(),
      { servicePrices: { [AssetId.TEST_USDC]: "100000" } },
    );

    expect((await handler.negotiate(request())).response).toMatchObject({
      accepted: false,
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [] },
    });
  });

  it("probes the missing selector once rather than per negotiation", async () => {
    const erc8183Client = legacyClient();
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      erc8183Client,
      { servicePrices: { [AssetId.TEST_U]: "1000000" } },
    );
    const probesAfterBuild = vi.mocked(erc8183Client.isPaymentTokenSupported)
      .mock.calls.length;

    await handler.negotiate(request(TEST_U.address));
    await handler.negotiate(request(TEST_U.address));

    expect(
      vi.mocked(erc8183Client.isPaymentTokenSupported).mock.calls.length,
    ).toBe(probesAfterBuild);
  });

  // A function that exists and refuses carries returndata back; only a
  // missing selector comes back empty. Degrading on the former would sell
  // through a pause or an access-control revert.
  it.each([
    [
      "a reason string",
      encodeErrorResult({
        abi: [{ type: "error", name: "Error", inputs: [{ type: "string" }] }],
        errorName: "Error",
        args: ["Pausable: paused"],
      }),
    ],
    ["a custom error", "0x8e78f0cb" as const],
  ])(
    "still fails closed when the contract reverts with %s",
    async (_label, data) => {
      const erc8183Client = client(new Set([TEST_USDC.address.toLowerCase()]));
      const handler = await multi(erc8183Client);

      vi.mocked(erc8183Client.isPaymentTokenSupported).mockRejectedValueOnce(
        new BaseError(
          'The contract function "isPaymentTokenSupported" reverted.',
          {
            cause: new ContractFunctionRevertedError({
              abi: [],
              functionName: "isPaymentTokenSupported",
              data,
            }),
          },
        ),
      );

      expect(
        (await handler.negotiate(request(TEST_USDC.address))).response,
      ).toMatchObject({
        reason_code: ReasonCode.UNSUPPORTED,
        details: { supported_assets: [] },
      });
      expect(handler.isSingleTokenDeployment).toBe(false);
    },
  );
});
