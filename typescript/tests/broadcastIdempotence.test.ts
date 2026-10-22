import {
  type Hex,
  type TransactionRequestLegacy,
  keccak256,
  parseAbi,
} from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContractBase } from "../src/core/contractBase.js";
import { NonceManager } from "../src/core/nonceManager.js";
import { Paymaster } from "../src/core/paymaster.js";
import {
  _resetTxConfigOverrides,
  setDefaultReceiptTimeout,
} from "../src/core/txConfig.js";
import { TransactionPendingError } from "../src/errors.js";
import { LocalExecutor } from "../src/wallets/localExecutor.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import {
  defaultMockHandlers,
  mockPublicClient,
} from "./helpers/mockTransport.js";

const ADDRESS = `0x${"11".repeat(20)}` as const;
const TARGET = `0x${"22".repeat(20)}` as const;
const ABI = parseAbi(["function pay() payable"]);
const AMBIGUOUS_ERRORS = [
  "already known",
  "replacement transaction underpriced",
  "429 Too Many Requests",
  "request timed out",
  "connection reset",
];

class RecordingWallet extends WalletProvider {
  static override readonly kind = "test";
  signed: TransactionRequestLegacy[] = [];
  get address() {
    return ADDRESS;
  }
  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    this.signed.push(tx);
    // Deliberately not an executable transaction: this is a control-flow test.
    const rawTransaction =
      `0xdead${Number(tx.nonce).toString(16).padStart(4, "0")}` as Hex;
    return {
      rawTransaction,
      hash: keccak256(rawTransaction),
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }
}

class TestContract extends ContractBase {
  send() {
    return this.sendTx({
      functionName: "pay",
      args: [],
      value: 1n,
      gas: 100_000n,
    });
  }
}

beforeEach(() => {
  NonceManager._clearAll();
  _resetTxConfigOverrides();
  setDefaultReceiptTimeout(1);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  NonceManager._clearAll();
  _resetTxConfigOverrides();
  vi.useRealTimers();
});

function setup(
  entry: string,
  error: string,
  pending = false,
  receiptStatus = "0x1",
) {
  const wallet = new RecordingWallet();
  const raw: Hex[] = [];
  const defaults = defaultMockHandlers();
  const mock = mockPublicClient({
    eth_getTransactionCount: () => (raw.length === 0 ? "0x0" : "0x1"),
    eth_sendRawTransaction: (params) => {
      raw.push(params[0] as Hex);
      if (raw.length === 1) throw new Error(error);
      return keccak256(params[0] as Hex);
    },
    eth_getTransactionReceipt: (params) =>
      pending
        ? null
        : {
            ...(defaults.eth_getTransactionReceipt([]) as object),
            transactionHash: params[0],
            status: receiptStatus,
          },
  });
  const contract = new TestContract({
    client: mock.client,
    address: TARGET,
    abi: ABI,
    walletProvider: wallet,
  });
  const executor = new LocalExecutor({
    client: mock.client,
    walletProvider: wallet,
  });
  const send = () =>
    entry === "contract"
      ? contract.send()
      : executor.execute({
          call: { address: TARGET, abi: ABI, functionName: "pay", args: [] },
          value: 1n,
          gas: 100_000n,
        });
  return { wallet, raw, mock, send };
}

describe.each(["contract", "executor"])("%s broadcast identity", (entry) => {
  it("does not classify a URL or raw transaction as the rejection reason", async () => {
    const { wallet, mock, send } = setup(
      entry,
      'insufficient funds for gas * price + value\n\nURL: https://network.example/rpc\nRequest body: {"params":["0xdead429beef"]}',
    );
    mock.handlers.eth_getTransactionCount = () => "0x0";
    await expect(send()).rejects.toThrow(/insufficient funds/);
    expect(wallet.signed).toHaveLength(1);
    await send();
    expect(wallet.signed.map((tx) => tx.nonce)).toEqual([0, 0]);
  });
  it.each(AMBIGUOUS_ERRORS)(
    "keeps one signature and the original hash after %s",
    async (error) => {
      const { wallet, raw, mock, send } = setup(entry, error);
      const resultPromise = send().catch((err: unknown) => err);
      await vi.runAllTimersAsync();
      const result = await resultPromise;
      expect(wallet.signed.map((tx) => tx.nonce)).toEqual([0]);
      expect(raw).toHaveLength(1);
      expect(result).toMatchObject({
        status: 1,
        transactionHash: keccak256(raw[0] as Hex),
      });
      expect(
        mock.calls.filter((call) => call.method === "eth_getTransactionCount"),
      ).toHaveLength(1);
    },
  );

  it.each(AMBIGUOUS_ERRORS)(
    "surfaces the original hash as pending after %s without signing again",
    async (error) => {
      const { wallet, raw, send } = setup(entry, error, true);
      const resultPromise = send().catch((err: unknown) => err);
      await vi.runAllTimersAsync();
      const result = await resultPromise;
      expect(result).toBeInstanceOf(TransactionPendingError);
      expect((result as TransactionPendingError).txHash).toBe(
        keccak256(raw[0] as Hex),
      );
      expect(wallet.signed).toHaveLength(1);
      expect(raw).toHaveLength(1);
    },
  );

  it("reports a confirmed revert without turning it into success, pending, or a new signature", async () => {
    const { wallet, raw, send } = setup(entry, "already known", false, "0x0");
    const resultPromise = send().catch((err: unknown) => err);
    await vi.runAllTimersAsync();
    const result = await resultPromise;
    expect(result).toBeInstanceOf(Error);
    expect(result).not.toBeInstanceOf(TransactionPendingError);
    expect((result as Error).message).toContain(
      "Transaction reverted on-chain",
    );
    expect(wallet.signed).toHaveLength(1);
    expect(raw).toHaveLength(1);
  });
});

describe("real paymaster duplicate submission", () => {
  it.each(["already known", "nonce too low"])(
    "reconciles %s without a new-nonce self-pay",
    async (reason) => {
      const wallet = new RecordingWallet();
      const defaults = defaultMockHandlers();
      const mock = mockPublicClient({
        eth_getTransactionCount: () => "0x8",
        eth_getTransactionReceipt: (params) => ({
          ...(defaults.eth_getTransactionReceipt([]) as object),
          transactionHash: params[0],
        }),
        eth_getTransactionByHash: () => null,
      });
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_url, init) => {
          const { method } = JSON.parse(init.body);
          return new Response(
            JSON.stringify(
              method === "eth_sendRawTransaction"
                ? { error: { code: -32000, message: reason } }
                : {
                    result:
                      method === "eth_getTransactionCount"
                        ? "0x7"
                        : { sponsorable: true },
                  },
            ),
          );
        }),
      );
      const executor = new LocalExecutor({
        client: mock.client,
        walletProvider: wallet,
        paymaster: new Paymaster("https://relay.example"),
        selfPayFallback: false,
      });
      const result = await executor.execute({
        call: { address: TARGET, abi: ABI, functionName: "pay", args: [] },
        gas: 100_000n,
      });
      expect(result.transactionHash).toBe(keccak256("0xdead0007"));
      expect(wallet.signed.map((tx) => tx.nonce)).toEqual([7]);
      expect(
        mock.calls.filter((call) => call.method === "eth_sendRawTransaction"),
      ).toHaveLength(0);
    },
  );
});
