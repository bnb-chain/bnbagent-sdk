/**
 * A programmable viem `custom()` transport for `ContractBase` tests.
 *
 * Intercepts at the JSON-RPC layer (rather than mocking individual viem
 * action functions) so `ContractBase` can call ordinary `PublicClient`
 * actions (`estimateGas`, `call`, `sendRawTransaction`,
 * `getTransactionReceipt`, `getGasPrice`, `getChainId`, `getLogs`, ...) and
 * have every one of them uniformly captured and stubbed.
 */

import {
  type PublicClient,
  type Transport,
  createPublicClient,
  custom,
} from "viem";

/** A single JSON-RPC method handler. Returns the *unwrapped* RPC result. */
export type MockHandler = (params: readonly unknown[]) => unknown;

export type MockHandlers = Record<string, MockHandler>;

export interface RecordedCall {
  method: string;
  params: readonly unknown[];
}

/** `sendRawTransaction`'s canonical mock result: `0x` + `"de"` x32. */
export const FAKE_TX_HASH: `0x${string}` = `0x${"de".repeat(32)}`;

/** `eth_chainId`'s default mock result — deliberately not a real BNB Chain id. */
export const FAKE_CHAIN_ID = 12345;

function toHex(value: number | bigint): `0x${string}` {
  return `0x${value.toString(16)}`;
}

/**
 * The default handler set every `ContractBase` write/read path needs to
 * complete a "happy path" attempt: a resolvable chain id, a positive gas
 * price, a clean gas estimate, a passing preflight `eth_call`, a nonce seed,
 * a successful broadcast, and a mined-and-successful receipt for that
 * broadcast's hash.
 */
export function defaultMockHandlers(): MockHandlers {
  return {
    eth_chainId: () => toHex(FAKE_CHAIN_ID),
    eth_gasPrice: () => toHex(1_000_000_000n), // 1 Gwei
    eth_blockNumber: () => toHex(1000n),
    eth_estimateGas: () => toHex(100_000n),
    eth_call: () => "0x",
    eth_getTransactionCount: () => toHex(0),
    eth_sendRawTransaction: () => FAKE_TX_HASH,
    eth_getTransactionReceipt: () => ({
      status: "0x1",
      blockNumber: "0x1",
      blockHash: `0x${"aa".repeat(32)}`,
      transactionHash: FAKE_TX_HASH,
      transactionIndex: "0x0",
      from: `0x${"11".repeat(20)}`,
      to: `0x${"22".repeat(20)}`,
      cumulativeGasUsed: "0x1e8480",
      gasUsed: "0x186a0",
      contractAddress: null,
      logs: [],
      logsBloom: `0x${"0".repeat(512)}`,
      effectiveGasPrice: "0x3b9aca00",
    }),
    eth_getLogs: () => [],
  };
}

/**
 * A programmable viem `custom()` transport. Each JSON-RPC method not present
 * in `handlers` throws.
 *
 * `retryCount: 0` disables viem's own transport-level retry — `buildRequest`
 * (viem's internal request wrapper) treats a plain `Error` without a numeric
 * RPC `.code` as retryable by default (see `shouldRetry`'s fallback branch),
 * which would silently multiply every mock rejection up to 4x before it ever
 * reaches `ContractBase`. Disabling it here makes the mock a faithful
 * "exactly what the handler did" stand-in, so call-count assertions
 * (`toHaveBeenCalledTimes`, `calls.length`) measure `ContractBase`'s own
 * retry loop and nothing else.
 */
export function mockTransport(handlers: MockHandlers): Transport {
  return custom(
    {
      async request({
        method,
        params,
      }: {
        method: string;
        params?: unknown[];
      }) {
        const handler = handlers[method];
        if (!handler) {
          throw new Error(
            `mockTransport: no handler registered for "${method}"`,
          );
        }
        return handler(params ?? []);
      },
    },
    { retryCount: 0 },
  );
}

export interface MockPublicClient {
  client: PublicClient;
  /**
   * Live handler map. Reassign an entry (e.g.
   * `handlers.eth_sendRawTransaction = () => { throw new Error(...) }`) to
   * override behaviour mid-test — the transport reads this same object on
   * every call, so mutations apply immediately without rebuilding the client.
   */
  handlers: MockHandlers;
  /** Every RPC call made through this client so far, in call order. */
  calls: RecordedCall[];
}

/**
 * A `PublicClient` backed by {@link mockTransport}, seeded with
 * {@link defaultMockHandlers} and instrumented to record every call.
 *
 * `overrides` replaces specific default handlers up front; `result.handlers`
 * can also be mutated after construction for per-attempt behaviour (e.g. a
 * handler that throws on its first invocation and succeeds thereafter).
 */
export function mockPublicClient(
  overrides: Partial<MockHandlers> = {},
): MockPublicClient {
  // Plain object-spread of a `Partial<MockHandlers>` widens every value to
  // `MockHandler | undefined`, which doesn't satisfy `MockHandlers`; copy
  // only the defined overrides instead.
  const handlers: MockHandlers = { ...defaultMockHandlers() };
  for (const [method, handler] of Object.entries(overrides)) {
    if (handler) {
      handlers[method] = handler;
    }
  }
  const calls: RecordedCall[] = [];
  const transport = custom(
    {
      async request({
        method,
        params,
      }: {
        method: string;
        params?: unknown[];
      }) {
        const recordedParams = params ?? [];
        calls.push({ method, params: recordedParams });
        const handler = handlers[method];
        if (!handler) {
          throw new Error(
            `mockPublicClient: no handler registered for "${method}"`,
          );
        }
        return handler(recordedParams);
      },
    },
    { retryCount: 0 },
  );
  const client = createPublicClient({ transport }) as PublicClient;
  return { client, handlers, calls };
}
