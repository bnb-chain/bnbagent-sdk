import { afterEach, describe, expect, it, vi } from "vitest";
import type { ERC8183Client } from "../src/erc8183/client.js";
import { ERC8183JobOps } from "../src/erc8183/jobOps.js";
import { DeliverableManifest } from "../src/erc8183/schema.js";
import { JobStatus } from "../src/erc8183/types.js";
import type { StorageProvider } from "../src/storage/storageProvider.js";
import type { WalletProvider } from "../src/wallets/walletProvider.js";
const ME = `0x${"11".repeat(20)}` as const;
const OTHER = `0x${"22".repeat(20)}` as const;
afterEach(() => {
  vi.restoreAllMocks();
});

describe("deliverable publication lifecycle", () => {
  async function setup() {
    const storage = {
      upload: vi.fn().mockResolvedValue("ipfs://staged"),
      download: vi.fn().mockResolvedValue({ response: "private" }),
      exists: vi.fn(),
    } as unknown as StorageProvider;
    const ops = await ERC8183JobOps.create({
      walletProvider: { address: ME } as WalletProvider,
      storageProvider: storage,
      responseCacheTtl: 0,
    });
    const submit = vi.fn().mockRejectedValue(new Error("submission failed"));
    Object.assign(ops, {
      client: {
        network: { chainId: 97 },
        commerce: { address: ME },
        router: { address: ME },
        policy: { address: ME },
        submit,
      },
    });
    vi.spyOn(ops, "verifyJob").mockResolvedValue({ valid: true });
    vi.spyOn(ops, "getJob").mockResolvedValue({
      success: true,
      provider: ME,
      status: JobStatus.FUNDED,
    });
    return { ops, storage, submit };
  }
  it("does not publish a URL when submit fails", async () => {
    const { ops, storage } = await setup();
    expect((await ops.submitResult(1, "private")).success).toBe(false);
    expect(
      (
        ops as unknown as { deliverableUrls: Map<number, string> }
      ).deliverableUrls.has(1),
    ).toBe(false);
    expect((await ops.getResponse(1)).error_code).toBe("not_found");
    expect(storage.download).not.toHaveBeenCalled();
  });
  it("checks chain lifecycle before previously staged URL maps", async () => {
    const { ops, storage } = await setup();
    (
      ops as unknown as { deliverableUrls: Map<number, string> }
    ).deliverableUrls.set(1, "ipfs://staged");
    expect((await ops.getResponse(1)).error_code).toBe("not_found");
    expect(storage.download).not.toHaveBeenCalled();
  });
  it("checks provider ownership before URL maps", async () => {
    const { ops, storage } = await setup();
    (
      ops as unknown as { deliverableUrls: Map<number, string> }
    ).deliverableUrls.set(1, "ipfs://staged");
    vi.mocked(ops.getJob).mockResolvedValue({
      success: true,
      provider: OTHER,
      status: JobStatus.SUBMITTED,
    });
    expect((await ops.getResponse(1)).error_code).toBe("not_found");
    expect(storage.download).not.toHaveBeenCalled();
  });
});
