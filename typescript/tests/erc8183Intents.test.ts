/**
 * Ports the intent-construction slice of `python/tests/test_erc8183_intents.py`:
 * every write on `CommerceClient` / `RouterClient` / `PolicyClient` builds a
 * dual-representation `Intent` (semantic `name` + `kwargs`, mechanical
 * `call`) and runs it through `ContractBase.executeIntent`. These tests
 * capture the Intent at a stub executor (`RecordingExecutor`, injected via a
 * stub wallet's `makeExecutor`) and assert:
 *
 * - `name` matches the `ERC8183_*` constant,
 * - `kwargs` carries the documented high-level keys,
 * - `call.functionName` / `call.args` match the mechanical call.
 *
 * Also covered: `createJob`'s jobId dual-sourcing (executor-supplied vs.
 * parsed from the `JobCreated` receipt event), the policy admin ops
 * (`addVoter`/`removeVoter`/`setQuorum`) staying on `sendTx` instead of the
 * intent seam, and paymaster wiring into the executor's `ExecutionContext`.
 */

import {
  type PublicClient,
  type TransactionRequestLegacy,
  decodeFunctionData,
  encodeAbiParameters,
  encodeEventTopics,
  encodeFunctionResult,
  getAddress,
} from "viem";
import { describe, expect, it, vi } from "vitest";
import { agenticCommerceAbi } from "../src/abis/agenticCommerce.js";
import type { Paymaster } from "../src/core/paymaster.js";
import { CommerceClient } from "../src/erc8183/commerce.js";
import { PolicyClient } from "../src/erc8183/policy.js";
import { RouterClient } from "../src/erc8183/router.js";
import { ZERO_ADDRESS, ZERO_REASON } from "../src/erc8183/types.js";
import {
  ERC8183_CLAIM_REFUND,
  ERC8183_COMPLETE,
  ERC8183_CREATE_JOB,
  ERC8183_DISPUTE,
  ERC8183_FUND,
  ERC8183_MARK_EXPIRED,
  ERC8183_REGISTER_JOB,
  ERC8183_REJECT,
  ERC8183_SETTLE,
  ERC8183_SET_BUDGET,
  ERC8183_SET_PROVIDER,
  ERC8183_SUBMIT,
  ERC8183_VOTE_REJECT,
  type ExecutionContext,
  type Intent,
  type IntentExecutor,
  type TxResult,
} from "../src/wallets/intents.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import { FAKE_TX_HASH, mockPublicClient } from "./helpers/mockTransport.js";

const CONTRACT_ADDRESS = getAddress(`0x${"aa".repeat(20)}`);
const WALLET_ADDRESS = getAddress(`0x${"99".repeat(20)}`);
const PROVIDER = getAddress(`0x${"11".repeat(20)}`);
const EVALUATOR = getAddress(`0x${"22".repeat(20)}`);
const HOOK = getAddress(`0x${"33".repeat(20)}`);
const POLICY = getAddress(`0x${"44".repeat(20)}`);
const VOTER = getAddress(`0x${"55".repeat(20)}`);

/** Stub IntentExecutor: records every intent, returns a canonical result. */
class RecordingExecutor implements IntentExecutor {
  intents: Intent[] = [];
  constructor(
    private result: TxResult = {
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt: null,
    },
  ) {}

  async execute(intent: Intent): Promise<TxResult> {
    this.intents.push(intent);
    return { ...this.result };
  }
}

/** A minimal wallet whose makeExecutor is fully controlled by the test. */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  contexts: ExecutionContext[] = [];
  makeExecutorImpl: ((context: ExecutionContext) => IntentExecutor) | null =
    null;

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

  override makeExecutor(context: ExecutionContext): IntentExecutor {
    this.contexts.push(context);
    if (!this.makeExecutorImpl) {
      throw new Error("StubWallet.makeExecutorImpl not configured");
    }
    return this.makeExecutorImpl(context);
  }
}

function commerceWithExecutor(result?: TxResult, paymaster?: Paymaster | null) {
  const mock = mockPublicClient();
  const executor = new RecordingExecutor(result);
  const wallet = new StubWallet();
  wallet.makeExecutorImpl = () => executor;
  const client = new CommerceClient(mock.client, CONTRACT_ADDRESS, wallet, {
    paymaster,
  });
  return { client, executor, wallet, mock };
}

