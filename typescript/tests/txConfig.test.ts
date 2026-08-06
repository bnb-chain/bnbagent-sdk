import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_RECEIPT_TIMEOUT,
  DEFAULT_RELAY_UNSEEN_TIMEOUT,
  MIN_GAS_PRICE_WEI,
  _resetTxConfigOverrides,
  getDefaultReceiptTimeout,
  getRelayUnseenTimeout,
  minGasPriceWei,
  setDefaultReceiptTimeout,
  setMinGasPriceWei,
  setRelayUnseenTimeout,
} from "../src/core/txConfig.js";
import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
} from "../src/networks/addresses.js";

/**
 * Ports python/tests/test_tx_config.py's TestMinGasPriceFloor and
 * TestReceiptTimeout classes.
 *
 * Precedence for both knobs: setter > env > per-chain/default.
 */

const ONE_GWEI = 1_000_000_000n;
const TENTH_GWEI = 100_000_000n;

beforeEach(() => {
  _resetTxConfigOverrides();
});

afterEach(() => {
  vi.unstubAllEnvs();
  _resetTxConfigOverrides();
});

describe("minGasPriceWei", () => {
  it("mainnet floor is a tenth of a gwei", () => {
    expect(minGasPriceWei(BSC_MAINNET_CHAIN_ID)).toBe(TENTH_GWEI);
  });

  it("testnet floor is one gwei", () => {
    expect(minGasPriceWei(BSC_TESTNET_CHAIN_ID)).toBe(ONE_GWEI);
  });

  it("unknown chain falls back to the default", () => {
    expect(minGasPriceWei(12345)).toBe(MIN_GAS_PRICE_WEI);
    expect(MIN_GAS_PRICE_WEI).toBe(TENTH_GWEI);
  });

  it("env overrides the per-chain default, globally", () => {
    vi.stubEnv("BNBAGENT_MIN_GAS_PRICE_WEI", "5000000000");
    expect(minGasPriceWei(BSC_TESTNET_CHAIN_ID)).toBe(5_000_000_000n);
    expect(minGasPriceWei(BSC_MAINNET_CHAIN_ID)).toBe(5_000_000_000n);
  });

  it("invalid env is ignored", () => {
    vi.stubEnv("BNBAGENT_MIN_GAS_PRICE_WEI", "not-a-number");
    expect(minGasPriceWei(BSC_TESTNET_CHAIN_ID)).toBe(ONE_GWEI);
  });

  it("setter overrides both env and the per-chain default", () => {
    vi.stubEnv("BNBAGENT_MIN_GAS_PRICE_WEI", "5000000000");
    setMinGasPriceWei(7_000_000_000n);
    expect(minGasPriceWei(BSC_TESTNET_CHAIN_ID)).toBe(7_000_000_000n);
    expect(minGasPriceWei(BSC_MAINNET_CHAIN_ID)).toBe(7_000_000_000n);
  });

  it("setter rejects non-positive values", () => {
    expect(() => setMinGasPriceWei(0n)).toThrow(
      "min gas price must be positive",
    );
    expect(() => setMinGasPriceWei(-1n)).toThrow(
      "min gas price must be positive",
    );
  });
});

describe("getDefaultReceiptTimeout", () => {
  it("defaults to 300", () => {
    expect(getDefaultReceiptTimeout()).toBe(DEFAULT_RECEIPT_TIMEOUT);
    expect(DEFAULT_RECEIPT_TIMEOUT).toBe(300);
  });

  it("env overrides the default", () => {
    vi.stubEnv("BNBAGENT_RECEIPT_TIMEOUT", "600");
    expect(getDefaultReceiptTimeout()).toBe(600);
  });

  it("invalid env is ignored", () => {
    vi.stubEnv("BNBAGENT_RECEIPT_TIMEOUT", "soon");
    expect(getDefaultReceiptTimeout()).toBe(300);
  });

  it("setter overrides env", () => {
    vi.stubEnv("BNBAGENT_RECEIPT_TIMEOUT", "600");
    setDefaultReceiptTimeout(900);
    expect(getDefaultReceiptTimeout()).toBe(900);
  });

  it("setter rejects non-positive values", () => {
    expect(() => setDefaultReceiptTimeout(0)).toThrow(
      "receipt timeout must be positive",
    );
    expect(() => setDefaultReceiptTimeout(-5)).toThrow(
      "receipt timeout must be positive",
    );
  });

  it("resolves lazily: a runtime setter call is honored by later reads", () => {
    expect(getDefaultReceiptTimeout()).toBe(300);
    setDefaultReceiptTimeout(777);
    expect(getDefaultReceiptTimeout()).toBe(777);
  });
});

describe("getRelayUnseenTimeout", () => {
  it("defaults to 30", () => {
    expect(getRelayUnseenTimeout()).toBe(DEFAULT_RELAY_UNSEEN_TIMEOUT);
    expect(DEFAULT_RELAY_UNSEEN_TIMEOUT).toBe(30);
  });

  it("env overrides the default", () => {
    vi.stubEnv("BNBAGENT_RELAY_UNSEEN_TIMEOUT", "90");
    expect(getRelayUnseenTimeout()).toBe(90);
  });

  it("env value 0 disables the early abort", () => {
    vi.stubEnv("BNBAGENT_RELAY_UNSEEN_TIMEOUT", "0");
    expect(getRelayUnseenTimeout()).toBe(0);
  });

  it("invalid env is ignored", () => {
    vi.stubEnv("BNBAGENT_RELAY_UNSEEN_TIMEOUT", "soon");
    expect(getRelayUnseenTimeout()).toBe(30);
  });

  it("negative env is ignored", () => {
    vi.stubEnv("BNBAGENT_RELAY_UNSEEN_TIMEOUT", "-5");
    expect(getRelayUnseenTimeout()).toBe(30);
  });

  it("setter overrides env", () => {
    vi.stubEnv("BNBAGENT_RELAY_UNSEEN_TIMEOUT", "90");
    setRelayUnseenTimeout(45);
    expect(getRelayUnseenTimeout()).toBe(45);
  });

  it("setter accepts 0 (disable) but rejects negatives", () => {
    setRelayUnseenTimeout(0);
    expect(getRelayUnseenTimeout()).toBe(0);
    expect(() => setRelayUnseenTimeout(-1)).toThrow(
      "relay unseen timeout must be >= 0",
    );
  });
});

describe("_resetTxConfigOverrides", () => {
  it("clears all overrides back to env/default precedence", () => {
    setMinGasPriceWei(9_000_000_000n);
    setDefaultReceiptTimeout(42);
    setRelayUnseenTimeout(7);
    _resetTxConfigOverrides();
    expect(minGasPriceWei(BSC_TESTNET_CHAIN_ID)).toBe(ONE_GWEI);
    expect(getDefaultReceiptTimeout()).toBe(300);
    expect(getRelayUnseenTimeout()).toBe(30);
  });
});
