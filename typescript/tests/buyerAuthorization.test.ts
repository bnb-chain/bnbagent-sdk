import { afterEach, describe, expect, it, vi } from "vitest";
import { fundJob } from "../examples/a2a-agent/scripts/buyer.js";
import { ERC8183Client } from "../src/erc8183/index.js";

vi.mock("../src/index.js", async (original) => ({
  ...(await original<object>()),
  EVMWalletProvider: class {},
  loadEnv: vi.fn(),
}));
vi.mock("../src/erc8183/index.js", async (original) => ({
  ...(await original<object>()),
  verifyQuoteSignature: vi.fn(async () => ({ valid: true })),
}));

const TOKEN = `0x${"11".repeat(20)}`;
const PROVIDER = `0x${"22".repeat(20)}`;
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("buyer purchase authorization", () => {
  it.each([
    { price: "1000", chain: 97, token: TOKEN, maximum: "1000", accepted: true },
    {
      price: "1001",
      chain: 97,
      token: TOKEN,
      maximum: "1000",
      accepted: false,
    },
    {
      price: "1000",
      chain: 56,
      token: TOKEN,
      maximum: "1000",
      accepted: false,
    },
    {
      price: "1000",
      chain: 97,
      token: `0x${"33".repeat(20)}`,
      maximum: "1000",
      accepted: false,
    },
    { price: "1000", chain: 97, token: TOKEN, maximum: "", accepted: false },
    { price: "1e3", chain: 97, token: TOKEN, maximum: "1000", accepted: false },
  ])(
    "checks $price on chain $chain before any write ($accepted)",
    async ({ price, chain, token, maximum, accepted }) => {
      for (const [key, value] of Object.entries({
        BUYER_PRIVATE_KEY: "test",
        BUYER_WALLET_PASSWORD: "test",
        BUYER_MAX_PRICE: maximum,
        BUYER_CHAIN_ID: "97",
        BUYER_PAYMENT_TOKEN: TOKEN,
      }))
        vi.stubEnv(key, value);
      const client = {
        publicClient: { getChainId: vi.fn(async () => 97) },
        commerce: { address: PROVIDER },
        paymentToken: vi.fn(async () => TOKEN),
        createJob: vi.fn(async () => ({
          jobId: 1n,
          transactionHash: "0xtest",
        })),
        registerJob: vi.fn(),
        setBudget: vi.fn(),
        fund: vi.fn(),
      };
      vi.spyOn(ERC8183Client, "create").mockResolvedValue(client as never);
      const quote = {
        provider_address: PROVIDER,
        chain_id: chain,
        response: { accepted: true, terms: { price, currency: token } },
      };
      if (accepted) {
        expect(await fundJob(quote, PROVIDER)).toBe(1n);
        expect(client.fund).toHaveBeenCalledWith(1n, 1000n);
      } else {
        await expect(fundJob(quote, PROVIDER)).rejects.toThrow();
        for (const write of [
          client.createJob,
          client.registerJob,
          client.setBudget,
          client.fund,
        ])
          expect(write).not.toHaveBeenCalled();
      }
    },
  );
});
