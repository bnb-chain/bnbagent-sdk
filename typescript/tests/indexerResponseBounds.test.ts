import { afterEach, describe, expect, it, vi } from "vitest";
import { ERC8004Agent } from "../src/erc8004/agent.js";
afterEach(() => vi.unstubAllGlobals());

describe("indexer response bounds", () => {
  it.each([true, false])(
    "rejects an oversized indexer body (declared=%s)",
    async (declared) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array(1024 * 1024 + 1));
        },
      });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(body, {
            headers: declared
              ? { "content-length": String(1024 * 1024 + 1) }
              : {},
          }),
        ),
      );
      const agent = Object.create(ERC8004Agent.prototype) as ERC8004Agent;
      Object.assign(agent, { networkConfig: { chainId: 97 } });
      await expect(agent.getAllAgents()).rejects.toThrow(/byte limit/);
    },
  );
});
