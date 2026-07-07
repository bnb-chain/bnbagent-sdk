/**
 * Minimal ERC-20 client — `decimals` / `symbol` / `balanceOf` / `allowance` /
 * `approve` only.
 *
 * Port of `python/bnbagent/erc20/client.py`'s `MinimalERC20Client`. External
 * callers typically reach this through `ERC8183Client`'s payment-token
 * helpers rather than constructing it directly (mirroring the Python
 * module's own guidance), but it is a complete, independently usable
 * `ContractBase` subclass.
 */

import { type PublicClient, getAddress } from "viem";
import { erc20Abi } from "../abis/erc20.js";
import { ContractBase } from "../core/contractBase.js";
import type { TxResult } from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";

export class MinimalERC20Client extends ContractBase {
  constructor(
    client: PublicClient,
    tokenAddress: string,
    walletProvider?: WalletProvider | null,
  ) {
    super({
      client,
      address: getAddress(tokenAddress),
      abi: erc20Abi,
      walletProvider: walletProvider ?? null,
    });
  }

  /** Number of decimals the token uses (e.g. `18`, `6`). */
  async decimals(): Promise<number> {
    const result = await this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: erc20Abi,
        functionName: "decimals",
      }),
    );
    return Number(result);
  }

  /** The token's ticker symbol (e.g. `"USDT"`). */
  async symbol(): Promise<string> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: erc20Abi,
        functionName: "symbol",
      }),
    );
  }

  /** The token balance held by `account`. */
  async balanceOf(account: string): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: erc20Abi,
        functionName: "balanceOf",
        args: [getAddress(account)],
      }),
    );
  }

  /** The amount `spender` is currently allowed to draw from `owner`. */
  async allowance(owner: string, spender: string): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: erc20Abi,
        functionName: "allowance",
        args: [getAddress(owner), getAddress(spender)],
      }),
    );
  }

  /**
   * Approve `spender` to draw up to `amount` from this wallet's balance.
   *
   * Requires a wallet provider — `ContractBase.sendTx` throws its read-only
   * error otherwise.
   */
  async approve(spender: string, amount: bigint): Promise<TxResult> {
    return this.sendTx({
      functionName: "approve",
      args: [getAddress(spender), amount],
    });
  }
}
