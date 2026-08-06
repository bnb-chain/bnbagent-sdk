/**
 * End-to-end wiring proof: the existing ERC-8183 / ERC-8004 clients work
 * UNCHANGED over an `AltanaWalletProvider` — the whole point of the
 * `executeIntent` seam.
 *
 * Three protocol-level properties that unit tests on the executor alone
 * cannot pin:
 *
 * 1. `ERC8183Client.fund` on a fundBundlesApproval wallet performs ZERO
 *    local signing/broadcast (`eth_sendRawTransaction` never appears) and
 *    ZERO SDK-side allowance management — one relay submission carrying
 *    approve + fund.
 * 2. `createJob` recovers the jobId from the receipt the executor fetched:
 *    the JobCreated log is decoded by emitting-contract address, so a
 *    same-topic0 decoy from another contract in the batch is ignored (the
 *    7702-batch inner-log concern, field-verified as fact 10).
 * 3. `registerAgent` (ERC-8004) recovers agentId through the same path.
 *
 * Recipe: `../src/core/clients.js` is mocked so `ERC8183Client.create`
 * gets the programmable mock transport (per `erc8183Client.test.ts`), and
 * `@altananetwork/sdk` is mocked at the module boundary (per
 * `altanaExecutor.test.ts`).
 */

import {
  type Hex,
  decodeFunctionData,
  encodeAbiParameters,
  encodeEventTopics,
  encodeFunctionData,
  encodeFunctionResult,
  getAddress,
} from "viem";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { agenticCommerceAbi } from "../src/abis/agenticCommerce.js";
import { erc20Abi } from "../src/abis/erc20.js";
import { identityRegistryAbi } from "../src/abis/identityRegistry.js";
import type { NetworkConfig } from "../src/config.js";
import { ContractInterface } from "../src/erc8004/contract.js";
import { ZERO_ADDRESS } from "../src/erc8183/types.js";
import {
  FAKE_TX_HASH,
  type MockPublicClient,
  mockPublicClient,
} from "./helpers/mockTransport.js";

const sdkMocks = vi.hoisted(() => {
  const executeMock = vi.fn();
  const createWalletMock = vi.fn();
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: vi.fn(),
    revokeSession: vi.fn(),
  }));
  return { executeMock, createWalletMock, createClientMock };
});

vi.mock("@altananetwork/sdk", async () => {
  const { privateKeyToAccount: toAccount } = await import("viem/accounts");
  return {
    createClient: sdkMocks.createClientMock,
    signerFromPrivateKey: (privateKey: `0x${string}`) => {
      const account = toAccount(privateKey);
      return {
        type: "privateKey",
        address: account.address,
        publicKey: account.publicKey,
        signDigest: async () => `0x${"11".repeat(65)}`,
        _privateKey: privateKey,
      };
    },
    createPrivateKeySigner: () => {
      throw new Error("not used in wiring tests");
    },
    BNB: { chainId: 56 },
  };
});

const { createPublicClientForMock } = vi.hoisted(() => ({
  createPublicClientForMock: vi.fn(),
}));

vi.mock("../src/core/clients.js", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../src/core/clients.js")>();
  return { ...actual, createPublicClientFor: createPublicClientForMock };
});

const { ERC8183Client } = await import("../src/erc8183/client.js");
const { AltanaWalletProvider } = await import(
  "../src/wallets/altana/provider.js"
);

const ADMIN_PK: `0x${string}` = `0x${"a1".repeat(32)}`;
const FAKE_COMMERCE = getAddress(`0x${"aa".repeat(20)}`);
const FAKE_ROUTER = getAddress(`0x${"bb".repeat(20)}`);
const FAKE_POLICY = getAddress(`0x${"cc".repeat(20)}`);
const FAKE_TOKEN = getAddress(`0x${"dd".repeat(20)}`);
const FAKE_REGISTRY = getAddress(`0x${"ee".repeat(20)}`);
const DECOY_CONTRACT = getAddress(`0x${"99".repeat(20)}`);
const CALLS_ID: `0x${string}` = `0x${"ca".repeat(32)}`;

