import { getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { describe, expect, it, vi } from "vitest";
import type { ERC8183Client } from "../src/erc8183/client.js";
import {
  ERC8183JobOps,
  ERR_JOB_TOKEN_MISMATCH,
} from "../src/erc8183/jobOps.js";
import {
  NegotiationHandler,
  buildJobDescription,
} from "../src/erc8183/negotiation.js";
import { type Job, JobStatus } from "../src/erc8183/types.js";
import { AssetId, getAsset } from "../src/networks/assets.js";
import type { WalletProvider } from "../src/wallets/walletProvider.js";

const ME = getAddress(`0x${"aa".repeat(20)}`);
const OTHER = getAddress(`0x${"77".repeat(20)}`);
const DEFAULT = getAsset(97, AssetId.TEST_U);
const USDC = getAsset(97, AssetId.TEST_USDC);
const USDT = getAsset(97, AssetId.TEST_USDT);
const SELLER = privateKeyToAccount(
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
);

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 1n,
    client: getAddress(`0x${"bb".repeat(20)}`),
    provider: ME,
    evaluator: getAddress(`0x${"cc".repeat(20)}`),
    description: "",
    budget: 1000n,
    expiredAt: BigInt(Math.floor(Date.now() / 1000) + 3600),
    status: JobStatus.FUNDED,
    hook: getAddress(`0x${"cc".repeat(20)}`),
    deliverable: `0x${"00".repeat(32)}`,
    submittedAt: 0n,
    ...overrides,
  };
}

function mockClient() {
  return {
    getJob: vi.fn(async () => job()),
    jobPaymentToken: vi.fn(async () => DEFAULT.address),
    paymentToken: vi.fn(async () => DEFAULT.address),
    tokenDecimals: vi.fn(async () => 18),
    getJobFundedBlock: vi.fn(async () => 123n),
    publicClient: {
      getChainId: vi.fn(async () => 97),
      getBlock: vi.fn(async () => ({
        timestamp: BigInt(Math.floor(Date.now() / 1000)),
      })),
      getBytecode: vi.fn(async () => undefined),
      readContract: vi.fn(),
    },
    network: { chainId: 97 },
    commerce: { address: getAddress(`0x${"11".repeat(20)}`) },
    policy: { disputeWindow: vi.fn(async () => 0n) },
  };
}

async function setup(
  opts: {
    servicePrice?: bigint;
    servicePrices?: Partial<Record<AssetId, bigint>>;
    allowUnsignedJobs?: boolean;
    provider?: `0x${string}`;
  } = {},
) {
  const ops = await ERC8183JobOps.create({
    walletProvider: { address: opts.provider ?? ME } as WalletProvider,
    servicePrice: opts.servicePrice,
    servicePrices: opts.servicePrices,
    allowUnsignedJobs: opts.allowUnsignedJobs ?? true,
  });
  const client = mockClient();
  (ops as unknown as { client: ERC8183Client }).client =
    client as unknown as ERC8183Client;
  return { ops, client };
}

async function signedDescription(
  commerce: `0x${string}`,
  negotiatedAt: number,
): Promise<string> {
  const handler = new NegotiationHandler({
    servicePrice: "100000",
    currency: USDC.address,
    walletProvider: {
      address: SELLER.address,
      signMessage: async (message) => ({
        signature: await SELLER.signMessage({ message }),
      }),
    },
    chainId: 97,
    verifyingContract: commerce,
    now: () => negotiatedAt,
  });
  return buildJobDescription(
    (
      await handler.negotiate({
        task_description: "Summarize",
        terms: {
          deliverables: "summary",
          quality_standards: "accurate",
          currency: USDC.address,
        },
      })
    ).toDict(),
  );
}

