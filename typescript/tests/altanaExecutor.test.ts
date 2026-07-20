/**
 * `AltanaIntentExecutor` behavior (`src/wallets/altana/provider.ts`):
 * mechanical-call encoding, the erc8183.fund approve bundle, relay result
 * interpretation (receipt fetch / revert / FAILED / missing hash /
 * timeout), the nonce-race retry, admin-vs-session dispatch and the
 * provider-level serialization queue.
 *
 * The Altana relay is mocked at the `@altananetwork/sdk` module boundary;
 * receipt polling and the paymentToken read run against the shared
 * `mockPublicClient` JSON-RPC transport, exactly like the LocalExecutor
 * suites.
 */

import {
  type Hex,
  decodeFunctionData,
  encodeFunctionData,
  encodeFunctionResult,
  getAddress,
  parseAbi,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { agenticCommerceAbi } from "../src/abis/agenticCommerce.js";
import { erc20Abi } from "../src/abis/erc20.js";
import {
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../src/errors.js";
import {
  ERC8183_CREATE_JOB,
  ERC8183_FUND,
  type Intent,
} from "../src/wallets/intents.js";
import {
  FAKE_TX_HASH,
  type MockPublicClient,
  mockPublicClient,
} from "./helpers/mockTransport.js";

const sdkMocks = vi.hoisted(() => {
  const executeMock = vi.fn();
  const createWalletMock = vi.fn();
  const signerFromPrivateKeyMock = vi.fn();
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: vi.fn(),
    revokeSession: vi.fn(),
  }));
  return {
    executeMock,
    createWalletMock,
    signerFromPrivateKeyMock,
    createClientMock,
  };
});

vi.mock("@altananetwork/sdk", async () => {
  const { privateKeyToAccount: toAccount } = await import("viem/accounts");
  sdkMocks.signerFromPrivateKeyMock.mockImplementation(
    (privateKey: `0x${string}`) => {
      const account = toAccount(privateKey);
      return {
        type: "privateKey",
        address: account.address,
        publicKey: account.publicKey,
        signDigest: async () => `0x${"11".repeat(65)}`,
        _privateKey: privateKey,
      };
    },
  );
  return {
    createClient: sdkMocks.createClientMock,
    signerFromPrivateKey: sdkMocks.signerFromPrivateKeyMock,
    createPrivateKeySigner: () =>
      sdkMocks.signerFromPrivateKeyMock(`0x${"77".repeat(32)}`),
    BNB: { chainId: 56 },
  };
});

const { AltanaWalletProvider } = await import(
  "../src/wallets/altana/provider.js"
);
type AltanaSessionT = import("../src/wallets/altana/types.js").AltanaSession;
type AltanaSignerT = import("../src/wallets/altana/types.js").AltanaSigner;
type RelayCall = { to: `0x${string}`; value?: bigint; data?: Hex };

const ADMIN_PK: `0x${string}` = `0x${"a1".repeat(32)}`;
const SESSION_PK: `0x${string}` = `0x${"b2".repeat(32)}`;
const WALLET = getAddress(`0x${"11".repeat(20)}`);
const COMMERCE = getAddress(`0x${"aa".repeat(20)}`);
const FAKE_TOKEN = getAddress(`0x${"dd".repeat(20)}`);
const CALLS_ID: `0x${string}` = `0x${"ca".repeat(32)}`;
const SET_VALUE_ABI = parseAbi(["function setValue(uint256 x) returns (bool)"]);
const TARGET = getAddress(`0x${"22".repeat(20)}`);

const PAYMENT_TOKEN_SELECTOR = encodeFunctionData({
  abi: agenticCommerceAbi,
  functionName: "paymentToken",
  args: [],
}).slice(0, 10);

function relayOk() {
  return {
    callsId: CALLS_ID,
    status: "CONFIRMED" as const,
    transactionHash: FAKE_TX_HASH,
  };
}

