/**
 * Public tx-tuning knobs: per-chain gas-price floor + configurable receipt timeout.
 *
 * Port of `python/bnbagent/core/contract_mixin.py` (module-level constants +
 * `set_min_gas_price_wei` / `min_gas_price_wei` / `set_default_receipt_timeout`
 * / `get_default_receipt_timeout`); see that module and its tests
 * (`python/tests/test_tx_config.py`) for the authoritative semantics this
 * file mirrors.
 *
 * The gas floor is per-chain because the real minimum differs by ~10x: BSC
 * mainnet sits at 0.1 Gwei since the gas-price reform, while testnet's
 * cutoff is ~1 Gwei. `eth_gasPrice` on low-traffic RPCs (BSC testnet in
 * particular) sometimes returns values below what validators actually
 * require, leaving the broadcast stuck in mempool without this floor.
 */

import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
} from "../networks/addresses.js";
import { getEnv } from "./envUtil.js";

export const MAX_RETRIES = 5;
export const RETRY_BASE_DELAY = 1.0;

/** Default/fallback gas-price floor (wei) for chains not in the per-chain table. */
export const MIN_GAS_PRICE_WEI = 100_000_000n; // 0.1 Gwei

export const MIN_GAS_PRICE_WEI_PER_CHAIN: Record<number, bigint> = {
  [BSC_MAINNET_CHAIN_ID]: 100_000_000n, // 0.1 Gwei
  [BSC_TESTNET_CHAIN_ID]: 1_000_000_000n, // 1 Gwei — above the testnet validator cutoff
};

/**
 * Fallback gas limit used only when on-chain estimation is unavailable
 * (transport/RPC error, opaque revert data) or bypassed (`skipPreflight`).
 * Nodes require `balance >= gasLimit * gasPrice` upfront, so a blanket 2M
 * limit would demand ~0.007 BNB per tx while typical writes burn 50-150k gas
 * — estimation keeps the entry cost proportional to real usage.
 */
export const DEFAULT_GAS_FALLBACK = 2_000_000n;

/**
 * Default seconds to wait for a transaction receipt. Shared by every write
 * path (`ContractBase.sendTx` here and the local intent executor).
 */
export const DEFAULT_RECEIPT_TIMEOUT = 300;

let minGasPriceOverride: bigint | null = null;
let receiptTimeoutOverride: number | null = null;

/**
 * Pin the gas-price floor (wei) for *all* chains.
 *
 * Precedence: `setMinGasPriceWei()` > `BNBAGENT_MIN_GAS_PRICE_WEI` env var
 * (global, all chains) > per-chain default.
 *
 * @throws {Error} `"min gas price must be positive"` if `wei <= 0`.
 */
export function setMinGasPriceWei(wei: bigint): void {
  if (wei <= 0n) {
    throw new Error("min gas price must be positive");
  }
  minGasPriceOverride = wei;
}

/**
 * Resolve the gas-price floor (wei) for `chainId`.
 *
 * Precedence: the {@link setMinGasPriceWei} override, then the
 * `BNBAGENT_MIN_GAS_PRICE_WEI` env var, then the per-chain default
 * ({@link MIN_GAS_PRICE_WEI} for unknown chains).
 */
export function minGasPriceWei(chainId: number): bigint {
  if (minGasPriceOverride !== null) {
    return minGasPriceOverride;
  }
  const raw = getEnv("BNBAGENT_MIN_GAS_PRICE_WEI");
  if (raw !== undefined) {
    const parsed = parseBigIntStrict(raw);
    if (parsed !== null) {
      return parsed;
    }
    console.warn(
      `Ignoring invalid BNBAGENT_MIN_GAS_PRICE_WEI=${JSON.stringify(raw)}`,
    );
  }
  return MIN_GAS_PRICE_WEI_PER_CHAIN[chainId] ?? MIN_GAS_PRICE_WEI;
}

/**
 * Set the default transaction-receipt timeout (seconds) for every write path.
 *
 * Takes precedence over the `BNBAGENT_RECEIPT_TIMEOUT` env var and applies to
 * operations executed after the call — including a cached intent executor,
 * which resolves the default lazily at execute time.
 *
 * @throws {Error} `"receipt timeout must be positive"` if `seconds <= 0`.
 */
export function setDefaultReceiptTimeout(seconds: number): void {
  if (seconds <= 0) {
    throw new Error("receipt timeout must be positive");
  }
  receiptTimeoutOverride = Math.trunc(seconds);
}

/**
 * Resolve the default receipt timeout (seconds).
 *
 * Precedence: the {@link setDefaultReceiptTimeout} override, then the
 * `BNBAGENT_RECEIPT_TIMEOUT` env var, then {@link DEFAULT_RECEIPT_TIMEOUT}.
 */
export function getDefaultReceiptTimeout(): number {
  if (receiptTimeoutOverride !== null) {
    return receiptTimeoutOverride;
  }
  const raw = getEnv("BNBAGENT_RECEIPT_TIMEOUT");
  if (raw !== undefined) {
    const parsed = parseIntStrict(raw);
    if (parsed !== null) {
      return parsed;
    }
    console.warn(
      `Ignoring invalid BNBAGENT_RECEIPT_TIMEOUT=${JSON.stringify(raw)}`,
    );
  }
  return DEFAULT_RECEIPT_TIMEOUT;
}

/** Reset both overrides to unset. Test-only. */
export function _resetTxConfigOverrides(): void {
  minGasPriceOverride = null;
  receiptTimeoutOverride = null;
}

/** Parse a strict base-10 integer string to a `bigint`, or `null` if invalid. */
function parseBigIntStrict(raw: string): bigint | null {
  const trimmed = raw.trim();
  if (!/^-?\d+$/.test(trimmed)) {
    return null;
  }
  try {
    return BigInt(trimmed);
  } catch {
    return null;
  }
}

/** Parse a strict base-10 integer string to a `number`, or `null` if invalid. */
function parseIntStrict(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^-?\d+$/.test(trimmed)) {
    return null;
  }
  const n = Number(trimmed);
  return Number.isSafeInteger(n) ? n : null;
}