function routerWithExecutor(result?: TxResult) {
  const mock = mockPublicClient();
  const executor = new RecordingExecutor(result);
  const wallet = new StubWallet();
  wallet.makeExecutorImpl = () => executor;
  const client = new RouterClient(mock.client, CONTRACT_ADDRESS, wallet);
  return { client, executor, wallet, mock };
}

function policyWithExecutor(result?: TxResult) {
  const mock = mockPublicClient();
  const executor = new RecordingExecutor(result);
  const wallet = new StubWallet();
  wallet.makeExecutorImpl = () => executor;
  const client = new PolicyClient(mock.client, CONTRACT_ADDRESS, wallet);
  return { client, executor, wallet, mock };
}

describe("CommerceClient: write intents", () => {
  it("createJob", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.createJob({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
      hook: HOOK,
    });
    expect(executor.intents).toHaveLength(1);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_CREATE_JOB);
    expect(intent.kwargs).toEqual({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
      hook: HOOK,
    });
    expect(intent.call?.functionName).toBe("createJob");
    expect(intent.call?.args).toEqual([PROVIDER, EVALUATOR, 123n, "d", HOOK]);
  });

  it("createJob defaults hook to ZERO_ADDRESS", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.createJob({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
    });
    expect(executor.intents[0]?.kwargs?.hook).toBe(ZERO_ADDRESS);
    expect(executor.intents[0]?.call?.args?.[4]).toBe(ZERO_ADDRESS);
  });

  it("setProvider", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.setProvider(7n, PROVIDER);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_SET_PROVIDER);
    expect(intent.kwargs).toEqual({
      jobId: 7n,
      provider: PROVIDER,
      optParams: "0x",
    });
    expect(intent.call?.functionName).toBe("setProvider");
    expect(intent.call?.args).toEqual([7n, PROVIDER, "0x"]);
  });

  it("setBudget", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.setBudget(7n, 500n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_SET_BUDGET);
    expect(intent.kwargs).toEqual({ jobId: 7n, amount: 500n, optParams: "0x" });
    expect(intent.call?.functionName).toBe("setBudget");
    expect(intent.call?.args).toEqual([7n, 500n, "0x"]);
  });

  it("fund", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.fund(7n, 500n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_FUND);
    expect(intent.kwargs).toEqual({
      jobId: 7n,
      expectedBudget: 500n,
      optParams: "0x",
    });
    expect(intent.call?.functionName).toBe("fund");
    expect(intent.call?.args).toEqual([7n, 500n, "0x"]);
  });

  it("submit", async () => {
    const { client, executor } = commerceWithExecutor();
    const deliverable = `0x${"11".repeat(32)}` as const;
    const optParams =
      `0x${Buffer.from('{"deliverable_url":"u"}').toString("hex")}` as const;
    await client.submit(7n, deliverable, optParams);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_SUBMIT);
    expect(intent.kwargs).toEqual({ jobId: 7n, deliverable, optParams });
    expect(intent.call?.functionName).toBe("submit");
    expect(intent.call?.args).toEqual([7n, deliverable, optParams]);
  });

  it("submit rejects a deliverable that isn't exactly 32 bytes", async () => {
    const { client } = commerceWithExecutor();
    await expect(client.submit(7n, "0x1234")).rejects.toThrow(
      /deliverable must be exactly 32 bytes/,
    );
  });

  it("complete defaults reason to ZERO_REASON", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.complete(7n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_COMPLETE);
    expect(intent.kwargs).toEqual({
      jobId: 7n,
      reason: ZERO_REASON,
      optParams: "0x",
    });
    expect(intent.call?.functionName).toBe("complete");
    expect(intent.call?.args).toEqual([7n, ZERO_REASON, "0x"]);
  });

  it("complete rejects a reason that isn't exactly 32 bytes", async () => {
    const { client } = commerceWithExecutor();
    await expect(client.complete(7n, "0x1234")).rejects.toThrow(
      /reason must be exactly 32 bytes/,
    );
  });

  it("reject", async () => {
    const { client, executor } = commerceWithExecutor();
    const reason = `0x${"22".repeat(32)}` as const;
    await client.reject(7n, reason);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_REJECT);
    expect(intent.kwargs).toEqual({ jobId: 7n, reason, optParams: "0x" });
    expect(intent.call?.functionName).toBe("reject");
    expect(intent.call?.args).toEqual([7n, reason, "0x"]);
  });

  it("claimRefund", async () => {
    const { client, executor } = commerceWithExecutor();
    await client.claimRefund(7n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_CLAIM_REFUND);
    expect(intent.kwargs).toEqual({ jobId: 7n });
    expect(intent.call?.functionName).toBe("claimRefund");
    expect(intent.call?.args).toEqual([7n]);
  });
});