describe("ERC8183JobOps multi-asset verification", () => {
  it("accepts a configured non-default job token", async () => {
    const { ops, client } = await setup({
      servicePrices: { [AssetId.TEST_USDC]: 100000n },
    });
    client.jobPaymentToken.mockResolvedValue(USDC.address);
    client.getJob.mockResolvedValue(job({ budget: 100000n }));
    await expect(ops.verifyJob(1)).resolves.toMatchObject({ valid: true });
    expect(client.paymentToken).not.toHaveBeenCalled();
  });

  it("accepts a signed non-default token only when quote and job token agree", async () => {
    const negotiatedAt = Math.floor(Date.now() / 1000) - 60;
    const { ops, client } = await setup({
      provider: SELLER.address,
      allowUnsignedJobs: false,
      servicePrices: { [AssetId.TEST_USDC]: 100000n },
    });
    client.jobPaymentToken.mockResolvedValue(USDC.address);
    client.publicClient.getBlock.mockResolvedValue({
      timestamp: BigInt(negotiatedAt + 30),
    });
    client.getJob.mockResolvedValue(
      job({
        provider: SELLER.address,
        budget: 100000n,
        description: await signedDescription(
          client.commerce.address,
          negotiatedAt,
        ),
      }),
    );
    await expect(ops.verifyJob(1)).resolves.toMatchObject({ valid: true });

    client.jobPaymentToken.mockResolvedValue(USDT.address);
    await expect(ops.verifyJob(1)).resolves.toMatchObject({
      valid: false,
      error_code: ERR_JOB_TOKEN_MISMATCH,
    });
  });

  it("returns stable mismatch for unknown and malformed job tokens", async () => {
    const { ops, client } = await setup({
      servicePrices: { [AssetId.TEST_USDC]: 1n },
    });
    client.jobPaymentToken.mockResolvedValue(OTHER);
    const unknown = await ops.verifyJob(1);
    expect(unknown).toMatchObject({
      valid: false,
      error_code: ERR_JOB_TOKEN_MISMATCH,
    });
    expect(unknown.error).not.toContain("0x");

    client.jobPaymentToken.mockResolvedValue(
      "not-an-address" as unknown as `0x${string}`,
    );
    await expect(ops.verifyJob(1)).resolves.toMatchObject({
      valid: false,
      error_code: ERR_JOB_TOKEN_MISMATCH,
    });
  });

  it("classifies every job-token provider exception as retryable chain_unavailable", async () => {
    const { ops, client } = await setup();
    for (const error of [
      new Error("execution reverted"),
      new Error("rpc://secret-key timeout"),
    ]) {
      client.jobPaymentToken.mockRejectedValueOnce(error);
      const result = await ops.verifyJob(1);
      expect(result).toMatchObject({
        valid: false,
        error_code: "chain_unavailable",
        retryable: true,
      });
      expect(result.error).not.toContain("secret");
    }
  });

  it.each([
    [AssetId.TEST_USDC, USDC.address, 100000n, 6],
    [AssetId.TEST_USDT, USDT.address, 10n ** 18n, 18],
  ] as const)(
    "uses %s atomic service price and catalog decimals without conversion",
    async (assetId, token, servicePrice, decimals) => {
      const { ops, client } = await setup({
        servicePrices: { [assetId]: servicePrice },
      });
      client.jobPaymentToken.mockResolvedValue(token);
      client.getJob.mockResolvedValue(job({ budget: servicePrice - 1n }));
      const result = await ops.verifyJob(1);
      expect(result).toMatchObject({
        valid: false,
        error_code: "budget_too_low",
        service_price: servicePrice.toString(),
        decimals,
      });
      expect(client.tokenDecimals).not.toHaveBeenCalled();
    },
  );

  it("accepts zero service price without default fallback", async () => {
    const { ops, client } = await setup({
      servicePrices: { [AssetId.TEST_USDC]: 0n },
    });
    client.jobPaymentToken.mockResolvedValue(USDC.address);
    client.getJob.mockResolvedValue(job({ budget: 0n }));
    await expect(ops.verifyJob(1)).resolves.toMatchObject({ valid: true });
    expect(client.paymentToken).not.toHaveBeenCalled();
  });

  it("legacy servicePrice accepts only the Commerce default job token", async () => {
    const { ops, client } = await setup({ servicePrice: 0n });
    client.jobPaymentToken.mockResolvedValue(USDC.address);
    const result = await ops.verifyJob(1);
    expect(result.error_code).toBe(ERR_JOB_TOKEN_MISMATCH);
  });

  it("rejects symbol and cross-chain servicePrices at construction", async () => {
    for (const assetId of ["USDC", AssetId.BINANCE_PEG_USDC]) {
      await expect(
        setup({
          servicePrices: {
            [assetId]: 1n,
          } as unknown as Partial<Record<AssetId, bigint>>,
        }),
      ).rejects.toThrow();
    }
  });
});
