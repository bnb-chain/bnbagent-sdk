/**
 * `isTransientRpcError` / `isRevertError` (src/core/rpcError.ts): transport
 * and JSON-RPC failures are transient whatever the RPC URL looks like, and a
 * revert is deterministic (audit M10).
 */

import {
  ContractFunctionRevertedError,
  ExecutionRevertedError,
  HttpRequestError,
  InvalidAddressError,
  RpcRequestError,
  TimeoutError,
} from "viem";
import { describe, expect, it } from "vitest";
import { isRevertError, isTransientRpcError } from "../src/core/rpcError.js";
import { NetworkError, RpcRangeLimitError } from "../src/errors.js";

// No "rpc", "network" or "timeout" in it: the old keyword match saw nothing.
const URL = "https://bsc-dataseed.binance.org/";

describe("isTransientRpcError", () => {
  it.each<[string, () => unknown]>([
    [
      "viem HttpRequestError (HTTP 502)",
      () => new HttpRequestError({ url: URL, status: 502, body: {} }),
    ],
    ["viem TimeoutError", () => new TimeoutError({ url: URL, body: {} })],
    [
      "a JSON-RPC error (header not found)",
      () =>
        new RpcRequestError({
          url: URL,
          body: {},
          error: { code: -32000, message: "header not found" },
        }),
    ],
    [
      "fetch failed with ECONNRESET",
      () =>
        Object.assign(new TypeError("fetch failed"), {
          cause: Object.assign(new Error("read ECONNRESET"), {
            code: "ECONNRESET",
          }),
        }),
    ],
    [
      "an undici socket error",
      () =>
        Object.assign(new Error("other side closed"), {
          code: "UND_ERR_SOCKET",
        }),
    ],
    [
      "an AbortSignal.timeout abort",
      () => new DOMException("The operation timed out.", "TimeoutError"),
    ],
    ["an SDK NetworkError", () => new NetworkError("no route")],
    [
      "an RpcRangeLimitError",
      () => new RpcRangeLimitError("-32005 limit exceeded"),
    ],
    [
      "a raw { code } payload",
      () => ({ code: -32005, message: "limit exceeded" }),
    ],
    [
      "a transport error wrapped by a contract call",
      () =>
        new Error("getJobFundedBlock failed", {
          cause: new HttpRequestError({ url: URL, status: 503, body: {} }),
        }),
    ],
  ])("%s is transient", (_label, make) => {
    expect(isTransientRpcError(make())).toBe(true);
  });

  it.each<[string, () => unknown]>([
    [
      "a decoded revert",
      () =>
        new ContractFunctionRevertedError({
          abi: [],
          functionName: "isValidSignature",
          message: "reverted",
        }),
    ],
    [
      "a node revert wrapping its JSON-RPC error",
      () =>
        new ExecutionRevertedError({
          cause: new RpcRequestError({
            url: URL,
            body: {},
            error: { code: 3, message: "execution reverted" },
          }),
        }),
    ],
    [
      "a TypeError on malformed content",
      () =>
        new TypeError("Cannot read properties of undefined (reading 'terms')"),
    ],
    [
      "an invalid address in content",
      () => new InvalidAddressError({ address: "0x12" }),
    ],
    [
      "a plain content error",
      () => new Error("signature has the wrong length"),
    ],
  ])("%s is not transient", (_label, make) => {
    expect(isTransientRpcError(make())).toBe(false);
  });
});

describe("isRevertError", () => {
  it("finds a revert anywhere in the cause chain", () => {
    expect(
      isRevertError(
        new Error("tokenDecimals failed", {
          cause: new ExecutionRevertedError({ message: "execution reverted" }),
        }),
      ),
    ).toBe(true);
    expect(
      isRevertError(
        new ContractFunctionRevertedError({
          abi: [],
          functionName: "decimals",
          message: "reverted",
        }),
      ),
    ).toBe(true);
  });

  it("is false for transport, RPC and other errors", () => {
    expect(
      isRevertError(new HttpRequestError({ url: URL, status: 502, body: {} })),
    ).toBe(false);
    expect(isRevertError(new TypeError("boom"))).toBe(false);
  });
});
