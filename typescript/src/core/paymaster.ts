/**
 * MegaFuel paymaster client for ERC-4337 gas sponsorship.
 *
 * Talks JSON-RPC 2.0 to a paymaster service (e.g. BSC MegaFuel) over
 * `fetch`, mirroring `python/bnbagent/core/paymaster.py`; see that module
 * and its tests (`python/tests/test_paymaster.py`) for the authoritative
 * semantics this file ports.
 */

import { bytesToHex, getAddress, numberToHex } from "viem";

const RPC_TIMEOUT_MS = 30_000;

/** Shape of the transaction fields `isSponsorable` inspects. */
export interface SponsorableTx {
  to?: string;
  from?: string;
  value?: bigint | number | string | Uint8Array;
  data?: string | Uint8Array;
  gas?: bigint | number | string | Uint8Array;
}

interface JsonRpcErrorPayload {
  message?: string;
  code?: number;
  data?: unknown;
}

interface JsonRpcResponse {
  result?: unknown;
  error?: JsonRpcErrorPayload;
}

/**
 * Convert a value to a `0x`-prefixed hex string.
 *
 * - `bigint`/`number` are hex-encoded directly.
 * - `Uint8Array` is hex-encoded byte-for-byte; an empty array yields
 *   `defaultValue`.
 * - A string already starting with `0x` passes through unchanged; a
 *   non-empty string without the prefix gets one added; an empty string
 *   yields `defaultValue`.
 * - `null`/`undefined` yield `defaultValue`.
 */
export function toHex(
  value: bigint | number | string | Uint8Array | null | undefined,
  defaultValue = "0x0",
): string {
  if (value === null || value === undefined) {
    return defaultValue;
  }
  if (typeof value === "bigint" || typeof value === "number") {
    return numberToHex(value);
  }
  if (value instanceof Uint8Array) {
    return value.length > 0 ? bytesToHex(value) : defaultValue;
  }
  if (typeof value === "string") {
    if (value.startsWith("0x")) {
      return value;
    }
    return value.length > 0 ? `0x${value}` : defaultValue;
  }
  return defaultValue;
}

/**
 * Convert an address to its EIP-55 checksummed hex form.
 *
 * Falsy input or an address viem rejects as invalid falls back to
 * `defaultValue` (logged as a warning), rather than throwing.
 */
export function toAddressHex(
  address: string | null | undefined,
  defaultValue = "0x0",
): string {
  if (!address) {
    return defaultValue;
  }
  try {
    return getAddress(address);
  } catch (error) {
    console.warn(
      `Invalid address '${address}', using default '${defaultValue}': ${error}`,
    );
    return defaultValue;
  }
}

/**
 * Client for a MegaFuel-style paymaster JSON-RPC service.
 *
 * Handles communication with paymaster services to sponsor gas fees for
 * transactions (ERC-4337 account abstraction).
 */
export class Paymaster {
  private readonly paymasterUrl: string;
  private readonly debug: boolean;

  /**
   * @param paymasterUrl - URL of the paymaster service.
   * @param debug - Enable verbose debug logging (default: false).
   */
  constructor(paymasterUrl: string, debug = false) {
    this.paymasterUrl = paymasterUrl;
    this.debug = debug;
    if (this.debug) {
      console.debug(`Initialized Paymaster with URL: ${paymasterUrl}`);
    }
  }

