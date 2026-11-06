import type { PublicClient } from "viem";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";
import {
  sendSelfPayTx,
  waitForReceiptAndInterpret,
} from "../src/core/txSender.js";

import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";
afterEach(() => {
  vi.useRealTimers();
  NonceManager._clearAll();
});
const address = `0x${"11".repeat(20)}` as const;

describe("transaction-hash", () => {
  it.each([`0x${"aa".repeat(32)}`, null, undefined, "invalid"])(
    "keeps receipt identity %s pending when it does not match",
    async (transactionHash) => {
      const client = {
        getTransactionReceipt: vi.fn().mockResolvedValue({
          transactionHash,
          status: "success",
        }),
      } as unknown as PublicClient;
      await expect(
        waitForReceiptAndInterpret(client, `0x${"bb".repeat(32)}`, 1),
      ).rejects.toThrow(/receipt hash does not match/);
    },
  );
  it.each([`0x${"ab".repeat(32)}`, null, undefined, 0, {}, "invalid"])(
    "preserves local pending identity for RPC hash %s",
    async (rpcHash) => {
      const wallet = new EVMWalletProvider({
        password: "pw",
        privateKey: `0x${"aa".repeat(32)}`,
        persist: false,
      });
      const sign = vi.spyOn(wallet, "signTransaction");
      const receipt = vi.fn();
      const send = vi.fn().mockResolvedValue(rpcHash);
      const client = {
        getChainId: vi.fn().mockResolvedValue(56),
        getTransactionCount: vi.fn().mockResolvedValue(0),
        getGasPrice: vi.fn().mockResolvedValue(1000000000n),
        sendRawTransaction: send,
        getTransactionReceipt: receipt,
      } as unknown as PublicClient;
      await expect(
        sendSelfPayTx({
          client,
          walletProvider: wallet,
          to: address,
          data: "0x",
          value: 0n,
          gas: 21000n,
          skipPreflight: true,
        }),
      ).rejects.toMatchObject({
        name: "TransactionPendingError",
        txHash: expect.stringMatching(/^0x[0-9a-f]{64}$/),
      });
      expect(
        await NonceManager.forAccount(
          client as never,
          wallet.address,
        ).getNonce(),
      ).toBe(1);
      expect(receipt).not.toHaveBeenCalled();
      expect(sign).toHaveBeenCalledTimes(1);
      expect(send).toHaveBeenCalledTimes(1);
    },
  );
});
