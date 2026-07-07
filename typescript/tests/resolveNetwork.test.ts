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
