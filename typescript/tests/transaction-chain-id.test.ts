import type { PublicClient } from "viem";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";

import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";
afterEach(() => {
  vi.useRealTimers();
  NonceManager._clearAll();
});
const address = `0x${"11".repeat(20)}` as const;

describe("transaction-chain-id", () => {
  it.each([undefined, 0, -1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1])(
    "rejects runtime chainId %s before signing",
    async (chainId) => {
      const wallet = new EVMWalletProvider({
        password: "pw",
        privateKey: `0x${"aa".repeat(32)}`,
        persist: false,
      });
      await expect(
        wallet.signTransaction({ chainId } as never),
      ).rejects.toThrow(/chainId is required/);
    },
  );
});
