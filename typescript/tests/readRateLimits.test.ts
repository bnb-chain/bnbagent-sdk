import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, expect, it, vi } from "vitest";
import { createErc8183Server } from "../examples/agent-server/src/erc8183Server.js";
import { ERC8183Client } from "../src/erc8183/client.js";
import type { ERC8183Config } from "../src/erc8183/config.js";
import { ERC8183JobOps } from "../src/erc8183/jobOps.js";
import { NegotiationHandler } from "../src/erc8183/negotiation.js";
import { SlidingWindowLimiter } from "../src/utils/index.js";

const ME = `0x${"11".repeat(20)}` as const;
const servers: Server[] = [];
afterEach(async () => {
  for (const server of servers.splice(0)) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
  vi.restoreAllMocks();
});

it("charges anonymous job detail and response requests to the same read budget before RPC", async () => {
  vi.spyOn(ERC8183Client, "create").mockResolvedValue({
    paymentToken: vi.fn().mockResolvedValue(ME),
    tokenDecimals: vi.fn().mockResolvedValue(18),
  } as unknown as ERC8183Client);
  const getJob = vi.fn().mockResolvedValue({ success: true });
  const getResponse = vi.fn();
  vi.spyOn(ERC8183JobOps, "create").mockResolvedValue({
    agentAddress: ME,
    getJob,
    getResponse,
  } as unknown as ERC8183JobOps);
  vi.spyOn(NegotiationHandler, "fromErc8183Client").mockResolvedValue(
    {} as NegotiationHandler,
  );
  const app = await createErc8183Server({
    config: {
      walletProvider: { address: ME },
      effectiveNetwork: "bsc-testnet",
      storage: null,
      servicePrice: "1",
      agentUrl: null,
    } as unknown as ERC8183Config,
    responseLimiter: new SlidingWindowLimiter(1, 60),
  });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await once(server, "listening");
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/erc8183/job`;
  expect((await fetch(`${base}/1`)).status).toBe(200);
  expect((await fetch(`${base}/2`)).status).toBe(429);
  expect((await fetch(`${base}/2/response`)).status).toBe(429);
  expect(getJob).toHaveBeenCalledTimes(1);
  expect(getResponse).not.toHaveBeenCalled();
});
