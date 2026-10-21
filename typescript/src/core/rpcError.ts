import {
  ContractFunctionRevertedError,
  ContractFunctionZeroDataError,
  ExecutionRevertedError,
  HttpRequestError,
  RpcError,
  RpcRequestError,
  SocketClosedError,
  TimeoutError,
  WebSocketRequestError,
} from "viem";
import { NetworkError } from "../errors.js";

const TRANSIENT_REASON =
  /\b429\b|too many requests|rate limit|limit exceeded|timeout|timed out|connection|network|\brpc\b|header not found|missing trie node/iu;

/**
 * Whether `error` is a revert (or a call that returned no data) anywhere in
 * its `.cause` chain: deterministic for the same chain state, so a retry
 * cannot get past it.
 */
export function isRevertError(error: unknown): boolean {
  const seen = new Set<unknown>();
  let current = error;
  while (current && typeof current === "object" && !seen.has(current)) {
    seen.add(current);
    if (
      current instanceof ContractFunctionRevertedError ||
      current instanceof ContractFunctionZeroDataError ||
      current instanceof ExecutionRevertedError
    ) {
      return true;
    }
    current = (current as { cause?: unknown }).cause;
  }
  return false;
}

/**
 * Whether `error` is a transport or JSON-RPC failure, which the same request
 * can get past on a retry: viem's HTTP, timeout, socket and RPC errors, a
 * system error code (`ECONNRESET`, …), an SDK {@link NetworkError}, or a raw
 * `{ code }` RPC payload, anywhere in the `.cause` chain; failing those, a
 * node reason naming such trouble. A revert ({@link isRevertError}) is never
 * transient, nor is any other error (e.g. one raised by the content being
 * checked).
 */
export function isTransientRpcError(error: unknown): boolean {
  if (isRevertError(error)) {
    return false;
  }
  const seen = new Set<unknown>();
  let transient = false;
  let current = error;
  while (current && typeof current === "object" && !seen.has(current)) {
    seen.add(current);
    const { code, name, cause } = current as {
      code?: unknown;
      name?: unknown;
      cause?: unknown;
    };
    transient ||=
      current instanceof HttpRequestError ||
      current instanceof TimeoutError ||
      current instanceof RpcRequestError ||
      current instanceof WebSocketRequestError ||
      current instanceof SocketClosedError ||
      current instanceof RpcError ||
      current instanceof NetworkError ||
      typeof code === "number" ||
      (typeof code === "string" && /^(E[A-Z0-9_]+|UND_ERR_\w+)$/u.test(code)) ||
      name === "TimeoutError";
    current = cause;
  }
  return transient || TRANSIENT_REASON.test(rpcErrorReason(error));
}

/** Node reasons only: viem's rendered messages also contain URLs and raw tx bytes. */
export function rpcErrorReason(error: unknown): string {
  const seen = new Set<unknown>();
  let reason = "";
  let current = error;
  while (current && typeof current === "object" && !seen.has(current)) {
    seen.add(current);
    const value = current as {
      details?: unknown;
      message?: unknown;
      cause?: unknown;
    };
    const text =
      typeof value.details === "string" ? value.details : value.message;
    if (typeof text === "string") {
      reason = text.split(/\n\s*\n|\n(?:URL|Request body|Details):/u)[0] ?? "";
    }
    current = value.cause;
  }
  return reason.toLowerCase();
}

export function isAmbiguousBroadcast(error: unknown): boolean {
  if (isRateLimited(error)) return true;
  const reason = rpcErrorReason(error);
  return /already known|known transaction|replacement transaction underpriced|\b429\b|too many requests|timeout|timed out|connection|network/iu.test(
    reason,
  );
}

export function isRateLimited(error: unknown): boolean {
  const seen = new Set<unknown>();
  let current = error;
  while (current && typeof current === "object" && !seen.has(current)) {
    seen.add(current);
    const value = current as { status?: number; cause?: unknown };
    if (value.status === 429) return true;
    current = value.cause;
  }
  return /\b429\b|too many requests/iu.test(rpcErrorReason(error));
}