function fakeSession(): AltanaSessionT {
  const account = privateKeyToAccount(SESSION_PK);
  const signer = {
    type: "privateKey",
    address: account.address,
    publicKey: account.publicKey,
    signDigest: async () => `0x${"22".repeat(65)}` as const,
    _privateKey: SESSION_PK,
  };
  return {
    walletAddress: WALLET,
    signer: signer as unknown as AltanaSignerT,
    publicKey: account.publicKey,
    permissions: {},
    expiry: 1_767_225_600,
  };
}

function setValueIntent(overrides: Partial<Intent> = {}): Intent {
  return {
    name: "test.set_value",
    call: {
      address: TARGET,
      abi: SET_VALUE_ABI,
      functionName: "setValue",
      args: [42n],
    },
    ...overrides,
  };
}

function fundIntent(overrides: Partial<Intent> = {}): Intent {
  return {
    name: ERC8183_FUND,
    kwargs: { jobId: 1n, expectedBudget: 250n, optParams: "0x" },
    call: {
      address: COMMERCE,
      abi: agenticCommerceAbi,
      functionName: "fund",
      args: [1n, 250n, "0x"],
    },
    description: "fund job",
    ...overrides,
  };
}

/** eth_call handler answering only commerce.paymentToken(); "0x" otherwise. */
function paymentTokenHandler() {
  return (params: readonly unknown[]) => {
    const [{ data }] = params as [{ data: Hex }];
    if (data.toLowerCase().startsWith(PAYMENT_TOKEN_SELECTOR)) {
      return encodeFunctionResult({
        abi: agenticCommerceAbi,
        functionName: "paymentToken",
        result: FAKE_TOKEN,
      });
    }
    return "0x";
  };
}

function makeAdminProvider() {
  return new AltanaWalletProvider({
    privateKey: ADMIN_PK,
    nonceRetry: { delayMs: 0 },
  });
}

function makeExecutor(
  provider = makeAdminProvider(),
  mock: MockPublicClient = mockPublicClient({
    eth_call: paymentTokenHandler(),
  }),
  receiptTimeout: number | null = 5,
) {
  const executor = provider.makeExecutor({
    client: mock.client,
    receiptTimeout,
  });
  return { provider, executor, mock };
}

/** The relay batch handed to the (mocked) SDK execute on call `n`. */
function relayBatch(n = 0): RelayCall[] {
  const opts = sdkMocks.executeMock.mock.calls[n]?.[0] as
    | { calls: RelayCall[] }
    | undefined;
  if (!opts) throw new Error(`relay execute call ${n} not recorded`);
  return opts.calls;
}

beforeEach(() => {
  sdkMocks.executeMock.mockClear();
  sdkMocks.createWalletMock.mockClear();
  sdkMocks.signerFromPrivateKeyMock.mockClear();
  sdkMocks.executeMock.mockImplementation(async () => relayOk());
  sdkMocks.createWalletMock.mockImplementation(
    async ({ signer }: { signer: { address: `0x${string}` } }) => ({
      address: signer.address,
      signer,
    }),
  );
});

describe("AltanaIntentExecutor — mechanical call encoding", () => {
  it("submits exactly one relay call with the intent's precise calldata", async () => {
    const { executor } = makeExecutor();
    await executor.execute(setValueIntent());

    const batch = relayBatch();
    expect(batch).toHaveLength(1);
    expect(batch[0]).toEqual({
      to: TARGET,
      value: 0n,
      data: encodeFunctionData({
        abi: SET_VALUE_ABI,
        functionName: "setValue",
        args: [42n],
      }),
    });
  });

  it("forwards intent.value verbatim", async () => {
    const { executor } = makeExecutor();
    await executor.execute(setValueIntent({ value: 123n }));
    expect(relayBatch()[0]?.value).toBe(123n);
  });

  it("throws the aligned missing-call error when the intent has no mechanical form", async () => {
    const { executor } = makeExecutor();
    await expect(executor.execute({ name: "erc8183.settle" })).rejects.toThrow(
      /AltanaIntentExecutor requires Intent\.call .* got None for intent 'erc8183\.settle'/,
    );
    expect(sdkMocks.executeMock).not.toHaveBeenCalled();
  });
});

