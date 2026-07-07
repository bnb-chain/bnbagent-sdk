/**
 * Ports the `PolicyClient`-relevant slice of `python/tests/test_erc8183_policy.py`:
 * writes (`dispute`/`voteReject`, smoke-tested end-to-end through the
 * default `LocalExecutor` write path — the intent shape itself is covered
 * exhaustively in `erc8183Intents.test.ts`), the owner-only admin ops
 * (`addVoter`/`removeVoter`/`setQuorum`, which always go through `sendTx`;
 * the invariant that they never touch the intent seam is covered in
 * `erc8183Intents.test.ts`), views decoded from a hand-encoded `eth_call`
 * result, and `getDeliverableUrl`'s window resolution, JSON parsing, and
 * `RpcRangeLimitError` classification.
 */

import {
  type TransactionRequestLegacy,
  decodeFunctionData,
  encodeAbiParameters,
  encodeEventTopics,
  encodeFunctionResult,
  getAddress,
  stringToHex,
} from "viem";
import { describe, expect, it } from "vitest";
import { optimisticPolicyAbi } from "../src/abis/optimisticPolicy.js";
import { PolicyClient } from "../src/erc8183/policy.js";
import { Verdict } from "../src/erc8183/types.js";
import { RpcRangeLimitError } from "../src/errors.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import { FAKE_TX_HASH, mockPublicClient } from "./helpers/mockTransport.js";

const CONTRACT_ADDRESS = getAddress(`0x${"aa".repeat(20)}`);
const WALLET_ADDRESS = getAddress(`0x${"99".repeat(20)}`);
const VOTER = getAddress(`0x${"55".repeat(20)}`);
const COMMERCE = getAddress(`0x${"bb".repeat(20)}`);
const ROUTER = getAddress(`0x${"cc".repeat(20)}`);
const ADMIN = getAddress(`0x${"dd".repeat(20)}`);

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

function readPolicy(
  results: Record<string, unknown>,
): (params: readonly unknown[]) => unknown {
  return (params) => {
    const [{ data }] = params as [{ data: `0x${string}` }];
    const decoded = decodeFunctionData({ abi: optimisticPolicyAbi, data });
    const result = results[decoded.functionName];
    return encodeFunctionResult({
      abi: optimisticPolicyAbi,
      functionName: decoded.functionName,
      // biome-ignore lint/suspicious/noExplicitAny: result shape varies per function
      result: result as any,
    });
  };
}

function makeClient(overrides: Record<string, unknown> = {}) {
  const mock = mockPublicClient({ eth_call: readPolicy(overrides) });
  const wallet = new StubWallet();
  const client = new PolicyClient(mock.client, CONTRACT_ADDRESS, wallet);
  return { mock, wallet, client };
}

describe("PolicyClient constructor", () => {
  it("checksums the contract address", () => {
    const { client } = makeClient();
    expect(client.address).toBe(CONTRACT_ADDRESS);
  });
});