describe("RouterClient: write intents", () => {
  it("registerJob", async () => {
    const { client, executor } = routerWithExecutor();
    await client.registerJob(7n, POLICY);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_REGISTER_JOB);
    expect(intent.kwargs).toEqual({ jobId: 7n, policy: POLICY });
    expect(intent.call?.functionName).toBe("registerJob");
    expect(intent.call?.args).toEqual([7n, POLICY]);
  });

  it("settle", async () => {
    const { client, executor } = routerWithExecutor();
    await client.settle(7n, "0x01");
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_SETTLE);
    expect(intent.kwargs).toEqual({ jobId: 7n, evidence: "0x01" });
    expect(intent.call?.functionName).toBe("settle");
    expect(intent.call?.args).toEqual([7n, "0x01"]);
  });

  it("settle defaults evidence to 0x", async () => {
    const { client, executor } = routerWithExecutor();
    await client.settle(7n);
    expect(executor.intents[0]?.kwargs?.evidence).toBe("0x");
  });

  it("markExpired", async () => {
    const { client, executor } = routerWithExecutor();
    await client.markExpired(7n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_MARK_EXPIRED);
    expect(intent.kwargs).toEqual({ jobId: 7n });
    expect(intent.call?.functionName).toBe("markExpired");
    expect(intent.call?.args).toEqual([7n]);
  });
});

describe("PolicyClient: write intents", () => {
  it("dispute", async () => {
    const { client, executor } = policyWithExecutor();
    await client.dispute(7n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_DISPUTE);
    expect(intent.kwargs).toEqual({ jobId: 7n });
    expect(intent.call?.functionName).toBe("dispute");
    expect(intent.call?.args).toEqual([7n]);
  });

  it("voteReject", async () => {
    const { client, executor } = policyWithExecutor();
    await client.voteReject(7n);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_VOTE_REJECT);
    expect(intent.kwargs).toEqual({ jobId: 7n });
    expect(intent.call?.functionName).toBe("voteReject");
    expect(intent.call?.args).toEqual([7n]);
  });
});

describe("createJob: jobId dual-sourcing", () => {
  it("uses the executor-supplied jobId and skips receipt parsing", async () => {
    const { client } = commerceWithExecutor({
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt: null,
      jobId: 5n,
    });
    const result = await client.createJob({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
    });
    expect(result.jobId).toBe(5n);
  });

  it("parses jobId from the JobCreated receipt event when the executor omits it", async () => {
    const jobId = 7n;
    const topics = encodeEventTopics({
      abi: agenticCommerceAbi,
      eventName: "JobCreated",
      args: { jobId, client: WALLET_ADDRESS, provider: PROVIDER },
    });
    const data = encodeAbiParameters(
      [
        { type: "address", name: "evaluator" },
        { type: "uint256", name: "expiredAt" },
        { type: "address", name: "hook" },
      ],
      [EVALUATOR, 123n, ZERO_ADDRESS],
    );
    const receipt = {
      status: "success",
      blockNumber: 1n,
      blockHash: `0x${"aa".repeat(32)}`,
      transactionHash: FAKE_TX_HASH,
      transactionIndex: 0,
      from: WALLET_ADDRESS,
      to: CONTRACT_ADDRESS,
      cumulativeGasUsed: 1n,
      gasUsed: 1n,
      contractAddress: null,
      logs: [
        {
          address: CONTRACT_ADDRESS,
          topics,
          data,
          blockNumber: 1n,
          blockHash: `0x${"aa".repeat(32)}`,
          transactionHash: FAKE_TX_HASH,
          transactionIndex: 0,
          logIndex: 0,
          removed: false,
        },
      ],
      logsBloom: `0x${"0".repeat(512)}`,
      effectiveGasPrice: 1n,
      // biome-ignore lint/suspicious/noExplicitAny: minimal test receipt shape
    } as any;

    const { client } = commerceWithExecutor({
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt,
    });
    const result = await client.createJob({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
    });
    expect(result.jobId).toBe(7n);
  });

  it("leaves jobId null when there is neither an executor result nor a decodable receipt log", async () => {
    const { client } = commerceWithExecutor({
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt: null,
    });
    const result = await client.createJob({
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 123n,
      description: "d",
    });
    expect(result.jobId).toBeNull();
  });
});

