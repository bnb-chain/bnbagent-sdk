import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createErc8183Server } from "../examples/agent-server/src/erc8183Server.js";
import { ERC8183Client } from "../src/erc8183/client.js";
import { CommerceClient } from "../src/erc8183/commerce.js";
import type { ERC8183Config } from "../src/erc8183/config.js";
import {
  ERC8183JobOps,
  type ERC8183JobOpsCreateOpts,
} from "../src/erc8183/jobOps.js";
import { NegotiationHandler } from "../src/erc8183/negotiation.js";
import { PolicyClient } from "../src/erc8183/policy.js";
import { JobStatus } from "../src/erc8183/types.js";
import { RpcRangeLimitError } from "../src/errors.js";
import type { StorageProvider } from "../src/storage/storageProvider.js";
import { RateLimitExceeded, SlidingWindowLimiter } from "../src/utils/index.js";
import { mockPublicClient } from "./helpers/mockTransport.js";

const ME = `0x${"11".repeat(20)}` as const;
const sockets: Server[] = [];

afterEach(async () => {
  for (const server of sockets.splice(0)) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

async function setup(
  status = JobStatus.SUBMITTED,
  options: Partial<ERC8183JobOpsCreateOpts> = {},
) {
  const rpc = mockPublicClient({ eth_blockNumber: () => "0x186a0" });
  const commerce = new CommerceClient(rpc.client, ME);
  const getJob = vi.spyOn(commerce, "getJob").mockResolvedValue({
    id: 1n,
    client: ME,
    provider: ME,
    evaluator: ME,
    hook: ME,
    description: "",
    budget: 1n,
    expiredAt: 9999999999n,
    submittedAt: 0n,
    deliverable: `0x${"00".repeat(32)}`,
    status,
  });
  const policy = new PolicyClient(rpc.client, ME);
  const client = Object.assign(
    Object.create(ERC8183Client.prototype) as ERC8183Client,
    {
      client: rpc.client,
      commerce,
      policy,
    },
  );
  vi.spyOn(ERC8183Client, "create").mockResolvedValue(client);
  const storage = {
    download: vi.fn().mockResolvedValue({ response: "ready" }),
    upload: vi.fn(),
    exists: vi.fn(),
  } as unknown as StorageProvider;
  const ops = await ERC8183JobOps.create({
    providerAddress: ME,
    storageProvider: storage,
    ...options,
  });
  const logs = () => rpc.calls.filter((call) => call.method === "eth_getLogs");
  return { rpc, client, getJob, policy, ops, storage, logs };
}

describe("response lookup RPC budgets", () => {
  it("evicts success payloads by byte size below the entry ceiling", async () => {
    const { ops, client, storage } = await setup();
    vi.spyOn(client, "getDeliverableUrl").mockResolvedValue(
      "ipfs://large-result",
    );
    vi.mocked(storage.download).mockResolvedValue({
      response: "x".repeat(6 * 1024 * 1024),
    });
    for (const id of [1, 2, 3, 1])
      expect((await ops.getResponse(id)).success).toBe(true);
    expect(storage.download).toHaveBeenCalledTimes(4);
  });
  it("rejects foreign providers before scanning or downloading", async () => {
    const { ops, getJob, storage, logs } = await setup();
    const original = getJob.getMockImplementation();
    if (!original) throw new Error("Missing job fixture");
    const job = await original(1n);
    getJob.mockResolvedValue({ ...job, provider: `0x${"22".repeat(20)}` });
    expect((await ops.getResponse(1)).error_code).toBe("not_found");
    expect(storage.download).not.toHaveBeenCalled();
    expect(logs()).toHaveLength(0);
  });

  it("caches successes with bounded capacity and independent payloads", async () => {
    const { ops, client, storage } = await setup(JobStatus.SUBMITTED, {
      responseCacheMaxEntries: 2,
    });
    vi.spyOn(client, "getDeliverableUrl").mockResolvedValue("ipfs://result");
    const result = await ops.getResponse(1);
    result.response = "mutated";
    expect((await ops.getResponse(1)).response).toBe("ready");
    expect(storage.download).toHaveBeenCalledTimes(1);
    for (const id of [2, 3, 1]) await ops.getResponse(id);
    expect(storage.download).toHaveBeenCalledTimes(4);
  });
  it("scans exactly 50,000 submission blocks and reuses a failed lookup", async () => {
    const { ops, logs } = await setup();
    for (let n = 0; n < 3; n++) {
      expect((await ops.getResponse(1)).error_code).toBe("chain_unavailable");
    }
    expect(logs()).toHaveLength(51); // 50 submission windows + one policy lookup.
    const blocks = logs()
      .slice(0, 50)
      .reduce((total, call) => {
        const range = call.params[0] as { fromBlock: string; toBlock: string };
        return total + BigInt(range.toBlock) - BigInt(range.fromBlock) + 1n;
      }, 0n);
    expect(blocks).toBe(50_000n);
  });

  it.each([JobStatus.OPEN, JobStatus.FUNDED])(
    "never scans logs for unsubmitted status %s",
    async (status) => {
      const { ops, logs, getJob } = await setup(status);
      for (let n = 0; n < 3; n++)
        expect((await ops.getResponse(1)).error_code).toBe("not_found");
      expect(logs()).toHaveLength(0);
      expect(getJob).toHaveBeenCalledTimes(1);
    },
  );

  it("shares concurrent requests for the same job", async () => {
    const { ops, logs } = await setup();
    await Promise.all(Array.from({ length: 20 }, () => ops.getResponse(1)));
    expect(logs()).toHaveLength(51);
  });

  it("rejects distinct excess jobs before adding RPC work", async () => {
    const { ops, logs } = await setup(JobStatus.SUBMITTED, {
      responseMaxInflight: 2,
    });
    const results = await Promise.all(
      [1, 2, 3].map((id) => ops.getResponse(id)),
    );
    expect(results[2]).toMatchObject({
      error_code: "chain_unavailable",
      retryable: true,
    });
    expect(logs()).toHaveLength(102);
  });

  it("expires failures and allows a later successful response", async () => {
    const now = vi.spyOn(performance, "now").mockReturnValue(1000);
    const { ops, client, logs } = await setup(JobStatus.SUBMITTED, {
      responseCacheTtl: 0.01,
    });
    await ops.getResponse(1);
    const resolveUrl = vi
      .spyOn(client, "getDeliverableUrl")
      .mockResolvedValue("ipfs://new");
    expect((await ops.getResponse(1)).success).toBe(false);
    now.mockReturnValue(1011);
    expect((await ops.getResponse(1)).success).toBe(true);
    expect(resolveUrl).toHaveBeenCalledTimes(1);
    expect(logs()).toHaveLength(51);
  });

  it("bounds failure cache storage and permits disabling it", async () => {
    const { ops, logs } = await setup(JobStatus.SUBMITTED, {
      responseCacheMaxEntries: 2,
    });
    for (const id of [1, 2, 3, 1]) await ops.getResponse(id);
    expect(logs()).toHaveLength(204);
    const other = await ERC8183JobOps.create({
      providerAddress: ME,
      storageProvider: {
        download: vi.fn(),
        upload: vi.fn(),
        exists: vi.fn(),
      } as unknown as StorageProvider,
      responseCacheTtl: 0,
    });
    await other.getResponse(1);
    await other.getResponse(1);
    expect(logs()).toHaveLength(306);
  });

  it.each([0, -1, Number.MAX_SAFE_INTEGER + 1, Number.NaN, 1.5])(
    "rejects invalid job id %s before RPC",
    async (id) => {
      const { ops, rpc } = await setup();
      expect((await ops.getResponse(id)).error_code).toBe("not_found");
      expect(rpc.calls).toHaveLength(0);
    },
  );

  it("never queries genesis when the block height cannot be read", async () => {
    const { policy, rpc } = await setup();
    vi.spyOn(rpc.client, "getBlockNumber").mockRejectedValue(
      new Error("RPC unavailable"),
    );
    await expect(policy.getDeliverableUrl(1n)).rejects.toBeInstanceOf(
      RpcRangeLimitError,
    );
    expect(
      rpc.calls.filter((call) => call.method === "eth_getLogs"),
    ).toHaveLength(0);
  });

  async function serve(
    options: Parameters<typeof createErc8183Server>[0] = {},
  ) {
    const fixture = await setup();
    vi.spyOn(fixture.client, "paymentToken").mockResolvedValue(ME);
    vi.spyOn(fixture.client, "tokenDecimals").mockResolvedValue(18);
    vi.spyOn(NegotiationHandler, "fromErc8183Client").mockResolvedValue(
      {} as NegotiationHandler,
    );
    const app = await createErc8183Server({
      config: {
        walletProvider: { address: ME },
        effectiveNetwork: "bsc-testnet",
        storage: fixture.storage,
        servicePrice: "0",
        agentUrl: null,
      } as unknown as ERC8183Config,
      ...options,
    });
    const server = app.listen(0, "127.0.0.1");
    sockets.push(server);
    await once(server, "listening");
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/erc8183/job`;
    return { ...fixture, base, jobOps: app.jobOps };
  }

  it("does not charge the global limit for rejected clients", async () => {
    const check = vi.fn();
    const { base } = await serve({
      responseLimiter: new SlidingWindowLimiter(1, 60),
      globalResponseLimiter: { check },
    });
    await fetch(`${base}/1/response`);
    for (let i = 0; i < 10; i++)
      expect((await fetch(`${base}/2/response`)).status).toBe(429);
    expect(check).toHaveBeenCalledTimes(1);
  });

  it("shares the response budget with verification", async () => {
    const { base, jobOps } = await serve({
      responseLimiter: new SlidingWindowLimiter(1, 60),
    });
    const verify = vi
      .spyOn(jobOps, "verifyJob")
      .mockResolvedValue({ valid: false, error_code: "quote_invalid" });
    expect((await fetch(`${base}/1/verify`)).status).toBe(400);
    expect((await fetch(`${base}/1/verify`)).status).toBe(429);
    expect((await fetch(`${base}/1/response`)).status).toBe(429);
    expect(verify).toHaveBeenCalledTimes(1);
  });

  it("limits anonymous requests before RPC and ignores spoofed forwarding headers", async () => {
    const { base, logs } = await serve({
      responseLimiter: new SlidingWindowLimiter(1, 60),
    });
    expect((await fetch(`${base}/1/response`)).status).toBe(503);
    expect(
      (
        await fetch(`${base}/2/response`, {
          headers: { "X-Forwarded-For": "8.8.8.8" },
        })
      ).status,
    ).toBe(429);
    expect(logs()).toHaveLength(51);
  });

  it("honors an async global limiter before SDK/RPC", async () => {
    const { base, logs, getJob } = await serve({
      globalResponseLimiter: {
        check: async () => {
          throw new RateLimitExceeded();
        },
      },
    });
    expect((await fetch(`${base}/1/response`)).status).toBe(429);
    expect(logs()).toHaveLength(0);
    expect(getJob).not.toHaveBeenCalled();
  });

  it("reads response rate limits from the environment", async () => {
    vi.stubEnv("ERC8183_RESPONSE_GLOBAL_RATE_LIMIT", "1");
    const { base, logs } = await serve();
    expect((await fetch(`${base}/1/response`)).status).toBe(503);
    expect((await fetch(`${base}/2/response`)).status).toBe(429);
    expect(logs()).toHaveLength(51);
  });
});