const PAYMENT_TOKEN_SELECTOR = encodeFunctionData({
  abi: agenticCommerceAbi,
  functionName: "paymentToken",
  args: [],
}).slice(0, 10);

function fakeNetwork(): NetworkConfig {
  return {
    name: "altana-wiring-net",
    chainId: 97,
    rpcUrl: "https://fake-rpc.example.com",
    usePaymaster: false,
    registryContract: FAKE_REGISTRY,
    commerceContract: FAKE_COMMERCE,
    routerContract: FAKE_ROUTER,
    policyContract: FAKE_POLICY,
  };
}

/** Wrap encoded topics+data as the raw RPC log shape receipts carry.
 * (`encodeEventTopics`' return type admits null/array filter slots, but a
 * fully-specified indexed-args call only ever yields concrete topics.) */
function rpcLog(
  address: `0x${string}`,
  encoded: {
    data: Hex;
    topics: readonly (Hex | readonly Hex[] | null)[];
  },
  logIndex: number,
) {
  return {
    address,
    topics: encoded.topics,
    data: encoded.data,
    blockNumber: "0x1",
    blockHash: `0x${"aa".repeat(32)}`,
    transactionHash: FAKE_TX_HASH,
    transactionIndex: "0x0",
    logIndex: `0x${logIndex.toString(16)}`,
    removed: false,
  };
}

function makeMock(receiptLogs: unknown[] = []): MockPublicClient {
  const mock = mockPublicClient({
    eth_chainId: () => "0x61", // 97, must match fakeNetwork
    eth_call: (params) => {
      const [{ data }] = params as [{ data: Hex }];
      if (data.toLowerCase().startsWith(PAYMENT_TOKEN_SELECTOR)) {
        return encodeFunctionResult({
          abi: agenticCommerceAbi,
          functionName: "paymentToken",
          result: FAKE_TOKEN,
        });
      }
      return "0x";
    },
  });
  const baseReceipt = mock.handlers.eth_getTransactionReceipt?.([]) as Record<
    string,
    unknown
  >;
  mock.handlers.eth_getTransactionReceipt = () => ({
    ...baseReceipt,
    logs: receiptLogs,
  });
  return mock;
}

function makeProvider() {
  return new AltanaWalletProvider({
    privateKey: ADMIN_PK,
    nonceRetry: { delayMs: 0 },
  });
}

beforeEach(() => {
  sdkMocks.executeMock.mockClear();
  sdkMocks.createWalletMock.mockClear();
  createPublicClientForMock.mockReset();
  sdkMocks.executeMock.mockResolvedValue({
    callsId: CALLS_ID,
    status: "CONFIRMED",
    transactionHash: FAKE_TX_HASH,
  });
  sdkMocks.createWalletMock.mockImplementation(
    async ({ signer }: { signer: { address: `0x${string}` } }) => ({
      address: signer.address,
      signer,
    }),
  );
});