describe("PolicyClient: admin ops bypass the intent seam", () => {
  /** A wallet whose makeExecutor always throws — proves the admin ops never
   * reach it (they must go through sendTx instead). */
  function policyWithPoisonedExecutor() {
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    wallet.makeExecutorImpl = () => {
      throw new Error("executeIntent must not be used for admin ops");
    };
    const client = new PolicyClient(mock.client, CONTRACT_ADDRESS, wallet);
    return { client, mock, wallet };
  }

  it("addVoter uses sendTx, not executeIntent", async () => {
    const { client, mock } = policyWithPoisonedExecutor();
    const result = await client.addVoter(VOTER);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("removeVoter uses sendTx, not executeIntent", async () => {
    const { client, mock } = policyWithPoisonedExecutor();
    const result = await client.removeVoter(VOTER);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("setQuorum uses sendTx, not executeIntent", async () => {
    const { client, mock } = policyWithPoisonedExecutor();
    const result = await client.setQuorum(3);
    expect(result.status).toBe(1);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });
});

describe("paymaster wiring into ExecutionContext", () => {
  it("threads the client's paymaster into makeExecutor's context", async () => {
    const sentinel = {} as Paymaster;
    const { client, wallet } = commerceWithExecutor(undefined, sentinel);
    await client.setBudget(1n, 10n);
    expect(wallet.contexts).toHaveLength(1);
    expect(wallet.contexts[0]?.paymaster).toBe(sentinel);
  });

  it("threads null when no paymaster is configured", async () => {
    const { client, wallet } = commerceWithExecutor();
    await client.setBudget(1n, 10n);
    expect(wallet.contexts[0]?.paymaster).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// CommerceClient: views, batch reads, and event helpers. There is no
// dedicated Python test module for these (the equivalent coverage lives in
// test_erc8183_job_ops.py against the ERC8183Client facade, out of scope for
// this task); grouped here alongside the rest of CommerceClient's coverage.
// ---------------------------------------------------------------------------

const RAW_JOB = {
  id: 1n,
  client: WALLET_ADDRESS,
  provider: PROVIDER,
  evaluator: EVALUATOR,
  description: "d",
  budget: 100n,
  expiredAt: 999n,
  status: 1,
  hook: ZERO_ADDRESS,
  submittedAt: 5n,
  deliverable: `0x${"aa".repeat(32)}` as const,
} as const;

function readCommerce(
  results: Record<string, unknown>,
): (params: readonly unknown[]) => unknown {
  return (params) => {
    const [{ data }] = params as [{ data: `0x${string}` }];
    const decoded = decodeFunctionData({ abi: agenticCommerceAbi, data });
    const result = results[decoded.functionName];
    return encodeFunctionResult({
      abi: agenticCommerceAbi,
      functionName: decoded.functionName,
      // biome-ignore lint/suspicious/noExplicitAny: result shape varies per function
      result: result as any,
    });
  };
}

describe("CommerceClient: views", () => {
  it("getJob decodes the tuple (checksummed addresses, enum status)", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ getJob: RAW_JOB }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    const job = await client.getJob(1n);
    expect(job).toEqual({
      id: 1n,
      client: WALLET_ADDRESS,
      provider: PROVIDER,
      evaluator: EVALUATOR,
      description: "d",
      budget: 100n,
      expiredAt: 999n,
      status: 1,
      hook: ZERO_ADDRESS,
      submittedAt: 5n,
      deliverable: RAW_JOB.deliverable,
    });
  });

  it("jobCounter()", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ jobCounter: 42n }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.jobCounter()).resolves.toBe(42n);
  });

  it("paymentToken()", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ paymentToken: PROVIDER }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.paymentToken()).resolves.toBe(PROVIDER);
  });

  it("platformFeeBp()", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ platformFeeBP: 250n }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.platformFeeBp()).resolves.toBe(250n);
  });

  it("platformTreasury()", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ platformTreasury: EVALUATOR }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.platformTreasury()).resolves.toBe(EVALUATOR);
  });

  it("jobHasBudget()", async () => {
    const mock = mockPublicClient({
      eth_call: readCommerce({ jobHasBudget: true }),
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    await expect(client.jobHasBudget(1n)).resolves.toBe(true);
  });
});

