import {
  type TransactionRequestLegacy,
  getAddress,
  keccak256,
  parseAbi,
} from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";
import type { Paymaster } from "../src/core/paymaster.js";
import {
  _resetTxConfigOverrides,
  setDefaultReceiptTimeout,
} from "../src/core/txConfig.js";
import { TransactionPendingError } from "../src/errors.js";
import { RelaySubmissionUnverifiedError } from "../src/index.js";
import type { ContractCall, Intent } from "../src/wallets/intents.js";
import { LocalExecutor } from "../src/wallets/localExecutor.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import {
  FAKE_TX_HASH,
  type MockHandlers,
  mockPublicClient,
} from "./helpers/mockTransport.js";

/**
 * Ports python/tests/test_local_executor_paymaster.py: the sponsor-if-
 * sponsorable, else self-pay contract of LocalExecutor's paymaster path.
 * See that module's docstring for why the executor must degrade gracefully
 * to self-pay rather than hard-failing on an unreachable paymaster or a
 * not-sponsorable write.
 */

const CONTRACT_ADDRESS = getAddress(
  "0x2222222222222222222222222222222222222222",
);
const WALLET_ADDRESS = getAddress("0x1111111111111111111111111111111111111111");
const FAKE_RAW_TX = "0xdeadbeef";
// What an HONEST relay returns from eth_sendRawTransaction: the keccak256 of
// the signed raw tx it was handed. LocalExecutor cross-checks the relay's
// answer against this, so the default fake paymaster must echo the
// mathematically correct hash.
const PAYMASTER_TX_HASH: `0x${string}` = keccak256(FAKE_RAW_TX);

const ABI = parseAbi(["function setValue(uint256 x) returns (bool)"]);

const CALL: ContractCall = {
  address: CONTRACT_ADDRESS,
  abi: ABI,
  functionName: "setValue",
  args: [1n],
};

/** A wallet provider stub that records every signed tx. */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  signedTxs: (TransactionRequestLegacy & { chainId?: number })[] = [];

  get address(): `0x${string}` {
    return WALLET_ADDRESS;
  }

  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    this.signedTxs.push(tx);
    return {
      rawTransaction: FAKE_RAW_TX,
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }
}

/** A fake Paymaster: only the three RPC methods LocalExecutor calls. */
function makeFakePaymaster(overrides?: {
  ethGetTransactionCount?: (address: string, block?: string) => Promise<number>;
  isSponsorable?: (tx: unknown) => Promise<boolean>;
  ethSendRawTransaction?: (
    raw: string,
    opts?: Record<string, string>,
  ) => Promise<string>;
}): {
  paymaster: Paymaster;
  ethGetTransactionCount: ReturnType<typeof vi.fn>;
  isSponsorable: ReturnType<typeof vi.fn>;
  ethSendRawTransaction: ReturnType<typeof vi.fn>;
} {
  const ethGetTransactionCount = vi.fn(
    overrides?.ethGetTransactionCount ?? (async () => 7),
  );
  const isSponsorable = vi.fn(overrides?.isSponsorable ?? (async () => true));
  const ethSendRawTransaction = vi.fn(
    overrides?.ethSendRawTransaction ?? (async () => PAYMASTER_TX_HASH),
  );
  const paymaster = {
    ethGetTransactionCount,
    isSponsorable,
    ethSendRawTransaction,
  } as unknown as Paymaster;
  return {
    paymaster,
    ethGetTransactionCount,
    isSponsorable,
    ethSendRawTransaction,
  };
}

function sendRawCount(mock: ReturnType<typeof mockPublicClient>): number {
  return mock.calls.filter((c) => c.method === "eth_sendRawTransaction").length;
}

function makeIntent(overrides?: Partial<Intent>): Intent {
  return { name: "test.op", description: "submit", call: CALL, ...overrides };
}

beforeEach(() => {
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

describe("LocalExecutor: intent.call required", () => {
  it("throws a descriptive error naming the intent when call is null", async () => {
    const mock = mockPublicClient();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
    });
    await expect(
      executor.execute({ name: "erc8004.register" }),
    ).rejects.toThrow(
      "LocalExecutor requires Intent.call (a web3 ContractFunction); got None for intent 'erc8004.register'",
    );
  });

  it("falls back to description when name is absent", async () => {
    const mock = mockPublicClient();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
    });
    await expect(
      executor.execute({ description: "fund the job" }),
    ).rejects.toThrow(/fund the job/);
  });
});

