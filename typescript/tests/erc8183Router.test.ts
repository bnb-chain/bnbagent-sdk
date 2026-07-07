/**
 * Ports the `RouterClient`-relevant slice of `python/tests/test_erc8183_router.py`:
 * writes (smoke-tested end-to-end through the default `LocalExecutor` write
 * path — the intent shape itself is covered exhaustively in
 * `erc8183Intents.test.ts`), views decoded from a hand-encoded `eth_call`
 * result, and event helpers flattened from a hand-encoded `eth_getLogs`
 * result.
 */

import {
  type TransactionRequestLegacy,
  decodeFunctionData,
  encodeAbiParameters,
  encodeEventTopics,
  encodeFunctionResult,
  getAddress,
} from "viem";
import { describe, expect, it } from "vitest";
import { evaluatorRouterAbi } from "../src/abis/evaluatorRouter.js";
import { RouterClient } from "../src/erc8183/router.js";
import { JobStatus, Verdict } from "../src/erc8183/types.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import { FAKE_TX_HASH, mockPublicClient } from "./helpers/mockTransport.js";

const CONTRACT_ADDRESS = getAddress(`0x${"aa".repeat(20)}`);
const WALLET_ADDRESS = getAddress(`0x${"99".repeat(20)}`);
const COMMERCE = getAddress(`0x${"bb".repeat(20)}`);
const POLICY = getAddress(`0x${"cc".repeat(20)}`);
const CLIENT = getAddress(`0x${"dd".repeat(20)}`);

/** A minimal signing wallet — drives the default LocalExecutor write path. */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  get address(): `0x${string}` {
    return WALLET_ADDRESS;
  }

  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    void tx;
    return {
      rawTransaction: "0xdeadbeef",
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }
}

function readRouter(
  results: Record<string, unknown>,
): (params: readonly unknown[]) => unknown {
  return (params) => {
    const [{ data }] = params as [{ data: `0x${string}` }];
    const decoded = decodeFunctionData({ abi: evaluatorRouterAbi, data });
    const result = results[decoded.functionName];
    return encodeFunctionResult({
      abi: evaluatorRouterAbi,
      functionName: decoded.functionName,
      // biome-ignore lint/suspicious/noExplicitAny: result shape varies per function
      result: result as any,
    });
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  const mock = mockPublicClient({ eth_call: readRouter(overrides) });
  const wallet = new StubWallet();
  const client = new RouterClient(mock.client, CONTRACT_ADDRESS, wallet);
  return { mock, wallet, client };
}

describe("RouterClient constructor", () => {
  it("checksums the contract address", () => {
    const { client } = makeClient();
    expect(client.address).toBe(CONTRACT_ADDRESS);
  });
});

describe("RouterClient: writes (smoke, via default LocalExecutor)", () => {
  it("registerJob broadcasts and returns a success result", async () => {
    const { client, mock } = makeClient();
    const result = await client.registerJob(1n, POLICY);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("settle broadcasts and returns a success result", async () => {
    const { client } = makeClient();
    const result = await client.settle(1n, "0x01");
    expect(result.status).toBe(1);
  });

  it("markExpired broadcasts and returns a success result", async () => {
    const { client } = makeClient();
    const result = await client.markExpired(1n);
    expect(result.status).toBe(1);
  });
});

describe("RouterClient: views", () => {
  it("commerce()", async () => {
    const { client } = makeClient({ commerce: COMMERCE });
    await expect(client.commerce()).resolves.toBe(COMMERCE);
  });

  it("inflightJobCount()", async () => {
    const { client } = makeClient({ inflightJobCount: 5n });
    await expect(client.inflightJobCount()).resolves.toBe(5n);
  });

  it("jobPolicy()", async () => {
    const { client } = makeClient({ jobPolicy: POLICY });
    await expect(client.jobPolicy(1n)).resolves.toBe(POLICY);
  });

  it("policyWhitelist()", async () => {
    const { client } = makeClient({ policyWhitelist: true });
    await expect(client.policyWhitelist(POLICY)).resolves.toBe(true);
  });

  it("paused()", async () => {
    const { client } = makeClient({ paused: false });
    await expect(client.paused()).resolves.toBe(false);
  });
});

describe("RouterClient: event helpers", () => {
  function rawLog(
    topics: (`0x${string}` | `0x${string}`[] | null)[],
    data: `0x${string}`,
  ) {
    return {
      address: CONTRACT_ADDRESS,
      topics,
      data,
      blockNumber: "0x64",
      blockHash: `0x${"aa".repeat(32)}`,
      transactionHash: FAKE_TX_HASH,
      transactionIndex: "0x0",
      logIndex: "0x0",
      removed: false,
    };
  }

  it("getJobRegisteredEvents flattens jobId/policy/client", async () => {
    const topics = encodeEventTopics({
      abi: evaluatorRouterAbi,
      eventName: "JobRegistered",
      args: { jobId: 1n, policy: POLICY, client: CLIENT },
    });
    const mock = mockPublicClient({
      eth_getLogs: () => [rawLog(topics, "0x")],
    });
    const client = new RouterClient(mock.client, CONTRACT_ADDRESS);
    const events = await client.getJobRegisteredEvents(0n);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      jobId: 1n,
      policy: POLICY,
      client: CLIENT,
    });
  });

  it("getJobSettledEvents flattens jobId/verdict/reason and maps to the Verdict enum", async () => {
    const reason = `0x${"11".repeat(32)}` as const;
    const topics = encodeEventTopics({
      abi: evaluatorRouterAbi,
      eventName: "JobSettled",
      args: { jobId: 1n, policy: POLICY, verdict: Verdict.APPROVE },
    });
    const data = encodeAbiParameters([{ type: "bytes32" }], [reason]);
    const mock = mockPublicClient({
      eth_getLogs: () => [rawLog(topics, data)],
    });
    const client = new RouterClient(mock.client, CONTRACT_ADDRESS);
    const events = await client.getJobSettledEvents(
      0n,
      "latest",
      Verdict.APPROVE,
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.jobId).toBe(1n);
    expect(events[0]?.verdict).toBe(Verdict.APPROVE);
    expect(events[0]?.reason).toBe(reason);
  });

  it("getJobFinalisedEvents flattens jobId/status and maps to the JobStatus enum", async () => {
    const topics = encodeEventTopics({
      abi: evaluatorRouterAbi,
      eventName: "JobFinalised",
      args: { jobId: 1n, status: JobStatus.COMPLETED },
    });
    const mock = mockPublicClient({
      eth_getLogs: () => [rawLog(topics, "0x")],
    });
    const client = new RouterClient(mock.client, CONTRACT_ADDRESS);
    const events = await client.getJobFinalisedEvents(
      0n,
      "latest",
      JobStatus.COMPLETED,
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.jobId).toBe(1n);
    expect(events[0]?.status).toBe(JobStatus.COMPLETED);
  });

  it("returns an empty array when there are no logs", async () => {
    const mock = mockPublicClient({ eth_getLogs: () => [] });
    const client = new RouterClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.getJobRegisteredEvents(0n)).resolves.toEqual([]);
  });
});
