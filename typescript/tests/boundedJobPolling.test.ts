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
describe("bounded polling", () => {
  it("pages a very large counter without retaining every global job", async () => {
    const ops = await ERC8183JobOps.create({ providerAddress: ME });
    const getJobsBatch = vi.fn().mockResolvedValue([]);
    Object.assign(ops, {
      client: {
        commerce: {
          jobCounter: vi.fn().mockResolvedValue(1_000_000_000n),
          getJobsBatch,
        },
      },
    });
    await ops.getPendingJobs();
    await ops.getPendingJobs();
    expect(getJobsBatch.mock.calls[0]?.[0]).toHaveLength(256);
    expect(getJobsBatch.mock.calls[1]?.[0]).toEqual(
      Array.from({ length: 256 }, (_, i) => BigInt(i + 257)),
    );
  });
});
