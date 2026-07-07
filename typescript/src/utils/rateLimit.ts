/**
 * In-memory sliding-window rate limiter for public agent endpoints.
 *
 * A public `/negotiate`-style endpoint signs negotiation hashes with the
 * provider's wallet on every accepted request. Without throttling, any caller
 * can drive arbitrary signing work and accumulate signed quotes; this limiter
 * caps the per-IP rate to bound that abuse without breaking marketplace
 * discovery.
 *
 * The limiter is transport-agnostic: it throws {@link RateLimitExceeded},
 * and the serving layer (HTTP route, MCP tool, A2A handler, ...) converts
 * that into its own protocol's rejection (e.g. HTTP 429).
 *
 * Trade-offs (intentional, single-replica scope):
 * - In-memory state: counters are not shared across replicas. Multi-replica
 *   deployments effectively get N x the per-replica limit; that is acceptable
 *   while horizontal scaling itself raises the cost of an attack.
 * - Memory growth is hard-capped by `maxKeys`; least-recently-used keys
 *   are evicted once the cap is exceeded. This prevents an IPv6 address-cycling
 *   attack from growing the limiter's memory without bound.
 */

/**
 * Thrown by {@link SlidingWindowLimiter.check} when a key's window is full.
 *
 * Transport-agnostic on purpose — the serving layer maps it to its own
 * rejection (HTTP 429, MCP error, ...). Deliberately NOT a {@link
 * BNBAgentError}: this is a generic utility error outside the SDK's
 * protocol error hierarchy.
 */
export class RateLimitExceeded extends Error {
  constructor(message?: string) {
    super(message);
    this.name = "RateLimitExceeded";
  }
}

const DEFAULT_MAX_KEYS = 10_000;

/**
 * Per-key sliding-window rate limiter with LRU key eviction.
 *
 * Allows up to `maxRequests` events per `windowSeconds` for any given key.
 * Throws {@link RateLimitExceeded} once the budget is exhausted. The number
 * of tracked keys is hard-capped at `maxKeys`; once exceeded, the
 * least-recently-used key is evicted to reclaim memory.
 */
export class SlidingWindowLimiter {
  private readonly maxRequestsValue: number;
  private readonly windowSecondsValue: number;
  private readonly maxKeysValue: number;
  private readonly now: () => number;
  private readonly buckets = new Map<string, number[]>();

  constructor(
    maxRequests: number,
    windowSeconds: number,
    maxKeys: number = DEFAULT_MAX_KEYS,
    now: () => number = () => performance.now() / 1000,
  ) {
    if (maxRequests <= 0) {
      throw new Error("max_requests must be > 0");
    }
    if (windowSeconds <= 0) {
      throw new Error("window_seconds must be > 0");
    }
    if (maxKeys <= 0) {
      throw new Error("max_keys must be > 0");
    }
    this.maxRequestsValue = maxRequests;
    this.windowSecondsValue = windowSeconds;
    this.maxKeysValue = maxKeys;
    this.now = now;
  }

  get maxRequests(): number {
    return this.maxRequestsValue;
  }

  get windowSeconds(): number {
    return this.windowSecondsValue;
  }

  get maxKeys(): number {
    return this.maxKeysValue;
  }

  /** Record a hit for `key`, or throw {@link RateLimitExceeded} if its window is full. */
  check(key: string): void {
    const now = this.now();

    let bucket = this.buckets.get(key);
    if (bucket !== undefined) {
      // Touch: move to the end of the Map's insertion order to mark
      // as most-recently used.
      this.buckets.delete(key);
      this.buckets.set(key, bucket);
    } else {
      bucket = [];
      this.buckets.set(key, bucket);
      // Evict the least-recently-used key (the Map's first entry) if the
      // cap is exceeded.
      if (this.buckets.size > this.maxKeysValue) {
        const oldestKey = this.buckets.keys().next().value;
        if (oldestKey !== undefined) {
          this.buckets.delete(oldestKey);
        }
      }
    }

    const cutoff = now - this.windowSecondsValue;
    while (bucket.length > 0 && bucket[0] <= cutoff) {
      bucket.shift();
    }

    if (bucket.length >= this.maxRequestsValue) {
      throw new RateLimitExceeded("Too many requests");
    }
    bucket.push(now);
  }
}
