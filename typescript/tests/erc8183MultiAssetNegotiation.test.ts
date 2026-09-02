import { getAddress } from "viem";
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
    const canonical = await handler.negotiate(request(AssetId.TEST_USDC));
    expect(usdc.response.terms).toMatchObject({
      currency: TEST_USDC.address,
      price: "100000",
    });
    expect(usdt.response.terms).toMatchObject({
      currency: TEST_USDT.address,
      price: "100000000000000000",
    });
    expect(canonical.response.terms).toMatchObject({
      currency: TEST_USDC.address,
      price: "100000",
    });
  });

  it("does not substitute USDC/USDT when the catalog-default U is omitted", async () => {
    const handler = await multi(
      client(
        new Set([
          TEST_USDC.address.toLowerCase(),
          TEST_USDT.address.toLowerCase(),
        ]),
      ),
    );
    const result = await handler.negotiate(request());
    expect(result.accepted).toBe(false);
    expect(result.response).toMatchObject({
      reason_code: ReasonCode.UNSUPPORTED,
      details: { supported_assets: [AssetId.TEST_USDC, AssetId.TEST_USDT] },
    });
  });

  it("uses catalog U as default even when Commerce paymentToken is misconfigured", async () => {
    const erc8183Client = client(
      new Set([TEST_USDC.address.toLowerCase()]),
      TEST_USDC.address,
    );
    const handler = await NegotiationHandler.fromErc8183ClientMulti(
      erc8183Client,
      { servicePrices: { [AssetId.TEST_USDC]: "1" } },
    );
    expect((await handler.negotiate(request())).accepted).toBe(false);
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
});
