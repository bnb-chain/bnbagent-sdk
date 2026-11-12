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
import {
  discoverDisputes,
  disputeWindowStart,
} from "../examples/voter/watch.js";
describe("voter startup recovery", () => {
  it("replays an existing dispute and bounds each log page", async () => {
    const getLogs = vi.fn().mockResolvedValue([{ args: { jobId: 7n } }]);
    const client = {
      publicClient: {
        getBlockNumber: vi.fn().mockResolvedValue(10_000n),
        getLogs,
      },
      policy: { address: ME },
    } as unknown as ERC8183Client;
    const watching = new Set<number>();
    expect(await discoverDisputes(client, 100n, watching)).toBe(2100n);
    expect(watching.has(7)).toBe(true);
    expect(getLogs).toHaveBeenCalledWith(
      expect.objectContaining({ fromBlock: 100n, toBlock: 2099n }),
    );
  });

  it("finds the exact live dispute window rather than relying on startup counter", async () => {
    const getBlock = vi.fn(
      async ({ blockNumber }: { blockNumber: bigint }) => ({
        timestamp: blockNumber * 2n,
      }),
    );
    const client = {
      publicClient: {
        getBlockNumber: vi.fn().mockResolvedValue(1000n),
        getBlock,
      },
      policy: { disputeWindow: vi.fn().mockResolvedValue(600n) },
    } as unknown as ERC8183Client;
    expect(await disputeWindowStart(client)).toBe(700n);
    expect(getBlock.mock.calls.length).toBeLessThanOrEqual(12);
  });
});
