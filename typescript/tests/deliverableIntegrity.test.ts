import { afterEach, describe, expect, it, vi } from "vitest";
import type { ERC8183Client } from "../src/erc8183/client.js";
import { ERC8183JobOps } from "../src/erc8183/jobOps.js";
import { DeliverableManifest } from "../src/erc8183/schema.js";
import { JobStatus } from "../src/erc8183/types.js";
import type { StorageProvider } from "../src/storage/storageProvider.js";
const ME = `0x${"11".repeat(20)}` as const;
const OTHER = `0x${"22".repeat(20)}` as const;
afterEach(() => {
  vi.restoreAllMocks();
});
describe("committed response integrity", () => {
  const payload = (jobId = 1, chainId = 97, commerce = ME) => ({
    version: 1,
    job_id: jobId,
    chain_id: chainId,
    contracts: { commerce },
    response: { content: "work" },
    metadata: {},
  });
  async function responseOps(
    data = payload(),
    status = JobStatus.SUBMITTED,
    hash = DeliverableManifest.fromDict(data).manifestHash(),
  ) {
    const storage = {
      download: vi.fn().mockResolvedValue(data),
      upload: vi.fn(),
      exists: vi.fn(),
    } as unknown as StorageProvider;
    const ops = await ERC8183JobOps.create({
      providerAddress: ME,
      storageProvider: storage,
      responseCacheTtl: 0,
    });
    const client = {
      network: { chainId: 97 },
      commerce: { address: ME },
      getDeliverableUrl: vi.fn().mockResolvedValue("ipfs://result"),
    };
    Object.assign(ops, { client });
    vi.spyOn(ops, "getJob").mockResolvedValue({
      success: true,
      provider: ME,
      status,
      deliverable: hash,
    });
    return { ops, storage };
  }

  it("returns a manifest only after checking commitment and current job context", async () => {
    const { ops } = await responseOps();
    expect(await ops.getResponse(1)).toMatchObject({
      success: true,
      response: { content: "work" },
    });
  });

  it("accepts its HTTP response wrapper without exposing unauthenticated extra fields", async () => {
    const data = { ...payload(), success: true, extra: "uncommitted" };
    const { ops } = await responseOps(data);
    const result = await ops.getResponse(1);
    expect(result.success).toBe(true);
    expect(result.extra).toBeUndefined();
  });

  it.each(["hash", "job", "chain", "commerce"])(
    "rejects a %s mismatch",
    async (field) => {
      const data = payload(
        field === "job" ? 2 : 1,
        field === "chain" ? 56 : 97,
        field === "commerce" ? OTHER : ME,
      );
      const { ops } = await responseOps(
        data,
        JobStatus.SUBMITTED,
        field === "hash"
          ? `0x${"00".repeat(32)}`
          : DeliverableManifest.fromDict(data).manifestHash(),
      );
      expect((await ops.getResponse(1)).success).toBe(false);
    },
  );
});