function stubMulticallClient(
  impl: (params: { contracts: unknown[] }) => unknown,
): PublicClient {
  return { multicall: vi.fn(impl) } as unknown as PublicClient;
}

describe("CommerceClient: getJobsBatch", () => {
  it("decodes successes and nulls out failed/missing entries, preserving order", async () => {
    const client2 = stubMulticallClient(({ contracts }) =>
      contracts.map((_, i) =>
        i === 1
          ? { status: "failure", error: new Error("reverted") }
          : { status: "success", result: RAW_JOB },
      ),
    );
    const commerce = new CommerceClient(client2, CONTRACT_ADDRESS);
    const jobs = await commerce.getJobsBatch([1n, 2n, 3n]);
    expect(jobs).toHaveLength(3);
    expect(jobs[0]).toMatchObject({ id: 1n, client: WALLET_ADDRESS });
    expect(jobs[1]).toBeNull();
    expect(jobs[2]).toMatchObject({ id: 1n, client: WALLET_ADDRESS });
  });

  it("returns [] for an empty jobIds array without calling multicall", async () => {
    const multicall = vi.fn();
    const client2 = { multicall } as unknown as PublicClient;
    const commerce = new CommerceClient(client2, CONTRACT_ADDRESS);
    await expect(commerce.getJobsBatch([])).resolves.toEqual([]);
    expect(multicall).not.toHaveBeenCalled();
  });
});

describe("CommerceClient: event helpers", () => {
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

  it("getJobFundedEvents flattens jobId/client/provider/amount", async () => {
    const topics = encodeEventTopics({
      abi: agenticCommerceAbi,
      eventName: "JobFunded",
      args: { jobId: 1n, client: WALLET_ADDRESS, provider: PROVIDER },
    });
    const data = encodeAbiParameters([{ type: "uint256" }], [500n]);
    const mock = mockPublicClient({
      eth_getLogs: () => [rawLog(topics, data)],
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    const events = await client.getJobFundedEvents(0n, "latest", PROVIDER);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      jobId: 1n,
      client: WALLET_ADDRESS,
      provider: PROVIDER,
      amount: 500n,
    });
  });

  it("getJobCreatedEvents flattens jobId/client/provider/evaluator/expiredAt", async () => {
    const topics = encodeEventTopics({
      abi: agenticCommerceAbi,
      eventName: "JobCreated",
      args: { jobId: 1n, client: WALLET_ADDRESS, provider: PROVIDER },
    });
    const data = encodeAbiParameters(
      [
        { type: "address", name: "evaluator" },
        { type: "uint256", name: "expiredAt" },
        { type: "address", name: "hook" },
      ],
      [EVALUATOR, 999n, ZERO_ADDRESS],
    );
    const mock = mockPublicClient({
      eth_getLogs: () => [rawLog(topics, data)],
    });
    const client = new CommerceClient(mock.client, CONTRACT_ADDRESS);
    const events = await client.getJobCreatedEvents(0n);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      jobId: 1n,
      client: WALLET_ADDRESS,
      provider: PROVIDER,
      evaluator: EVALUATOR,
      expiredAt: 999n,
    });
  });
});
