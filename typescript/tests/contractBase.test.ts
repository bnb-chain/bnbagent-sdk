import { type TransactionRequestLegacy, getAddress, parseAbi } from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContractBase } from "../src/core/contractBase.js";
import { NonceManager } from "../src/core/nonceManager.js";
import {
  DEFAULT_GAS_FALLBACK,
  _resetTxConfigOverrides,
  setDefaultReceiptTimeout,
} from "../src/core/txConfig.js";
import { TransactionPendingError } from "../src/errors.js";
import type {
  ExecutionContext,
  Intent,
  IntentExecutor,
  TxResult,
} from "../src/wallets/intents.js";
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
 * Ports python/tests/test_contract_mixin.py: TestGasEstimation,
 * TestExecuteIntent, TestGasPriceFloor, TestReceiptTimeoutPassed, and
 * TestSendTxReceiptTimeoutPending, adapted to viem's RPC-layer mock instead
 * of a mocked `web3.py` contract-function object. See contractBase.ts's
 * module docstring for the JS-side adaptations (opaque/revert
 * classification, the hand-rolled receipt poll loop) this suite exercises.
 */

const CONTRACT_ADDRESS = getAddress(
  "0x2222222222222222222222222222222222222222",
);
const WALLET_ADDRESS = getAddress("0x1111111111111111111111111111111111111111");
const FAKE_RAW_TX = "0xdeadbeef";

const ABI = parseAbi([
  "function setValue(uint256 x) returns (bool)",
  "event ValueSet(uint256 indexed x)",
]);

/** Exposes ContractBase's protected members for direct testing. */
class TestContract extends ContractBase {
  callSendTx(req: {
    functionName: string;
    args: readonly unknown[];
    value?: bigint;
    gas?: bigint;
    skipPreflight?: boolean;
  }): Promise<TxResult> {
    return this.sendTx(req);
  }

  callExecuteIntent(intent: Intent): Promise<TxResult> {
    return this.executeIntent(intent);
  }

  callWithRetryPublic<T>(fn: () => Promise<T>): Promise<T> {
    return this.callWithRetry(fn);
  }

  callReadEvents(opts: {
    eventName: string;
    fromBlock: bigint;
    toBlock?: bigint | "latest";
    args?: Record<string, unknown>;
  }) {
    return this.readEvents(opts);
  }
}

/** A wallet provider stub that records every signed tx and every executor call. */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  signedTxs: (TransactionRequestLegacy & { chainId?: number })[] = [];
  signTransactionImpl: (tx: TransactionRequestLegacy) => Promise<SignedTx> =
    async () => ({
      rawTransaction: FAKE_RAW_TX,
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    });
  makeExecutorImpl: ((context: ExecutionContext) => IntentExecutor) | null =
    null;

  get address(): `0x${string}` {
    return WALLET_ADDRESS;
  }

  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    this.signedTxs.push(tx);
    return this.signTransactionImpl(tx);
  }

  override makeExecutor(context: ExecutionContext): IntentExecutor {
    if (!this.makeExecutorImpl) {
      throw new Error("StubWallet.makeExecutorImpl not configured");
    }
    return this.makeExecutorImpl(context);
  }
}

function makeContract(opts: {
  handlers?: Partial<MockHandlers>;
  wallet: null;
}): {
  contract: TestContract;
  mock: ReturnType<typeof mockPublicClient>;
  wallet: null;
};
function makeContract(opts?: {
  handlers?: Partial<MockHandlers>;
  wallet?: StubWallet;
}): {
  contract: TestContract;
  mock: ReturnType<typeof mockPublicClient>;
  wallet: StubWallet;
};
function makeContract(opts?: {
  handlers?: Partial<MockHandlers>;
  wallet?: StubWallet | null;
}) {
  const mock = mockPublicClient(opts?.handlers);
  const wallet = opts?.wallet === undefined ? new StubWallet() : opts.wallet;
  const contract = new TestContract({
    client: mock.client,
    address: CONTRACT_ADDRESS,
    abi: ABI,
    walletProvider: wallet,
  });
  return { contract, mock, wallet };
}

function sendRawCount(mock: ReturnType<typeof mockPublicClient>): number {
  return mock.calls.filter((c) => c.method === "eth_sendRawTransaction").length;
}

