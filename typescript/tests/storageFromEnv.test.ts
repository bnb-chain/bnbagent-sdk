/**
 * Tests for provider-level `fromEnv()` static methods.
 *
 * Ports `python/tests/test_storage_from_env.py`.
 */

import { existsSync, rmSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { IPFSStorageProvider } from "../src/storage/ipfsStorageProvider";
import { LocalStorageProvider } from "../src/storage/localStorageProvider";

/** Reach into a provider's "private" (TS-only, not `#`) fields for assertions. */
function internals<T extends object>(provider: T): Record<string, unknown> {
  return provider as unknown as Record<string, unknown>;
}

/** `delete process.env[key]` via an indirected key so biome's noDelete rule doesn't rewrite it to an `= undefined` assignment (which sets the string `"undefined"`, not "unset"). */
function unsetEnv(key: string): void {
  delete process.env[key];
}

const ENV_KEYS = [
  "STORAGE_LOCAL_PATH",
  "STORAGE_API_KEY",
  "STORAGE_API_URL",
  "STORAGE_GATEWAY_URL",
] as const;

let saved: Record<string, string | undefined>;

beforeEach(() => {
  saved = {};
  for (const key of ENV_KEYS) saved[key] = process.env[key];
});

afterEach(() => {
  for (const key of ENV_KEYS) {
    if (saved[key] === undefined) delete process.env[key];
    else process.env[key] = saved[key];
  }
});

describe("LocalStorageProvider.fromEnv", () => {
  afterEach(() => {
    // fromEnv()'s default path creates ./.agent-data as a side effect
    // (matching the Python test, which does the same in its CWD).
    rmSync(".agent-data", { recursive: true, force: true });
  });

  it("defaults to .agent-data", () => {
    unsetEnv("STORAGE_LOCAL_PATH");
    const provider = LocalStorageProvider.fromEnv();
    expect(internals(provider).base).toBe(".agent-data");
    expect(existsSync(".agent-data")).toBe(true);
  });

  it("respects STORAGE_LOCAL_PATH", () => {
    process.env.STORAGE_LOCAL_PATH = "/tmp/bnbagent-storage-from-env-custom";
    const provider = LocalStorageProvider.fromEnv();
    expect(internals(provider).base).toBe(
      "/tmp/bnbagent-storage-from-env-custom",
    );
    rmSync("/tmp/bnbagent-storage-from-env-custom", {
      recursive: true,
      force: true,
    });
  });

  it("returns a LocalStorageProvider instance", () => {
    unsetEnv("STORAGE_LOCAL_PATH");
    expect(LocalStorageProvider.fromEnv()).toBeInstanceOf(LocalStorageProvider);
  });
});

describe("IPFSStorageProvider.fromEnv", () => {
  it("requires STORAGE_API_KEY", () => {
    unsetEnv("STORAGE_API_KEY");
    expect(() => IPFSStorageProvider.fromEnv()).toThrow(/STORAGE_API_KEY/);
  });

  it("stores the API key", () => {
    process.env.STORAGE_API_KEY = "test-jwt";
    unsetEnv("STORAGE_API_URL");
    unsetEnv("STORAGE_GATEWAY_URL");
    const provider = IPFSStorageProvider.fromEnv();
    expect(provider).toBeInstanceOf(IPFSStorageProvider);
    expect(internals(provider).apiKey).toBe("test-jwt");
  });

  it("defaults to the Pinata pinning + gateway URLs", () => {
    process.env.STORAGE_API_KEY = "test-jwt";
    unsetEnv("STORAGE_API_URL");
    unsetEnv("STORAGE_GATEWAY_URL");
    const provider = IPFSStorageProvider.fromEnv();
    expect(internals(provider).pinningUrl as string).toContain("pinata.cloud");
    expect(internals(provider).gateway as string).toContain("pinata.cloud");
  });

  it("respects STORAGE_API_URL", () => {
    process.env.STORAGE_API_KEY = "test-jwt";
    process.env.STORAGE_API_URL = "https://custom.pin.io/pinJSON";
    unsetEnv("STORAGE_GATEWAY_URL");
    const provider = IPFSStorageProvider.fromEnv();
    expect(internals(provider).pinningUrl).toBe(
      "https://custom.pin.io/pinJSON",
    );
  });

  it("respects STORAGE_GATEWAY_URL and strips a trailing slash", () => {
    process.env.STORAGE_API_KEY = "test-jwt";
    unsetEnv("STORAGE_API_URL");
    process.env.STORAGE_GATEWAY_URL = "https://custom.gateway.io/ipfs/";
    const provider = IPFSStorageProvider.fromEnv();
    expect(internals(provider).gateway).toBe("https://custom.gateway.io/ipfs");
  });
});
