import { afterEach, describe, expect, it, vi } from "vitest";
import { NETWORKS, type NetworkConfig, resolveNetwork } from "../src/config.js";

/**
 * Ports python/tests/test_resolve_network.py.
 *
 * Precedence: RPC_URL_<NETWORK> (per-network) > RPC_URL (global) > preset.
 * A NetworkConfig object passed directly is returned as-is (no env applied).
 */

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("resolveNetwork precedence", () => {
  it("uses the preset default when no env is set", () => {
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.rpcUrl).toBe(NETWORKS["bsc-testnet"].rpcUrl);
  });

  it("global RPC_URL overrides the preset", () => {
    vi.stubEnv("RPC_URL", "https://global.example.com");
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.rpcUrl).toBe("https://global.example.com");
  });

  it("per-network override wins over the global override", () => {
    vi.stubEnv("RPC_URL", "https://global.example.com");
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://testnet.example.com");
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.rpcUrl).toBe("https://testnet.example.com");
  });

  it("per-network override is scoped to that network only", () => {
    // A testnet pin must not leak onto mainnet resolution.
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://testnet.example.com");
    const testnet = resolveNetwork("bsc-testnet");
    const mainnet = resolveNetwork("bsc-mainnet");
    expect(testnet.rpcUrl).toBe("https://testnet.example.com");
    expect(mainnet.rpcUrl).toBe(NETWORKS["bsc-mainnet"].rpcUrl);
  });

  it("both networks can be pinned simultaneously", () => {
    // One process can resolve BOTH networks to distinct pinned nodes.
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://t.example.com");
    vi.stubEnv("RPC_URL_BSC_MAINNET", "https://m.example.com");
    expect(resolveNetwork("bsc-testnet").rpcUrl).toBe("https://t.example.com");
    expect(resolveNetwork("bsc-mainnet").rpcUrl).toBe("https://m.example.com");
  });

  it("localhost override disables the paymaster", () => {
    vi.stubEnv("RPC_URL_BSC_TESTNET", "http://localhost:8545");
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.usePaymaster).toBe(false);
  });

  it("loopback overrides disable the paymaster regardless of scheme/host", () => {
    for (const url of [
      "https://localhost:8545",
      "http://127.0.0.1:8545",
      "http://[::1]:8545",
      "ws://localhost:8545",
    ]) {
      vi.stubEnv("RPC_URL_BSC_TESTNET", url);
      expect(resolveNetwork("bsc-testnet").usePaymaster).toBe(false);
    }
  });

  it("a remote RPC override inherits the preset's usePaymaster", () => {
    // Regression for BUG-023: a non-localhost override must NOT force the
    // paymaster on — it inherits whatever the preset declared.
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://bsc-testnet.publicnode.com");
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.usePaymaster).toBe(NETWORKS["bsc-testnet"].usePaymaster);
    expect(nc.rpcUrl).toBe("https://bsc-testnet.publicnode.com");
  });

  it("both presets stay sponsored by default (the paymaster IS the product)", () => {
    // Product decision (BUG-022, reversed once and reverted): gasless-by-
    // default is the point of the paymaster — 0-tBNB onboarding. Flaky-relay
    // protection lives in the executor's self-pay fallback, relay broadcast
    // verification, and BNBAGENT_USE_PAYMASTER=0 — never in this default.
    expect(NETWORKS["bsc-testnet"].usePaymaster).toBe(true);
    expect(NETWORKS["bsc-testnet"].paymasterUrl).toBeTruthy();
    expect(NETWORKS["bsc-mainnet"].usePaymaster).toBe(true);
    expect(NETWORKS["bsc-mainnet"].paymasterUrl).toBeTruthy();
  });

  it("BNBAGENT_USE_PAYMASTER=0 disables the paymaster with no RPC override", () => {
    vi.stubEnv("BNBAGENT_USE_PAYMASTER", "0");
    expect(resolveNetwork("bsc-testnet").usePaymaster).toBe(false);
  });

  it("BNBAGENT_USE_PAYMASTER=1 beats the localhost inference", () => {
    vi.stubEnv("RPC_URL_BSC_TESTNET", "http://localhost:8545");
    vi.stubEnv("BNBAGENT_USE_PAYMASTER", "1");
    expect(resolveNetwork("bsc-testnet").usePaymaster).toBe(true);
  });

  it("BNBAGENT_USE_PAYMASTER=0 wins even under a remote override", () => {
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://bsc-testnet.publicnode.com");
    vi.stubEnv("BNBAGENT_USE_PAYMASTER", "0");
    expect(resolveNetwork("bsc-testnet").usePaymaster).toBe(false);
  });

  it("an invalid BNBAGENT_USE_PAYMASTER is ignored (inherits preset)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("BNBAGENT_USE_PAYMASTER", "yes");
    expect(resolveNetwork("bsc-testnet").usePaymaster).toBe(
      NETWORKS["bsc-testnet"].usePaymaster,
    );
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  it("BNBAGENT_USE_PAYMASTER is ignored for an explicit NetworkConfig object", () => {
    vi.stubEnv("BNBAGENT_USE_PAYMASTER", "1");
    const explicit: NetworkConfig = {
      name: "bsc-testnet",
      chainId: 97,
      rpcUrl: "https://mine.example.com",
      usePaymaster: false,
      registryContract: "",
      commerceContract: "",
      routerContract: "",
      policyContract: "",
    };
    const nc = resolveNetwork(explicit);
    expect(nc).toBe(explicit);
    expect(nc.usePaymaster).toBe(false);
  });

  it("preserves chain metadata under an override", () => {
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://testnet.example.com");
    const nc = resolveNetwork("bsc-testnet");
    const preset = NETWORKS["bsc-testnet"];
    expect(nc.chainId).toBe(preset.chainId);
    expect(nc.commerceContract).toBe(preset.commerceContract);
    expect(nc.registryContract).toBe(preset.registryContract);
    expect(nc.routerContract).toBe(preset.routerContract);
    expect(nc.policyContract).toBe(preset.policyContract);
  });

  it("a NetworkConfig object is returned identity-same and ignores env", () => {
    vi.stubEnv("RPC_URL", "https://global.example.com");
    vi.stubEnv("RPC_URL_BSC_TESTNET", "https://testnet.example.com");
    const explicit: NetworkConfig = {
      name: "bsc-testnet",
      chainId: 97,
      rpcUrl: "https://mine.example.com",
      usePaymaster: false,
      registryContract: "",
      commerceContract: "",
      routerContract: "",
      policyContract: "",
    };
    const nc = resolveNetwork(explicit);
    expect(nc).toBe(explicit);
    expect(nc.rpcUrl).toBe("https://mine.example.com");
  });

  it("throws on an unknown network name", () => {
    expect(() => resolveNetwork("opbnb")).toThrow("Unknown network: opbnb");
  });

  it("treats an empty-string env override as unset", () => {
    vi.stubEnv("RPC_URL", "");
    vi.stubEnv("RPC_URL_BSC_TESTNET", "");
    const nc = resolveNetwork("bsc-testnet");
    expect(nc.rpcUrl).toBe(NETWORKS["bsc-testnet"].rpcUrl);
  });

  it("defaults to bsc-testnet when no argument is given", () => {
    const nc = resolveNetwork();
    expect(nc).toBe(NETWORKS["bsc-testnet"]);
  });
});
