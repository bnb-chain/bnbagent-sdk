/**
 * Ports the read/write behavior of `python/bnbagent/erc20/client.py`'s
 * `MinimalERC20Client` (there is no dedicated Python test file for it; its
 * five methods are exercised indirectly through `ERC8183Client`'s
 * payment-token helpers there). This suite drives the TS port directly
 * through a real `PublicClient` over `mockTransport` — `decimals` / `symbol`
 * / `balanceOf` / `allowance` decode a hand-encoded `eth_call` return value,
 * `approve` is checked against `ContractBase`'s shared write path (read-only
 * guard + broadcast), which `contractBase.test.ts` covers exhaustively on
 * its own.
 */

import {
  type TransactionRequestLegacy,
  encodeFunctionResult,
  getAddress,
} from "viem";
import { describe, expect, it } from "vitest";
import { erc20Abi } from "../src/abis/erc20.js";
import { MinimalERC20Client } from "../src/erc20/client.js";
import type { TxResult } from "../src/wallets/intents.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import { FAKE_TX_HASH, mockPublicClient } from "./helpers/mockTransport.js";

const TOKEN_ADDRESS = getAddress("0x3333333333333333333333333333333333333333");
const WALLET_ADDRESS = getAddress("0x1111111111111111111111111111111111111111");
const OWNER = getAddress("0x4444444444444444444444444444444444444444");
const SPENDER = getAddress("0x5555555555555555555555555555555555555555");
const FAKE_RAW_TX = "0xdeadbeef";

/** A minimal signing wallet stub — enough to drive `sendTx`'s broadcast path. */
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
      rawTransaction: FAKE_RAW_TX,
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }
}

function withEthCall(result: `0x${string}`) {
  return mockPublicClient({ eth_call: () => result });
}

describe("MinimalERC20Client", () => {
  it("checksums the token address passed to the constructor", () => {
    const mock = mockPublicClient();
    const client = new MinimalERC20Client(
      mock.client,
      TOKEN_ADDRESS.toLowerCase(),
    );
    expect(client.address).toBe(TOKEN_ADDRESS);
  });

  it("rejects a malformed token address before any RPC call", () => {
    const mock = mockPublicClient();
    expect(
      () => new MinimalERC20Client(mock.client, "not-an-address"),
    ).toThrow();
    expect(mock.calls).toHaveLength(0);
  });

  it("decimals() decodes the uint8 return value", async () => {
    const mock = withEthCall(
      encodeFunctionResult({
        abi: erc20Abi,
        functionName: "decimals",
        result: 18,
      }),
    );
    const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
    await expect(client.decimals()).resolves.toBe(18);
  });

  it("symbol() decodes the string return value", async () => {
    const mock = withEthCall(
      encodeFunctionResult({
        abi: erc20Abi,
        functionName: "symbol",
        result: "USDT",
      }),
    );
    const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
    await expect(client.symbol()).resolves.toBe("USDT");
  });

  it("balanceOf() checksums the account and decodes the balance", async () => {
    const mock = withEthCall(
      encodeFunctionResult({
        abi: erc20Abi,
        functionName: "balanceOf",
        result: 1_000_000n,
      }),
    );
    const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
    await expect(client.balanceOf(OWNER.toLowerCase())).resolves.toBe(
      1_000_000n,
    );
  });

  it("balanceOf() rejects a malformed account address before any RPC call", async () => {
    const mock = mockPublicClient();
    const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
    await expect(client.balanceOf("nope")).rejects.toThrow();
    expect(mock.calls.some((c) => c.method === "eth_call")).toBe(false);
  });

  it("allowance() checksums both addresses and decodes the value", async () => {
    const mock = withEthCall(
      encodeFunctionResult({
        abi: erc20Abi,
        functionName: "allowance",
        result: 42n,
      }),
    );
    const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
    await expect(
      client.allowance(OWNER.toLowerCase(), SPENDER.toLowerCase()),
    ).resolves.toBe(42n);
  });

  describe("approve", () => {
    it("throws ContractBase's read-only message when there is no wallet provider", async () => {
      const mock = mockPublicClient();
      const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS);
      await expect(client.approve(SPENDER, 100n)).rejects.toThrow(
        "wallet_provider is required for write operations (client is read-only)",
      );
      expect(mock.calls).toHaveLength(0);
    });

    it("broadcasts and returns a TxResult when a wallet provider is present", async () => {
      const mock = mockPublicClient();
      const wallet = new StubWallet();
      const client = new MinimalERC20Client(mock.client, TOKEN_ADDRESS, wallet);

      const result: TxResult = await client.approve(
        SPENDER.toLowerCase(),
        100n,
      );

      expect(result.transactionHash).toBe(FAKE_TX_HASH);
      expect(result.status).toBe(1);
      expect(
        mock.calls.some((c) => c.method === "eth_sendRawTransaction"),
      ).toBe(true);
    });
  });
});