describe("LocalExecutor: no paymaster", () => {
  it("self-pays", async () => {
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
    });
    const result = await executor.execute(makeIntent());
    expect(result.status).toBe(1);
    expect(sendRawCount(mock)).toBe(1);
  });

  it("forwards a non-zero Intent.value into the signed self-pay tx", async () => {
    // Deliberate TS/Python divergence: TS forwards Intent.value into the
    // built transaction; Python's local executor port drops it. This locks
    // the TS behavior in place.
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
    });
    const result = await executor.execute(makeIntent({ value: 12345n }));
    expect(result.status).toBe(1);
    expect(wallet.signedTxs[0]?.value).toBe(12345n);
  });
});

describe("LocalExecutor: sponsored path", () => {
  it("sends via the paymaster with a zero gasPrice; the client never broadcasts", async () => {
    const overrideReceipt: Partial<MockHandlers> = {
      eth_getTransactionReceipt: () => ({
        status: "0x1",
        blockNumber: "0x1",
        blockHash: `0x${"aa".repeat(32)}`,
        transactionHash: PAYMASTER_TX_HASH,
        transactionIndex: "0x0",
        from: WALLET_ADDRESS,
        to: CONTRACT_ADDRESS,
        cumulativeGasUsed: "0x1e8480",
        gasUsed: "0x186a0",
        contractAddress: null,
        logs: [],
        logsBloom: `0x${"0".repeat(512)}`,
        effectiveGasPrice: "0x3b9aca00",
      }),
    };
    const mock = mockPublicClient(overrideReceipt);
    const wallet = new StubWallet();
    const { paymaster, ethSendRawTransaction } = makeFakePaymaster({
      isSponsorable: async () => true,
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(ethSendRawTransaction).toHaveBeenCalledTimes(1);
    expect(ethSendRawTransaction).toHaveBeenCalledWith(FAKE_RAW_TX, {
      UserAgent: "bnbagent/v1.0.0",
    });
    expect(sendRawCount(mock)).toBe(0); // client never broadcasts
    expect(wallet.signedTxs[0]?.gasPrice).toBe(0n);
    expect(result.transactionHash).toBe(PAYMASTER_TX_HASH);
    expect(result.status).toBe(1);
  });

  it("forwards a non-zero Intent.value into the signed sponsored tx", async () => {
    const overrideReceipt: Partial<MockHandlers> = {
      eth_getTransactionReceipt: () => ({
        status: "0x1",
        blockNumber: "0x1",
        blockHash: `0x${"aa".repeat(32)}`,
        transactionHash: PAYMASTER_TX_HASH,
        transactionIndex: "0x0",
        from: WALLET_ADDRESS,
        to: CONTRACT_ADDRESS,
        cumulativeGasUsed: "0x1e8480",
        gasUsed: "0x186a0",
        contractAddress: null,
        logs: [],
        logsBloom: `0x${"0".repeat(512)}`,
        effectiveGasPrice: "0x3b9aca00",
      }),
    };
    const mock = mockPublicClient(overrideReceipt);
    const wallet = new StubWallet();
    const { paymaster } = makeFakePaymaster({
      isSponsorable: async () => true,
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    const result = await executor.execute(makeIntent({ value: 6789n }));

    expect(result.status).toBe(1);
    expect(wallet.signedTxs[0]?.value).toBe(6789n);
    expect(wallet.signedTxs[0]?.gasPrice).toBe(0n);
  });

  it("falls back to self-pay when the tx is not sponsorable, and logs it", async () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const { paymaster, ethSendRawTransaction } = makeFakePaymaster({
      isSponsorable: async () => false,
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(ethSendRawTransaction).not.toHaveBeenCalled();
    expect(sendRawCount(mock)).toBe(1);
    expect(result.status).toBe(1);
    expect(
      infoSpy.mock.calls.some((args) =>
        String(args[0]).toLowerCase().includes("not sponsorable"),
      ),
    ).toBe(true);
  });

  it("falls back to self-pay when isSponsorable throws", async () => {
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const { paymaster, ethSendRawTransaction } = makeFakePaymaster({
      isSponsorable: async () => {
        throw new Error("megafuel 503");
      },
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(ethSendRawTransaction).not.toHaveBeenCalled();
    expect(sendRawCount(mock)).toBe(1);
    expect(result.status).toBe(1);
  });

  it("falls back to self-pay without reaching isSponsorable when the paymaster nonce fetch fails", async () => {
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const { paymaster, isSponsorable, ethSendRawTransaction } =
      makeFakePaymaster({
        ethGetTransactionCount: async () => {
          throw new Error("megafuel down");
        },
      });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(isSponsorable).not.toHaveBeenCalled();
    expect(ethSendRawTransaction).not.toHaveBeenCalled();
    expect(sendRawCount(mock)).toBe(1);
    expect(result.status).toBe(1);
  });

  it("a genuine preflight revert throws and neither path sends anything", async () => {
    const mock = mockPublicClient({
      eth_call: () => {
        throw new Error("execution reverted: SubmissionTooLate()");
      },
    });
    const wallet = new StubWallet();
    const { paymaster, ethSendRawTransaction } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    await expect(executor.execute(makeIntent())).rejects.toThrow(
      /Transaction would revert/,
    );
    expect(ethSendRawTransaction).not.toHaveBeenCalled();
    expect(sendRawCount(mock)).toBe(0);
  });

  it("propagates a post-sign broadcast failure unmodified and never falls back to self-pay (no double-broadcast)", async () => {
    // Fund-loss invariant: once a sponsored tx is signed and handed to the
    // paymaster, a failure on that *send* must never be retried into
    // self-pay — the paymaster may have already accepted/relayed it, so a
    // self-pay retry risks a second broadcast of (functionally) the same
    // operation. trySponsored's docstring calls this out explicitly; this
    // test is the guardrail that would go red if a future edit wrapped the
    // `ethSendRawTransaction` call in a catch-and-fallback.
    const mock = mockPublicClient();
    const wallet = new StubWallet();
    const sendError = new Error("paymaster broadcast failed: nonce too low");
    const { paymaster, ethSendRawTransaction } = makeFakePaymaster({
      isSponsorable: async () => true,
      ethSendRawTransaction: async () => {
        throw sendError;
      },
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: wallet,
      paymaster,
    });

    // Identity check (not just message match): the error that escapes
    // execute() must be the exact object the paymaster rejected with, i.e.
    // truly unmodified — not caught, wrapped, or replaced along the way.
    await expect(executor.execute(makeIntent())).rejects.toBe(sendError);
    expect(ethSendRawTransaction).toHaveBeenCalledTimes(1);
    expect(sendRawCount(mock)).toBe(0);
  });

  it("reports a relay hash that the chain never observed as unverified, not pending", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 1,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1_000);

    const error = await errorPromise;
    expect(error).toBeInstanceOf(RelaySubmissionUnverifiedError);
    expect(error).toMatchObject({
      name: "RelaySubmissionUnverifiedError",
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 1,
    });
  });

  it("keeps a chain-visible relay transaction pending while its receipt is unavailable", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
      eth_getTransactionByHash: () => ({
        blockHash: null,
        blockNumber: null,
        from: WALLET_ADDRESS,
        gas: "0x186a0",
        gasPrice: "0x0",
        hash: PAYMASTER_TX_HASH,
        input: "0x",
        nonce: "0x7",
        r: `0x${"00".repeat(32)}`,
        s: `0x${"00".repeat(32)}`,
        to: CONTRACT_ADDRESS,
        transactionIndex: null,
        type: "0x0",
        v: "0x1b",
        value: "0x0",
      }),
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 1,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(errorPromise).resolves.toMatchObject({
      name: "TransactionPendingError",
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 1,
    });
  });

  it("checks transaction visibility even when the receipt RPC never settles", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => new Promise(() => {}),
      eth_getTransactionByHash: () => ({
        blockHash: null,
        blockNumber: null,
        from: WALLET_ADDRESS,
        gas: "0x186a0",
        gasPrice: "0x0",
        hash: PAYMASTER_TX_HASH,
        input: "0x",
        nonce: "0x7",
        r: `0x${"00".repeat(32)}`,
        s: `0x${"00".repeat(32)}`,
        to: CONTRACT_ADDRESS,
        transactionIndex: null,
        type: "0x0",
        v: "0x1b",
        value: "0x0",
      }),
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 1,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(errorPromise).resolves.toMatchObject({
      name: "TransactionPendingError",
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 1,
    });
    expect(
      mock.calls.some((call) => call.method === "eth_getTransactionByHash"),
    ).toBe(true);
  });

  it("does not claim pending when the visibility RPC is still inconclusive", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
      eth_getTransactionByHash: () => new Promise(() => {}),
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 1,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(errorPromise).resolves.toMatchObject({
      name: "RelaySubmissionUnverifiedError",
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 1,
    });
  });
});

describe("LocalExecutor: relay hash consistency", () => {
  const DIVERGENT_HASH: `0x${string}` = `0x${"ef".repeat(32)}`;

  it("warns and tracks the signed-tx hash when the relay returns a different hash", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const mock = mockPublicClient();
    const { paymaster } = makeFakePaymaster({
      ethSendRawTransaction: async () => DIVERGENT_HASH,
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(result.status).toBe(1);
    expect(
      warnSpy.mock.calls.some((args) =>
        String(args[0]).includes("does not match the signed transaction hash"),
      ),
    ).toBe(true);
    // Receipt polling watched the mathematically true hash, not the relay's.
    const receiptCalls = mock.calls.filter(
      (call) => call.method === "eth_getTransactionReceipt",
    );
    expect(receiptCalls.length).toBeGreaterThan(0);
    expect(
      receiptCalls.every((call) => call.params[0] === PAYMASTER_TX_HASH),
    ).toBe(true);
    expect(mock.calls.some((call) => call.params[0] === DIVERGENT_HASH)).toBe(
      false,
    );
  });

  it("an unverified divergent relay hash surfaces the signed-tx hash", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });
    const { paymaster } = makeFakePaymaster({
      ethSendRawTransaction: async () => DIVERGENT_HASH,
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 1,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(errorPromise).resolves.toMatchObject({
      name: "RelaySubmissionUnverifiedError",
      txHash: PAYMASTER_TX_HASH,
    });
  });

  it("a matching relay hash does not warn", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const mock = mockPublicClient();
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
    });

    const result = await executor.execute(makeIntent());

    expect(result.status).toBe(1);
    expect(
      warnSpy.mock.calls.some((args) =>
        String(args[0]).includes("does not match"),
      ),
    ).toBe(false);
  });
});

