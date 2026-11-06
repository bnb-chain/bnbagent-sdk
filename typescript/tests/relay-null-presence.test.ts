import type { PublicClient } from "viem";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";
import { waitForReceiptAndInterpret } from "../src/core/txSender.js";
import { RelaySubmissionUnverifiedError } from "../src/errors.js";

afterEach(() => {
  vi.useRealTimers();
  NonceManager._clearAll();
});
const address = `0x${"11".repeat(20)}` as const;

describe("relay-null-presence", () => {
  it("does not treat null transaction presence as evidence the relay broadcast", async () => {
    vi.useFakeTimers();
    const client = {
      getTransactionReceipt: vi.fn().mockRejectedValue(new Error("not found")),
      getTransaction: vi.fn().mockResolvedValue(null),
    } as unknown as PublicClient;
    const pending = waitForReceiptAndInterpret(
      client,
      `0x${"ab".repeat(32)}`,
      10,
      { requireTransactionSeen: true, unseenAbortSeconds: 1 },
    );
    const checked = expect(pending).rejects.toThrow(
      RelaySubmissionUnverifiedError,
    );
    await vi.advanceTimersByTimeAsync(1001);
    await checked;
  });
});