describe("AltanaIntentExecutor — relay result interpretation", () => {
  it("CONFIRMED: fetches the real receipt and returns status 1 + receipt + callsId", async () => {
    const { executor } = makeExecutor();
    const result = await executor.execute(setValueIntent());

    expect(result.status).toBe(1);
    expect(result.transactionHash).toBe(FAKE_TX_HASH);
    expect(result.callsId).toBe(CALLS_ID);
    expect(result.receipt?.transactionHash).toBe(FAKE_TX_HASH);
    expect(result.receipt?.status).toBe("success");
  });

  it("throws when the fetched receipt is reverted (relay CONFIRMED is not on-chain success)", async () => {
    const mock = mockPublicClient({ eth_call: paymentTokenHandler() });
    const receipt = mock.handlers.eth_getTransactionReceipt?.([]) as Record<
      string,
      unknown
    >;
    mock.handlers.eth_getTransactionReceipt = () => ({
      ...receipt,
      status: "0x0",
    });
    const { executor } = makeExecutor(makeAdminProvider(), mock);
    await expect(executor.execute(setValueIntent())).rejects.toThrow(
      /Transaction reverted on-chain/,
    );
  });

  it("throws on relay FAILED, naming the callsId", async () => {
    sdkMocks.executeMock.mockResolvedValueOnce({
      callsId: CALLS_ID,
      status: "FAILED",
    });
    const { executor } = makeExecutor();
    await expect(executor.execute(setValueIntent())).rejects.toThrow(
      new RegExp(`FAILED for test\\.set_value.*${CALLS_ID}`),
    );
  });

  it("throws when the relay returns no transactionHash, naming status and callsId", async () => {
    sdkMocks.executeMock.mockResolvedValueOnce({
      callsId: CALLS_ID,
      status: "PENDING",
    });
    const { executor } = makeExecutor();
    await expect(executor.execute(setValueIntent())).rejects.toThrow(
      new RegExp(`status PENDING without a transactionHash.*${CALLS_ID}`),
    );
  });

  it("raises TransactionPendingError when the receipt never lands within receiptTimeout", async () => {
    const mock = mockPublicClient({ eth_call: paymentTokenHandler() });
    mock.handlers.eth_getTransactionReceipt = () => {
      throw new Error("not found");
    };
    mock.handlers.eth_getTransactionByHash = () => ({
      blockHash: null,
      blockNumber: null,
      from: WALLET,
      gas: "0x186a0",
      gasPrice: "0x0",
      hash: FAKE_TX_HASH,
      input: "0x",
      nonce: "0x7",
      r: `0x${"00".repeat(32)}`,
      s: `0x${"00".repeat(32)}`,
      to: TARGET,
      transactionIndex: null,
      type: "0x0",
      v: "0x1b",
      value: "0x0",
    });
    const { executor } = makeExecutor(makeAdminProvider(), mock, 0.2);
    await expect(executor.execute(setValueIntent())).rejects.toBeInstanceOf(
      TransactionPendingError,
    );
  });

  it("reports a relay hash the chain never observed as unverified", async () => {
    const mock = mockPublicClient({ eth_call: paymentTokenHandler() });
    mock.handlers.eth_getTransactionReceipt = () => {
      throw new Error("not found");
    };
    const { executor } = makeExecutor(makeAdminProvider(), mock, 0.01);
    await expect(executor.execute(setValueIntent())).rejects.toBeInstanceOf(
      RelaySubmissionUnverifiedError,
    );
  });
});