beforeEach(() => {
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

describe("sendTx: read-only guard", () => {
  it("throws when there is no wallet provider", async () => {
    const { contract } = makeContract({ wallet: null });
    await expect(
      contract.callSendTx({ functionName: "setValue", args: [1n] }),
    ).rejects.toThrow(
      "wallet_provider is required for write operations (client is read-only)",
    );
  });
});

describe("sendTx: gas estimation", () => {
  it("estimates with a 20% buffer", async () => {
    const { contract, wallet } = makeContract(); // default eth_estimateGas -> 100_000
    const result = await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    expect(result.status).toBe(1);
    expect(wallet.signedTxs[0]?.gas).toBe(120_000n);
  });

  it("explicit gas skips estimation entirely", async () => {
    const { contract, wallet, mock } = makeContract();
    await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
      gas: 500_000n,
    });
    expect(mock.calls.some((c) => c.method === "eth_estimateGas")).toBe(false);
    expect(wallet.signedTxs[0]?.gas).toBe(500_000n);
  });

  it("falls back to the default gas limit on a transport error", async () => {
    const { contract, wallet } = makeContract({
      handlers: {
        eth_estimateGas: () => {
          throw new Error("ECONNRESET: rpc down");
        },
      },
    });
    const result = await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    expect(result.status).toBe(1);
    expect(wallet.signedTxs[0]?.gas).toBe(DEFAULT_GAS_FALLBACK);
  });

  it("a genuine revert during estimation throws before any broadcast", async () => {
    const { contract, wallet, mock } = makeContract({
      handlers: {
        eth_estimateGas: () => {
          throw new Error("execution reverted: NotProvider");
        },
      },
    });
    await expect(
      contract.callSendTx({ functionName: "setValue", args: [1n] }),
    ).rejects.toThrow(/Transaction would revert/);
    expect(wallet.signedTxs).toHaveLength(0);
    expect(sendRawCount(mock)).toBe(0);
  });

  it("an opaque 0x revert during estimation falls back to the default gas limit", async () => {
    const { contract, wallet } = makeContract({
      handlers: {
        eth_estimateGas: () => {
          throw Object.assign(new Error("execution reverted"), {
            data: "0x",
          });
        },
      },
    });
    const result = await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    expect(result.status).toBe(1);
    expect(wallet.signedTxs[0]?.gas).toBe(DEFAULT_GAS_FALLBACK);
  });

  it("skipPreflight bypasses estimation and uses the default gas limit", async () => {
    const { contract, wallet, mock } = makeContract();
    await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
      skipPreflight: true,
    });
    expect(mock.calls.some((c) => c.method === "eth_estimateGas")).toBe(false);
    expect(mock.calls.some((c) => c.method === "eth_call")).toBe(false);
    expect(wallet.signedTxs[0]?.gas).toBe(DEFAULT_GAS_FALLBACK);
  });
});