describe("ERC8183Client over AltanaWalletProvider", () => {
  it("fund(): no eth_sendRawTransaction, no allowance management — one relay batch of approve+fund", async () => {
    const mock = makeMock();
    createPublicClientForMock.mockReturnValue(mock.client);
    const client = await ERC8183Client.create({
      walletProvider: makeProvider(),
      network: fakeNetwork(),
    });

    const result = await client.fund(7n, 250n);
    expect(result.status).toBe(1);

    // Self-broadcasting all the way down: nothing was locally signed or
    // broadcast, and the SDK never read allowance/decimals (that whole
    // branch is skipped by fundBundlesApproval === true).
    expect(
      mock.calls.filter((c) => c.method === "eth_sendRawTransaction"),
    ).toHaveLength(0);
    const ethCalls = mock.calls.filter((c) => c.method === "eth_call");
    expect(ethCalls).toHaveLength(1); // exactly the paymentToken read

    // One relay submission carrying the atomic approve+fund batch.
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
    const { calls } = sdkMocks.executeMock.mock.calls[0]?.[0] as {
      calls: { to: `0x${string}`; data: Hex }[];
    };
    expect(calls).toHaveLength(2);
    const approve = decodeFunctionData({
      abi: erc20Abi,
      data: calls[0]?.data as Hex,
    });
    expect(calls[0]?.to).toBe(FAKE_TOKEN);
    expect(approve.functionName).toBe("approve");
    expect(approve.args).toEqual([FAKE_COMMERCE, 250n]);
    const fund = decodeFunctionData({
      abi: agenticCommerceAbi,
      data: calls[1]?.data as Hex,
    });
    expect(calls[1]?.to).toBe(FAKE_COMMERCE);
    expect(fund.functionName).toBe("fund");
    expect(fund.args).toEqual([7n, 250n, "0x"]);
  });

  it("createJob(): recovers jobId from the fetched receipt by emitting-contract address", async () => {
    // JobCreated(jobId idx, client idx, provider idx, evaluator, expiredAt, hook)
    const jobCreatedLog = (jobId: bigint) => ({
      topics: encodeEventTopics({
        abi: agenticCommerceAbi,
        eventName: "JobCreated",
        args: {
          jobId,
          client: getAddress(`0x${"11".repeat(20)}`),
          provider: ZERO_ADDRESS,
        },
      }),
      data: encodeAbiParameters(
        [{ type: "address" }, { type: "uint256" }, { type: "address" }],
        [FAKE_ROUTER, 9_999_999_999n, FAKE_ROUTER],
      ),
    });
    const jobCreated = jobCreatedLog(42n);
    const decoy = jobCreatedLog(99n);
    // The decoy shares topic0 but is emitted by ANOTHER contract in the
    // same (7702-style batched) receipt — it must be ignored.
    const mock = makeMock([
      rpcLog(DECOY_CONTRACT, decoy, 0),
      rpcLog(FAKE_COMMERCE, jobCreated, 1),
    ]);
    createPublicClientForMock.mockReturnValue(mock.client);
    const client = await ERC8183Client.create({
      walletProvider: makeProvider(),
      network: fakeNetwork(),
    });

    const result = await client.createJob({
      expiredAt: 9_999_999_999n,
      skipExpiryCheck: true,
    });
    expect(result.jobId).toBe(42n);
    expect(result.status).toBe(1);
    expect(result.callsId).toBe(CALLS_ID);
    expect(
      mock.calls.filter((c) => c.method === "eth_sendRawTransaction"),
    ).toHaveLength(0);
  });
});

describe("ERC-8004 ContractInterface over AltanaWalletProvider", () => {
  it("registerAgent(): recovers agentId from the fetched receipt through the same path", async () => {
    // Registered(agentId idx, agentURI, owner idx)
    const registered = {
      topics: encodeEventTopics({
        abi: identityRegistryAbi,
        eventName: "Registered",
        args: { agentId: 7n, owner: getAddress(`0x${"11".repeat(20)}`) },
      }),
      data: encodeAbiParameters(
        [{ type: "string" }],
        ["https://agent.example/card.json"],
      ),
    };
    const mock = makeMock([rpcLog(FAKE_REGISTRY, registered, 0)]);
    const contract = new ContractInterface({
      client: mock.client,
      contractAddress: FAKE_REGISTRY,
      walletProvider: makeProvider(),
    });

    const result = await contract.registerAgent(
      "https://agent.example/card.json",
    );
    expect(result.success).toBe(true);
    expect(result.agentId).toBe(7);
    expect(result.transactionHash).toBe(FAKE_TX_HASH);
    expect(
      mock.calls.filter((c) => c.method === "eth_sendRawTransaction"),
    ).toHaveLength(0);
    // The register call itself went through the relay as one mechanical call.
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
    const { calls } = sdkMocks.executeMock.mock.calls[0]?.[0] as {
      calls: { to: `0x${string}`; data: Hex }[];
    };
    expect(calls).toHaveLength(1);
    const register = decodeFunctionData({
      abi: identityRegistryAbi,
      data: calls[0]?.data as Hex,
    });
    expect(calls[0]?.to).toBe(FAKE_REGISTRY);
    expect(register.functionName).toBe("register");
  });
});
