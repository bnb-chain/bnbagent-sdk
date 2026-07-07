import {
  type PublicClient,
  encodeFunctionResult,
  getAddress,
  multicall3Abi,
  parseAbi,
} from "viem";
import { describe, expect, it, vi } from "vitest";
import { erc20Abi } from "../src/abis/erc20.js";
import {
  DEFAULT_BATCH_SIZE,
  MULTICALL3_ADDRESS,
  multicallRead,
} from "../src/core/multicall.js";
import { mockPublicClient } from "./helpers/mockTransport.js";

/**
 * Ports python/tests/test_multicall.py's TestSingleBatch, TestMultipleBatches,
 * TestPartialFailure, TestEmptyList, and TestRpcErrorPropagates classes.
 *
 * Most cases stub `client.multicall` directly (a plain object with a
 * `vi.fn` in place of the real method, cast to `PublicClient`) rather than
 * driving it through a real JSON-RPC mock: viem's own `multicall` action
 * swallows a rejected `readContract` call into a per-item `status: "failure"`
 * entry whenever `allowFailure: true` (which this module always passes to
 * viem — see multicall.ts's module docstring), so a transport-level "429"
 * thrown by a custom-transport mock would never actually reach
 * `multicallRead`'s retry wrapper as a rejection. Stubbing `client.multicall`
 * itself exercises exactly what `multicallRead` reacts to. One end-to-end
 * test below drives a real `PublicClient` over `mockPublicClient` with a
 * hand-encoded `aggregate3` response, proving the viem wiring (contracts
 * shape, `multicallAddress`, `batchSize: 0`) is real.
 */

const ADDRESS = getAddress("0x2222222222222222222222222222222222222222");

const ABI = parseAbi(["function getJob(uint256 id) view returns (bytes)"]);

type MulticallStub = {
  multicall: ReturnType<typeof vi.fn>;
};

function stubClient(impl: (params: unknown) => unknown): {
  client: PublicClient;
  stub: MulticallStub;
} {
  const stub: MulticallStub = { multicall: vi.fn(impl) };
  return { client: stub as unknown as PublicClient, stub };
}

/** Build a viem-shaped multicall response: all entries succeed with the given results. */
function successResults(results: unknown[]) {
  return results.map((result) => ({ status: "success" as const, result }));
}