describe("AltanaIntentExecutor — nonce races and dispatch", () => {
  it("retries on InvalidNonce and succeeds (relay view catches up)", async () => {
    sdkMocks.executeMock.mockRejectedValueOnce(
      new Error("relay error: InvalidNonce"),
    );
    const { executor } = makeExecutor();
    const result = await executor.execute(setValueIntent());
    expect(result.status).toBe(1);
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(2);
  });

  it("does not retry non-nonce relay errors", async () => {
    sdkMocks.executeMock.mockRejectedValueOnce(
      new Error("insufficient funds for fee"),
    );
    const { executor } = makeExecutor();
    await expect(executor.execute(setValueIntent())).rejects.toThrow(
      /insufficient funds/,
    );
    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(1);
  });

  it("admin mode: createWallet is called exactly once across executes", async () => {
    const { executor } = makeExecutor();
    await executor.execute(setValueIntent());
    await executor.execute(setValueIntent({ value: 1n }));
    expect(sdkMocks.createWalletMock).toHaveBeenCalledTimes(1);
    const opts = sdkMocks.executeMock.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    expect(opts.wallet).toEqual({
      address: privateKeyToAccount(ADMIN_PK).address,
    });
    expect(opts).not.toHaveProperty("session");
  });

  it("session mode: executes with the session and never touches admin key material", async () => {
    const session = fakeSession();
    const provider = new AltanaWalletProvider({
      session,
      nonceRetry: { delayMs: 0 },
    });
    const { executor } = makeExecutor(provider);
    await executor.execute(setValueIntent());

    expect(sdkMocks.createWalletMock).not.toHaveBeenCalled();
    expect(sdkMocks.signerFromPrivateKeyMock).not.toHaveBeenCalled();
    const opts = sdkMocks.executeMock.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    expect(opts.session).toBe(session);
    expect(opts).not.toHaveProperty("wallet");
    expect(opts).not.toHaveProperty("signer");
  });

  it("serializes concurrent executes through the provider queue (even across executors)", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    sdkMocks.executeMock.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 10));
      inFlight -= 1;
      return relayOk();
    });

    const provider = makeAdminProvider();
    const first = makeExecutor(provider);
    const second = makeExecutor(provider);
    await Promise.all([
      first.executor.execute(setValueIntent()),
      second.executor.execute(setValueIntent({ value: 1n })),
      first.executor.execute(setValueIntent({ value: 2n })),
    ]);

    expect(sdkMocks.executeMock).toHaveBeenCalledTimes(3);
    expect(maxInFlight).toBe(1);
  });
});

describe("AltanaIntentExecutor — erc8183.fund approve bundling", () => {
  it("prepends approve(commerce, expectedBudget) in the SAME relay batch", async () => {
    const { executor } = makeExecutor();
    await executor.execute(fundIntent());

    const batch = relayBatch();
    expect(batch).toHaveLength(2);
    expect(batch[0]?.to).toBe(FAKE_TOKEN);
    expect(batch[0]?.value).toBe(0n);
    const approve = decodeFunctionData({
      abi: erc20Abi,
      data: batch[0]?.data as Hex,
    });
    expect(approve.functionName).toBe("approve");
    expect(approve.args).toEqual([COMMERCE, 250n]);
    expect(batch[1]).toEqual({
      to: COMMERCE,
      value: 0n,
      data: encodeFunctionData({
        abi: agenticCommerceAbi,
        functionName: "fund",
        args: [1n, 250n, "0x"],
      }),
    });
  });

  it("falls back to call.args[1] for the amount when kwargs lack expectedBudget", async () => {
    const { executor } = makeExecutor();
    await executor.execute(fundIntent({ kwargs: { jobId: 1n } }));
    const approve = decodeFunctionData({
      abi: erc20Abi,
      data: relayBatch()[0]?.data as Hex,
    });
    expect(approve.args).toEqual([COMMERCE, 250n]);
  });

  it("caches the paymentToken read: two funds, one eth_call", async () => {
    const { executor, mock } = makeExecutor();
    await executor.execute(fundIntent());
    await executor.execute(fundIntent());

    const tokenReads = mock.calls.filter(
      (call) =>
        call.method === "eth_call" &&
        String(
          (call.params[0] as { data?: string } | undefined)?.data ?? "",
        ).startsWith(PAYMENT_TOKEN_SELECTOR),
    );
    expect(tokenReads).toHaveLength(1);
    expect(relayBatch(0)).toHaveLength(2);
    expect(relayBatch(1)).toHaveLength(2);
  });

  it("non-fund erc8183 intents stay a single relay call", async () => {
    const { executor } = makeExecutor();
    await executor.execute(setValueIntent({ name: ERC8183_CREATE_JOB }));
    expect(relayBatch()).toHaveLength(1);
  });
});