describe("LocalExecutor: unseen relay hash fail-fast", () => {
  it("aborts after the fail-fast window instead of the full receipt timeout", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 300,
    });

    const errorPromise = executor.execute(makeIntent()).catch((error) => error);
    await vi.advanceTimersByTimeAsync(30_000);

    const error = await errorPromise;
    expect(error).toBeInstanceOf(RelaySubmissionUnverifiedError);
    expect(error).toMatchObject({
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 30,
    });
  });

  it("a chain-visible relay tx is exempt from the fail-fast window and waits the full timeout", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
      eth_getTransactionByHash: () => ({
        blockHash: null,
        blockNumber: null,
        from: WALLET_ADDRESS,
        gas: "0x186a0",
        gasPrice: "0x0",
        hash: PAYMASTER_TX_HASH,
        input: "0x",
        nonce: "0x7",
        r: `0x${"00".repeat(32)}`,
        s: `0x${"00".repeat(32)}`,
        to: CONTRACT_ADDRESS,
        transactionIndex: null,
        type: "0x0",
        v: "0x1b",
        value: "0x0",
      }),
    });
    const { paymaster } = makeFakePaymaster();
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      paymaster,
      receiptTimeout: 60,
    });

    let outcome: unknown = "unsettled";
    const errorPromise = executor.execute(makeIntent()).then(
      (value) => {
        outcome = value;
        return value;
      },
      (error) => {
        outcome = error;
        return error;
      },
    );
    // Past the 30s fail-fast window: still waiting (the tx was seen).
    await vi.advanceTimersByTimeAsync(31_000);
    expect(outcome).toBe("unsettled");

    await vi.advanceTimersByTimeAsync(29_000);
    await expect(errorPromise).resolves.toMatchObject({
      name: "TransactionPendingError",
      txHash: PAYMASTER_TX_HASH,
      timeoutSeconds: 60,
    });
  });
});