describe("multicallRead", () => {
  describe("single batch", () => {
    it("all calls succeed in one multicall() invocation", async () => {
      const { client, stub } = stubClient((params) => {
        const { contracts } = params as { contracts: unknown[] };
        return successResults(contracts.map((_, i) => `result_${i}`));
      });

      const callArgsList = Array.from({ length: 5 }, (_, i) => [BigInt(i)]);
      const results = await multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList,
      });

      expect(results).toHaveLength(5);
      expect(results.every(([success]) => success)).toBe(true);
      expect(results.map(([, r]) => r)).toEqual([
        "result_0",
        "result_1",
        "result_2",
        "result_3",
        "result_4",
      ]);
      expect(stub.multicall).toHaveBeenCalledTimes(1);
    });
  });

  describe("multiple batches", () => {
    it("250 calls @ batchSize 100 -> 3 multicall() invocations of 100/100/50", async () => {
      const batchSizes: number[] = [];
      const { client, stub } = stubClient((params) => {
        const { contracts } = params as { contracts: unknown[] };
        batchSizes.push(contracts.length);
        return successResults(contracts.map((_, i) => i));
      });

      const callArgsList = Array.from({ length: 250 }, (_, i) => [BigInt(i)]);
      const results = await multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList,
        batchSize: 100,
      });

      expect(results).toHaveLength(250);
      expect(results.every(([success]) => success)).toBe(true);
      expect(stub.multicall).toHaveBeenCalledTimes(3);
      expect(batchSizes).toEqual([100, 100, 50]);
    });

    it("uses DEFAULT_BATCH_SIZE (100) when batchSize is omitted", async () => {
      expect(DEFAULT_BATCH_SIZE).toBe(100);
      const batchSizes: number[] = [];
      const { client } = stubClient((params) => {
        const { contracts } = params as { contracts: unknown[] };
        batchSizes.push(contracts.length);
        return successResults(contracts.map(() => "ok"));
      });

      const callArgsList = Array.from({ length: 150 }, (_, i) => [BigInt(i)]);
      await multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList,
      });

      expect(batchSizes).toEqual([100, 50]);
    });
  });

  describe("partial failure", () => {
    it("failed indices -> [false, null]; others succeed; order preserved", async () => {
      const failedIndices = new Set([1, 3]);
      const { client } = stubClient((params) => {
        const { contracts } = params as { contracts: unknown[] };
        return contracts.map((_, i) =>
          failedIndices.has(i)
            ? { status: "failure" as const, error: new Error("reverted") }
            : { status: "success" as const, result: `result_${i}` },
        );
      });

      const callArgsList = Array.from({ length: 5 }, (_, i) => [BigInt(i)]);
      const results = await multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList,
      });

      expect(results).toEqual([
        [true, "result_0"],
        [false, null],
        [true, "result_2"],
        [false, null],
        [true, "result_4"],
      ]);
    });

    it("throws instead of tolerating a failure when allowFailure is false", async () => {
      const { client } = stubClient(() => [
        { status: "success" as const, result: "ok" },
        { status: "failure" as const, error: new Error("reverted") },
      ]);

      await expect(
        multicallRead(client, {
          address: ADDRESS,
          abi: ABI,
          functionName: "getJob",
          callArgsList: [[0n], [1n]],
          allowFailure: false,
        }),
      ).rejects.toThrow(/allowFailure=false/);
    });
  });

  describe("empty list", () => {
    it("returns [] with zero RPC calls", async () => {
      const { client, stub } = stubClient(() => {
        throw new Error("should not be called");
      });

      const results = await multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList: [],
      });

      expect(results).toEqual([]);
      expect(stub.multicall).not.toHaveBeenCalled();
    });
  });

  describe("unknown function", () => {
    it("throws before any RPC call", async () => {
      const { client, stub } = stubClient(() => {
        throw new Error("should not be called");
      });

      await expect(
        multicallRead(client, {
          address: ADDRESS,
          abi: ABI,
          functionName: "nope",
          callArgsList: [[0n]],
        }),
      ).rejects.toThrow("Function nope not found in ABI");
      expect(stub.multicall).not.toHaveBeenCalled();
    });
  });

  describe("batch exception", () => {
    it("a non-rate-limit error propagates immediately, without retry", async () => {
      const { client, stub } = stubClient(() => {
        throw new Error("connection refused");
      });

      await expect(
        multicallRead(client, {
          address: ADDRESS,
          abi: ABI,
          functionName: "getJob",
          callArgsList: [[0n]],
        }),
      ).rejects.toThrow("connection refused");
      expect(stub.multicall).toHaveBeenCalledTimes(1);
    });

    it("a 429 is retried once (with backoff) and then succeeds", async () => {
      vi.useFakeTimers();
      let attempt = 0;
      const { client, stub } = stubClient(() => {
        attempt++;
        if (attempt === 1) {
          throw new Error("429 Too Many Requests");
        }
        return successResults(["ok"]);
      });

      const promise = multicallRead(client, {
        address: ADDRESS,
        abi: ABI,
        functionName: "getJob",
        callArgsList: [[0n]],
      });
      let results: Array<[boolean, unknown]> | undefined;
      promise.then((r) => {
        results = r;
      });

      await vi.advanceTimersByTimeAsync(0);
      expect(results).toBeUndefined(); // still sleeping through the 1s backoff
      await vi.advanceTimersByTimeAsync(1000);

      expect(results).toEqual([[true, "ok"]]);
      expect(attempt).toBe(2);
      expect(stub.multicall).toHaveBeenCalledTimes(2);
      vi.useRealTimers();
    });
  });

  describe("end-to-end wiring through a real PublicClient", () => {
    it("decodes a single erc20 decimals() call via a hand-encoded aggregate3 response", async () => {
      const tokenAddress = getAddress(
        "0x3333333333333333333333333333333333333333",
      );
      const decimalsReturnData = encodeFunctionResult({
        abi: erc20Abi,
        functionName: "decimals",
        result: 18,
      });
      const aggregate3Response = encodeFunctionResult({
        abi: multicall3Abi,
        functionName: "aggregate3",
        result: [{ success: true, returnData: decimalsReturnData }],
      });

      const mock = mockPublicClient({
        eth_call: (params) => {
          const req = params[0] as { to?: string };
          if (req.to?.toLowerCase() !== MULTICALL3_ADDRESS.toLowerCase()) {
            throw new Error(`unexpected eth_call target: ${req.to}`);
          }
          return aggregate3Response;
        },
      });

      const results = await multicallRead(mock.client, {
        address: tokenAddress,
        abi: erc20Abi,
        functionName: "decimals",
        callArgsList: [[]],
      });

      expect(results).toEqual([[true, 18]]);
      const multicallCalls = mock.calls.filter(
        (c) =>
          c.method === "eth_call" &&
          (c.params[0] as { to?: string }).to?.toLowerCase() ===
            MULTICALL3_ADDRESS.toLowerCase(),
      );
      expect(multicallCalls).toHaveLength(1);
    });
  });
});