describe("PolicyClient: writes (smoke, via default LocalExecutor)", () => {
  it("dispute broadcasts and returns a success result", async () => {
    const { client, mock } = makeClient();
    const result = await client.dispute(1n);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("voteReject broadcasts and returns a success result", async () => {
    const { client } = makeClient();
    const result = await client.voteReject(1n);
    expect(result.status).toBe(1);
  });
});

describe("PolicyClient: admin writes (sendTx)", () => {
  it("addVoter broadcasts and returns a success result", async () => {
    const { client, mock } = makeClient();
    const result = await client.addVoter(VOTER);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("removeVoter broadcasts and returns a success result", async () => {
    const { client } = makeClient();
    const result = await client.removeVoter(VOTER);
    expect(result.status).toBe(1);
  });

  it("setQuorum broadcasts and returns a success result", async () => {
    const { client } = makeClient();
    const result = await client.setQuorum(3);
    expect(result.status).toBe(1);
  });
});

describe("PolicyClient: views", () => {
  it("check() maps the verdict int to the Verdict enum", async () => {
    const reason = `0x${"11".repeat(32)}` as const;
    const { client } = makeClient({ check: [Verdict.APPROVE, reason] });
    const [verdict, returnedReason] = await client.check(1n);
    expect(verdict).toBe(Verdict.APPROVE);
    expect(returnedReason).toBe(reason);
  });

  it("submittedAt()", async () => {
    const { client } = makeClient({ submittedAt: 1000n });
    await expect(client.submittedAt(1n)).resolves.toBe(1000n);
  });

  it("disputed()", async () => {
    const { client } = makeClient({ disputed: true });
    await expect(client.disputed(1n)).resolves.toBe(true);
  });

  it("rejectVotes()", async () => {
    const { client } = makeClient({ rejectVotes: 5 });
    await expect(client.rejectVotes(1n)).resolves.toBe(5);
  });

  it("hasVoted()", async () => {
    const { client } = makeClient({ hasVoted: true });
    await expect(client.hasVoted(1n, VOTER)).resolves.toBe(true);
  });

  it("isVoter()", async () => {
    const { client } = makeClient({ isVoter: true });
    await expect(client.isVoter(VOTER)).resolves.toBe(true);
  });

  it("disputeWindow()", async () => {
    const { client } = makeClient({ disputeWindow: 86400n });
    await expect(client.disputeWindow()).resolves.toBe(86400n);
  });

  it("voteQuorum()", async () => {
    const { client } = makeClient({ voteQuorum: 3 });
    await expect(client.voteQuorum()).resolves.toBe(3);
  });

  it("disputeQuorumSnapshot()", async () => {
    const { client } = makeClient({ disputeQuorumSnapshot: 2 });
    await expect(client.disputeQuorumSnapshot(1n)).resolves.toBe(2);
  });

  it("activeVoterCount()", async () => {
    const { client } = makeClient({ activeVoterCount: 10 });
    await expect(client.activeVoterCount()).resolves.toBe(10);
  });

  it("admin()", async () => {
    const { client } = makeClient({ admin: ADMIN });
    await expect(client.admin()).resolves.toBe(ADMIN);
  });

  it("commerce()", async () => {
    const { client } = makeClient({ commerce: COMMERCE });
    await expect(client.commerce()).resolves.toBe(COMMERCE);
  });

  it("router()", async () => {
    const { client } = makeClient({ router: ROUTER });
    await expect(client.router()).resolves.toBe(ROUTER);
  });
});

describe("PolicyClient: getDeliverableUrl", () => {
  function jobInitialisedLog(jobId: bigint, optParams: `0x${string}`) {
    const topics = encodeEventTopics({
      abi: optimisticPolicyAbi,
      eventName: "JobInitialised",
      args: { jobId },
    });
    const data = encodeAbiParameters(
      [
        { type: "bytes32", name: "deliverable" },
        { type: "uint64", name: "submittedAt" },
        { type: "bytes", name: "optParams" },
      ],
      [`0x${"aa".repeat(32)}`, 1000n, optParams],
    );
    return {
      address: CONTRACT_ADDRESS,
      topics,
      data,
      blockNumber: "0x64",
      blockHash: `0x${"bb".repeat(32)}`,
      transactionHash: FAKE_TX_HASH,
      transactionIndex: "0x0",
      logIndex: "0x0",
      removed: false,
    };
  }

  it("parses deliverable_url out of optParams JSON", async () => {
    const optParams = stringToHex('{"deliverable_url": "http://test"}');
    const mock = mockPublicClient({
      eth_getLogs: () => [jobInitialisedLog(1n, optParams)],
    });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).resolves.toBe("http://test");
  });

  it("returns null when there are no logs", async () => {
    const mock = mockPublicClient({ eth_getLogs: () => [] });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).resolves.toBeNull();
  });

  it("returns null when optParams has no deliverable_url field", async () => {
    const optParams = stringToHex('{"other": "value"}');
    const mock = mockPublicClient({
      eth_getLogs: () => [jobInitialisedLog(1n, optParams)],
    });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).resolves.toBeNull();
  });

  it("returns null when optParams is not valid JSON", async () => {
    const optParams = stringToHex("not json");
    const mock = mockPublicClient({
      eth_getLogs: () => [jobInitialisedLog(1n, optParams)],
    });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).resolves.toBeNull();
  });

  it("scans a tight +-10 block window around hintBlock", async () => {
    const optParams = stringToHex('{"deliverable_url": "http://test"}');
    const mock = mockPublicClient({
      eth_getLogs: () => [jobInitialisedLog(1n, optParams)],
    });
    await new PolicyClient(mock.client, CONTRACT_ADDRESS).getDeliverableUrl(
      1n,
      { hintBlock: 500n },
    );
    const call = mock.calls.find((c) => c.method === "eth_getLogs");
    const params = call?.params[0] as { fromBlock: string; toBlock: string };
    expect(params.fromBlock).toBe("0x1ea"); // 490
    expect(params.toBlock).toBe("0x1fe"); // 510
  });

  it("falls back to a 1000-block window ending at latest when hintBlock is absent", async () => {
    // default eth_blockNumber mock handler returns 1000
    const mock = mockPublicClient({ eth_getLogs: () => [] });
    await new PolicyClient(mock.client, CONTRACT_ADDRESS).getDeliverableUrl(1n);
    const call = mock.calls.find((c) => c.method === "eth_getLogs");
    const params = call?.params[0] as { fromBlock: string; toBlock: string };
    expect(params.fromBlock).toBe("0x0"); // max(0, 1000 - 1000)
    expect(params.toBlock).toBe("latest");
  });

  it("raises RpcRangeLimitError on a -32005/limit exceeded error instead of returning null", async () => {
    const mock = mockPublicClient({
      eth_getLogs: () => {
        throw new Error("limit exceeded");
      },
    });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).rejects.toBeInstanceOf(RpcRangeLimitError);
  });

  it("swallows other event-query errors and returns null", async () => {
    const mock = mockPublicClient({
      eth_getLogs: () => {
        throw new Error("boom");
      },
    });
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS);
    await expect(
      client.getDeliverableUrl(1n, { hintBlock: 100n }),
    ).resolves.toBeNull();
  });
});