describe("sendTx: preflight eth_call", () => {
  it("a genuine revert throws before broadcast", async () => {
    const { contract, mock } = makeContract({
      handlers: {
        eth_call: () => {
          throw new Error("execution reverted: Paused");
        },
      },
    });
    await expect(
      contract.callSendTx({ functionName: "setValue", args: [1n] }),
    ).rejects.toThrow(/Transaction would revert/);
    expect(sendRawCount(mock)).toBe(0);
  });

  it("an opaque 0x revert warns and proceeds to broadcast", async () => {
    const { contract, mock } = makeContract({
      handlers: {
        eth_call: () => {
          throw Object.assign(new Error("execution reverted"), {
            data: "0x",
          });
        },
      },
    });
    const result = await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    expect(result.status).toBe(1);
    expect(sendRawCount(mock)).toBe(1);
  });

  it("a timeout warns and proceeds to broadcast", async () => {
    vi.useFakeTimers();
    const { contract, mock } = makeContract({
      handlers: {
        eth_call: () => new Promise(() => {}), // never resolves
      },
    });
    const promise = contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    let result: TxResult | undefined;
    promise.then((r) => {
      result = r;
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(result?.status).toBe(1);
    expect(sendRawCount(mock)).toBe(1);
  });
});

describe("sendTx: gas-price floor", () => {
  it("floors at 1 gwei on testnet when the network price is far below it", async () => {
    const { contract, wallet } = makeContract({
      handlers: {
        eth_chainId: () => "0x61", // 97
        eth_gasPrice: () => "0x64", // 100 wei
      },
    });
    await contract.callSendTx({ functionName: "setValue", args: [1n] });
    expect(wallet.signedTxs[0]?.gasPrice).toBe(1_000_000_000n);
  });

  it("uses the network price (+20%) when it is above the floor", async () => {
    const { contract, wallet } = makeContract({
      handlers: {
        eth_chainId: () => "0x61", // 97
        eth_gasPrice: () => "0x12a05f200", // 5 Gwei
      },
    });
    await contract.callSendTx({ functionName: "setValue", args: [1n] });
    expect(wallet.signedTxs[0]?.gasPrice).toBe(6_000_000_000n);
  });
});

describe("sendTx: receipt timeout", () => {
  async function expectPendingAfter(
    timeoutSeconds: number,
    setup?: () => void,
  ) {
    vi.useFakeTimers();
    setup?.();
    const { contract, mock } = makeContract({
      handlers: {
        eth_getTransactionReceipt: () => {
          throw new Error("not found");
        },
      },
    });
    const promise = contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    let caught: unknown;
    promise.catch((e) => {
      caught = e;
    });
    await vi.advanceTimersByTimeAsync(timeoutSeconds * 1000);
    expect(caught).toBeInstanceOf(TransactionPendingError);
    expect((caught as TransactionPendingError).txHash).toBe(FAKE_TX_HASH);
    expect((caught as TransactionPendingError).timeoutSeconds).toBe(
      timeoutSeconds,
    );
    expect(sendRawCount(mock)).toBe(1);
  }

  it("defaults to 300 seconds", async () => {
    await expectPendingAfter(300);
  });

  it("honors the BNBAGENT_RECEIPT_TIMEOUT env var", async () => {
    await expectPendingAfter(10, () => {
      vi.stubEnv("BNBAGENT_RECEIPT_TIMEOUT", "10");
    });
  });

  it("honors setDefaultReceiptTimeout()", async () => {
    await expectPendingAfter(7, () => {
      setDefaultReceiptTimeout(7);
    });
  });

  it("resolves the timeout lazily, at send time", async () => {
    // The setter is called *after* the contract already exists — proving
    // sendTx reads the timeout at call time, not at construction/first-use.
    vi.useFakeTimers();
    const { contract } = makeContract({
      handlers: {
        eth_getTransactionReceipt: () => {
          throw new Error("not found");
        },
      },
    });
    setDefaultReceiptTimeout(42);
    const promise = contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    let caught: unknown;
    promise.catch((e) => {
      caught = e;
    });
    await vi.advanceTimersByTimeAsync(42_000);
    expect((caught as TransactionPendingError).timeoutSeconds).toBe(42);
  });

  it("is never retried: exactly one broadcast for one pending outcome", async () => {
    await expectPendingAfter(5, () => {
      setDefaultReceiptTimeout(5);
    });
  });
});

describe("sendTx: on-chain revert", () => {
  it("a reverted receipt throws, without raising TransactionPendingError", async () => {
    const { contract, mock } = makeContract({
      handlers: {
        eth_getTransactionReceipt: () => ({
          status: "0x0",
          blockNumber: "0x1",
          blockHash: `0x${"aa".repeat(32)}`,
          transactionHash: FAKE_TX_HASH,
          transactionIndex: "0x0",
          from: WALLET_ADDRESS,
          to: CONTRACT_ADDRESS,
          cumulativeGasUsed: "0x1",
          gasUsed: "0x1",
          contractAddress: null,
          logs: [],
          logsBloom: `0x${"0".repeat(512)}`,
          effectiveGasPrice: "0x1",
        }),
      },
    });
    await expect(
      contract.callSendTx({ functionName: "setValue", args: [1n] }),
    ).rejects.toThrow(`Transaction reverted on-chain: ${FAKE_TX_HASH}`);
    expect(sendRawCount(mock)).toBe(1);
  });
});

describe("sendTx: retry loop", () => {
  it("re-syncs and retries once on a nonce-too-low error", async () => {
    let attempt = 0;
    const { contract, wallet, mock } = makeContract({
      handlers: {
        eth_sendRawTransaction: () => {
          attempt++;
          if (attempt === 1) {
            throw new Error("nonce too low: next nonce 1, tx nonce 0");
          }
          return FAKE_TX_HASH;
        },
      },
    });
    const result = await contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    expect(result.status).toBe(1);
    expect(attempt).toBe(2);
    expect(sendRawCount(mock)).toBe(2);
    expect(wallet.signedTxs).toHaveLength(2);
  });

  it("backs off once on a 429 and then retries successfully", async () => {
    vi.useFakeTimers();
    let attempt = 0;
    const { contract, mock } = makeContract({
      handlers: {
        eth_sendRawTransaction: () => {
          attempt++;
          if (attempt === 1) {
            throw new Error("429 Too Many Requests");
          }
          return FAKE_TX_HASH;
        },
      },
    });
    const promise = contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    let resolved = false;
    promise.then(() => {
      resolved = true;
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(resolved).toBe(false); // still sleeping through the 1s backoff
    await vi.advanceTimersByTimeAsync(1000);
    expect(resolved).toBe(true);
    expect(attempt).toBe(2);
    expect(sendRawCount(mock)).toBe(2);
  });

  it("re-seeds the nonce on a 429 retry (no nonce gap)", async () => {
    vi.useFakeTimers();
    let attempt = 0;
    let nonceReads = 0;
    const { contract } = makeContract({
      handlers: {
        eth_getTransactionCount: () => {
          nonceReads++;
          return "0x5"; // pending nonce = 5, stable (tx never broadcast on attempt 1)
        },
        eth_sendRawTransaction: () => {
          attempt++;
          if (attempt === 1) {
            throw new Error("429 Too Many Requests");
          }
          return FAKE_TX_HASH;
        },
      },
    });
    const promise = contract.callSendTx({
      functionName: "setValue",
      args: [1n],
    });
    let resolved = false;
    promise.then(() => {
      resolved = true;
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(resolved).toBe(true);
    // The rate-limit branch must reset the NonceManager so the second attempt
    // re-seeds from chain (getTransactionCount called on BOTH attempts) and
    // reuses nonce 5 instead of skipping to 6 — otherwise every retry burns a
    // nonce and strands the account.
    expect(nonceReads).toBe(2);
  });

  it("an unrelated broadcast error (insufficient funds) throws immediately, once", async () => {
    const { contract, mock } = makeContract({
      handlers: {
        eth_sendRawTransaction: () => {
          throw new Error("insufficient funds for gas * price + value");
        },
      },
    });
    await expect(
      contract.callSendTx({ functionName: "setValue", args: [1n] }),
    ).rejects.toThrow(/insufficient funds/);
    expect(sendRawCount(mock)).toBe(1);
  });
});

describe("executeIntent", () => {
  it("throws the read-only error when there is no wallet provider", async () => {
    const { contract } = makeContract({ wallet: null });
    await expect(contract.callExecuteIntent({ name: "x.y" })).rejects.toThrow(
      "wallet_provider is required for write operations (client is read-only)",
    );
  });

  it("builds the executor via makeExecutor with an ExecutionContext, and caches it", async () => {
    const wallet = new StubWallet();
    const contexts: ExecutionContext[] = [];
    let executeCalls = 0;
    wallet.makeExecutorImpl = (context) => {
      contexts.push(context);
      return {
        execute: async (_intent: Intent): Promise<TxResult> => {
          executeCalls++;
          return { transactionHash: FAKE_TX_HASH, status: 1, receipt: null };
        },
      };
    };
    const { contract, mock } = makeContract({ wallet });

    const r1 = await contract.callExecuteIntent({ name: "x.y" });
    const r2 = await contract.callExecuteIntent({ name: "x.z" });

    expect(r1.transactionHash).toBe(FAKE_TX_HASH);
    expect(r2.transactionHash).toBe(FAKE_TX_HASH);
    expect(contexts).toHaveLength(1); // makeExecutor called exactly once
    expect(contexts[0]?.client).toBe(mock.client);
    expect(executeCalls).toBe(2); // execute() called for each intent
  });
});

describe("callWithRetry", () => {
  it("retries on rate limit and then returns the result", async () => {
    vi.useFakeTimers();
    const { contract } = makeContract();
    let calls = 0;
    const promise = contract.callWithRetryPublic(async () => {
      calls++;
      if (calls === 1) {
        throw new Error("429 Too Many Requests");
      }
      return "ok";
    });
    let result: string | undefined;
    promise.then((r) => {
      result = r;
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(result).toBe("ok");
    expect(calls).toBe(2);
  });

  it("rethrows a non-rate-limit error immediately", async () => {
    const { contract } = makeContract();
    let calls = 0;
    await expect(
      contract.callWithRetryPublic(async () => {
        calls++;
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");
    expect(calls).toBe(1);
  });
});

describe("readEvents", () => {
  it("forwards address/fromBlock/toBlock to getLogs", async () => {
    const { contract, mock } = makeContract();
    const events = await contract.callReadEvents({
      eventName: "ValueSet",
      fromBlock: 5n,
      toBlock: 10n,
    });
    expect(events).toEqual([]);
    const call = mock.calls.find((c) => c.method === "eth_getLogs");
    expect(call).toBeDefined();
    const params = call?.params[0] as {
      address: string;
      fromBlock: string;
      toBlock: string;
    };
    expect(params.address.toLowerCase()).toBe(CONTRACT_ADDRESS.toLowerCase());
    expect(params.fromBlock).toBe("0x5");
    expect(params.toBlock).toBe("0xa");
  });

  it("throws for an event name absent from the ABI", async () => {
    const { contract } = makeContract();
    await expect(
      contract.callReadEvents({ eventName: "Nope", fromBlock: 0n }),
    ).rejects.toThrow(/no event named/);
  });
});
