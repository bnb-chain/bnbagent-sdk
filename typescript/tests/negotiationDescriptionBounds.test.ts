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
import { NegotiationHandler } from "../src/erc8183/negotiation.js";
describe("description preflight", () => {
  it("rejects oversized terms without invoking a quote signer", async () => {
    const signMessage = vi.fn();
    const handler = new NegotiationHandler({
      servicePrice: "1",
      currency: ME,
      walletProvider: { address: ME, signMessage },
      chainId: 97,
      verifyingContract: ME,
    });
    const result = await handler.negotiate({
      task_description: "x".repeat(5000),
      terms: { deliverables: "report", quality_standards: "good" },
    });
    expect(result.toDict().response).toMatchObject({ accepted: false });
    expect(signMessage).not.toHaveBeenCalled();
  });
});