  /**
   * POST a JSON-RPC 2.0 request to the paymaster service.
   *
   * Throws on a non-2xx HTTP response, on a network/transport error (the
   * underlying `fetch` rejection propagates unchanged), and when the
   * response body carries a JSON-RPC `error` object.
   */
  private async makeRpcRequest(
    method: string,
    params: unknown[],
    options?: { requestId?: number; headers?: Record<string, string> },
  ): Promise<JsonRpcResponse> {
    const requestId = options?.requestId ?? 1;
    const payload = {
      jsonrpc: "2.0",
      id: requestId,
      method,
      params,
    };

    const mergedHeaders: Record<string, string> = {
      ...(options?.headers ?? {}),
    };
    if (!("Content-Type" in mergedHeaders)) {
      mergedHeaders["Content-Type"] = "application/json";
    }

    if (this.debug) {
      console.debug(`Making RPC request: ${method} to ${this.paymasterUrl}`);
    }

    let response: Response;
    try {
      response = await fetch(this.paymasterUrl, {
        method: "POST",
        headers: mergedHeaders,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(RPC_TIMEOUT_MS),
      });
    } catch (error) {
      console.error(`Failed to make RPC request: ${error}`);
      throw error;
    }

    if (!response.ok) {
      throw new Error(
        `HTTP error ${response.status}${response.statusText ? `: ${response.statusText}` : ""}`,
      );
    }

    const result = (await response.json()) as JsonRpcResponse;

    if (result.error) {
      const errorMsg = result.error.message ?? "Unknown error";
      const errorCode = result.error.code ?? -1;
      console.error(`RPC error [${errorCode}]: ${errorMsg}`);
      if (result.error.data) {
        console.error(`Error data: ${JSON.stringify(result.error.data)}`);
      }
      throw new Error(`RPC error [${errorCode}]: ${errorMsg}`);
    }

    return result;
  }

  /**
   * Get the transaction count (nonce) for an address.
   *
   * @param address - Ethereum address (0x prefix optional; checksummed
   * before sending).
   * @param block - Block number or tag (default: "latest").
   */
  async ethGetTransactionCount(
    address: string,
    block = "latest",
  ): Promise<number> {
    const prefixed = address.startsWith("0x") ? address : `0x${address}`;
    const checksummed = getAddress(prefixed);

    const result = await this.makeRpcRequest("eth_getTransactionCount", [
      checksummed,
      block,
    ]);

    const nonceHex = result.result;
    if (nonceHex === undefined || nonceHex === null) {
      throw new Error(
        "Failed to get transaction count: missing 'result' field",
      );
    }

    const nonce = Number.parseInt(nonceHex as string, 16);
    if (this.debug) {
      console.debug(`Transaction count for ${checksummed}: ${nonce}`);
    }
    return nonce;
  }

  /**
   * Send a signed raw transaction to the paymaster service.
   *
   * @param signedTransaction - Signed transaction hex string (0x prefix
   * optional).
   * @param txOptions - Extra values forwarded as HTTP headers on the
   * request (e.g. a sponsorship API key).
   * @returns The transaction hash.
   */
  async ethSendRawTransaction(
    signedTransaction: string,
    txOptions?: Record<string, string>,
  ): Promise<string> {
    const prefixed = signedTransaction.startsWith("0x")
      ? signedTransaction
      : `0x${signedTransaction}`;

    const result = await this.makeRpcRequest(
      "eth_sendRawTransaction",
      [prefixed],
      { headers: txOptions },
    );

    const txHash = result.result;
    if (txHash === undefined || txHash === null) {
      throw new Error("Failed to send raw transaction: missing 'result' field");
    }
    return txHash as string;
  }

  /**
   * Check whether a transaction is sponsorable by the paymaster.
   *
   * Unlike the other RPC methods, a missing `result` field is not an
   * error here — it is logged and treated as "not sponsorable" (`false`).
   * A JSON-RPC `error` object still throws (via `makeRpcRequest`).
   */
  async isSponsorable(tx: SponsorableTx): Promise<boolean> {
    const toHexAddr = toAddressHex(tx.to);
    const fromHexAddr = toAddressHex(tx.from);
    const valueHex = toHex(tx.value ?? 0);
    const dataHex = toHex(tx.data ?? "");
    const gasHex = toHex(tx.gas ?? 0);

    if (this.debug) {
      console.debug(
        `Checking if transaction is sponsorable: ${toHexAddr}, ${fromHexAddr}, ${valueHex}, ${dataHex}, ${gasHex}`,
      );
    }

    const result = await this.makeRpcRequest("pm_isSponsorable", [
      {
        to: toHexAddr,
        from: fromHexAddr,
        value: valueHex,
        data: dataHex,
        gas: gasHex,
      },
    ]);

    const sponsorship = result.result as { sponsorable?: boolean } | undefined;
    if (sponsorship === undefined || sponsorship === null) {
      console.error("Invalid response: missing 'result' field");
      return false;
    }
    return sponsorship.sponsorable ?? false;
  }
}
