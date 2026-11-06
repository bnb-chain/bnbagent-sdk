import { afterEach, describe, expect, it, vi } from "vitest";
import { IPFSStorageProvider } from "../src/storage/ipfsStorageProvider.js";
afterEach(() => vi.unstubAllGlobals());

describe("IPFS pinning response bounds", () => {
  it.each([true, false])(
    "bounds pinning response before JSON parse (declared=%s)",
    async (declared) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array(65537));
        },
      });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(body, {
            headers: declared ? { "content-length": "65537" } : {},
          }),
        ),
      );
      await expect(
        new IPFSStorageProvider("https://pin.example", "key").upload({
          ok: true,
        }),
      ).rejects.toThrow(/byte limit/);
    },
  );
});
