import { describe, expect, it } from "vitest";
import { BNBAgentError } from "../src/errors";
import {
  RateLimitExceeded,
  SlidingWindowLimiter,
} from "../src/utils/rateLimit";

describe("SlidingWindowLimiter", () => {
  it("allows up to the limit", () => {
    const limiter = new SlidingWindowLimiter(3, 60.0);
    for (let i = 0; i < 3; i++) {
      limiter.check("1.2.3.4");
    }
  });

  it("rejects over the limit for the same key", () => {
    const limiter = new SlidingWindowLimiter(2, 60.0);
    limiter.check("1.2.3.4");
    limiter.check("1.2.3.4");
    expect(() => limiter.check("1.2.3.4")).toThrow(RateLimitExceeded);
    expect(() => limiter.check("1.2.3.4")).toThrow("Too many requests");
  });

  it("RateLimitExceeded is not a BNBAgentError", () => {
    const err = new RateLimitExceeded("Too many requests");
    expect(err).not.toBeInstanceOf(BNBAgentError);
    expect(err).toBeInstanceOf(Error);
  });

  it("keys are independent", () => {
    const limiter = new SlidingWindowLimiter(1, 60.0);
    limiter.check("1.2.3.4");
    limiter.check("5.6.7.8"); // different key, fresh budget
    expect(() => limiter.check("1.2.3.4")).toThrow(RateLimitExceeded);
  });

  it("window recovers after expiry using an injected clock", () => {
    let now = 1000.0;
    const limiter = new SlidingWindowLimiter(1, 10.0, undefined, () => now);
    limiter.check("ip");
    expect(() => limiter.check("ip")).toThrow(RateLimitExceeded);

    now += 11.0; // past the 10s window
    limiter.check("ip"); // bucket is pruned, allowed again
  });

  it("does not record a rejected hit", () => {
    let now = 1000.0;
    const limiter = new SlidingWindowLimiter(1, 10.0, undefined, () => now);
    limiter.check("ip"); // recorded at t=1000
    now = 1005.0;
    expect(() => limiter.check("ip")).toThrow(RateLimitExceeded); // rejected, must NOT be recorded

    // If the rejected hit at t=1005 had been recorded, the bucket would
    // still contain it here (1005 > cutoff 1001) and this would throw.
    // Since only the t=1000 hit was ever recorded, it is pruned by the
    // t=1011 cutoff (1011 - 10 = 1001) and the bucket is empty again.
    now = 1011.0;
    limiter.check("ip");
  });

  it("rejects invalid construction args", () => {
    expect(() => new SlidingWindowLimiter(0, 60.0)).toThrow(
      "max_requests must be > 0",
    );
    expect(() => new SlidingWindowLimiter(10, 0)).toThrow(
      "window_seconds must be > 0",
    );
  });

  it("rejects invalid max_keys", () => {
    expect(() => new SlidingWindowLimiter(10, 60.0, 0)).toThrow(
      "max_keys must be > 0",
    );
    expect(() => new SlidingWindowLimiter(10, 60.0, -1)).toThrow(
      "max_keys must be > 0",
    );
  });

  it("exposes constructor params via getters", () => {
    const limiter = new SlidingWindowLimiter(5, 30, 100);
    expect(limiter.maxRequests).toBe(5);
    expect(limiter.windowSeconds).toBe(30);
    expect(limiter.maxKeys).toBe(100);
  });

  it("defaults max_keys to 10_000", () => {
    const limiter = new SlidingWindowLimiter(5, 30);
    expect(limiter.maxKeys).toBe(10_000);
  });

  it("bounds the number of tracked keys by max_keys", () => {
    const limiter = new SlidingWindowLimiter(100, 60.0, 5);
    for (let i = 0; i < 20; i++) {
      limiter.check(`ip-${i}`);
    }
    expect(bucketCount(limiter)).toBe(5);
  });

  it("evicts the oldest key when max_keys is exceeded", () => {
    const limiter = new SlidingWindowLimiter(100, 60.0, 3);
    limiter.check("a");
    limiter.check("b");
    limiter.check("c");
    expect(hasBucket(limiter, "a")).toBe(true);

    limiter.check("d"); // "a" (LRU) should be evicted
    expect(hasBucket(limiter, "a")).toBe(false);
    expect(bucketCount(limiter)).toBe(3);

    // "a" having been evicted, it can accept a new hit (budget reset).
    limiter.check("a");
  });

  it("recent access refreshes LRU position", () => {
    const limiter = new SlidingWindowLimiter(100, 60.0, 3);
    limiter.check("a");
    limiter.check("b");
    limiter.check("a"); // re-access "a": moves to most-recently-used, "b" is now LRU
    limiter.check("c");

    limiter.check("d"); // "b" (LRU) should be evicted, not "a"
    expect(hasBucket(limiter, "b")).toBe(false);
    expect(hasBucket(limiter, "a")).toBe(true);
    expect(bucketCount(limiter)).toBe(3);
  });
});

/** Test-only helper reaching into the limiter's private bucket map. */
function buckets(limiter: SlidingWindowLimiter): Map<string, unknown> {
  return (limiter as unknown as { buckets: Map<string, unknown> }).buckets;
}

function bucketCount(limiter: SlidingWindowLimiter): number {
  return buckets(limiter).size;
}

function hasBucket(limiter: SlidingWindowLimiter, key: string): boolean {
  return buckets(limiter).has(key);
}
