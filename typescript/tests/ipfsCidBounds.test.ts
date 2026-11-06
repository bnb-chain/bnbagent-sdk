import { afterEach, describe, expect, it, vi } from "vitest";
import { IPFSStorageProvider } from "../src/storage/ipfsStorageProvider.js";
afterEach(() => vi.unstubAllGlobals());
describe("IPFS CID bounds", () => {
  it("rejects attacker-sized CIDs before network access", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const provider = new IPFSStorageProvider("https://pin.example", "key");
    await expect(
      provider.download(`ipfs://b${"a".repeat(100000)}`),
    ).rejects.toThrow(/CID/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
