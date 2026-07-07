import { type TransactionRequestLegacy, getAddress, parseAbi } from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";
import type { Paymaster } from "../src/core/paymaster.js";
import {
  _resetTxConfigOverrides,
  setDefaultReceiptTimeout,
} from "../src/core/txConfig.js";
import { TransactionPendingError } from "../src/errors.js";
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
const PAYMASTER_TX_HASH: `0x${string}` = `0x${"cd".repeat(32)}`;

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