describe("LocalExecutor: receipt timeout resolution", () => {
  it("a ctor receiptTimeout beats a global setDefaultReceiptTimeout override", async () => {
    vi.useFakeTimers();
    setDefaultReceiptTimeout(7);
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      receiptTimeout: 42,
    });
    const promise = executor.execute(makeIntent());
    let caught: unknown;
    promise.catch((e) => {
      caught = e;
    });
    await vi.advanceTimersByTimeAsync(42_000);
    expect(caught).toBeInstanceOf(TransactionPendingError);
    expect((caught as TransactionPendingError).timeoutSeconds).toBe(42);
  });

  it("receiptTimeout: null resolves setDefaultReceiptTimeout lazily, at execute time", async () => {
    vi.useFakeTimers();
    const mock = mockPublicClient({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });
    // The executor is constructed *before* the setter is called — proving
    // execute() reads the timeout at call time, not at construction.
    const executor = new LocalExecutor({
      client: mock.client,
      walletProvider: new StubWallet(),
      receiptTimeout: null,
    });
    setDefaultReceiptTimeout(42);
    const promise = executor.execute(makeIntent());
    let caught: unknown;
    promise.catch((e) => {
      caught = e;
    });
    await vi.advanceTimersByTimeAsync(42_000);
    expect((caught as TransactionPendingError).timeoutSeconds).toBe(42);
  });
});
