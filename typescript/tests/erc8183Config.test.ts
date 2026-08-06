/**
 * Ports `python/tests/test_erc8183_config.py` (WALLET_KIND=twak dispatches
 * to `TWAKProvider` — full coverage of that path lives in
 * `twakProvider.test.ts`; any other non-`""`/`"evm"` wallet kind throws
 * instead of dispatching to a factory).
 *
 * `ERC8183Config` wraps `EVMWalletProvider` for the `privateKey`/
 * `walletPassword` convenience paths, which persists an encrypted keystore
 * to disk by default. Two isolation mechanisms keep this suite from ever
 * touching the real developer machine:
 *
 * - Direct-constructor tests pass `walletsDir` (a TS-only test hook — see
 *   `config.ts` — not part of the Python surface) pointed at a fresh
 *   `mkdtempSync` directory per test.
 * - `fromEnv()` has no such hook (it mirrors Python's signature exactly),
 *   so `node:os`'s `homedir()` is mocked file-wide to a throwaway
 *   directory instead; `EVMWalletProvider`'s default wallets dir
 *   (`join(homedir(), ".bnbagent", "wallets")`) is computed once at module
 *   load, so the mock must be installed (and the module dynamically
 *   imported) before any test runs — hence the top-level `vi.mock` +
 *   dynamic `import()` below, mirroring `erc8004Agent.test.ts`'s pattern
 *   for module-load-time RPC mocking.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { NetworkConfig } from "../src/config.js";
import { StorageProvider } from "../src/storage/storageProvider.js";
import { WalletProvider } from "../src/wallets/walletProvider.js";

const FAKE_HOME = mkdtempSync(join(tmpdir(), "bnbagent-erc8183-config-home-"));

vi.mock("node:os", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:os")>();
  return { ...actual, homedir: () => FAKE_HOME };
});

const { ERC8183Config } = await import("../src/erc8183/config.js");

const VALID_PK = `0x${"cd".repeat(32)}`;
const VALID_PASSWORD = "test-password";

class StubWallet extends WalletProvider {
  static override readonly kind = "stub";
  constructor(private readonly addr: `0x${string}`) {
    super();
  }
  get address(): `0x${string}` {
    return this.addr;
  }
}

class FakeStorage extends StorageProvider {
  async upload(): Promise<string> {
    return "file:///fake";
  }
  async download(): Promise<Record<string, unknown>> {
    return {};
  }
  async exists(): Promise<boolean> {
    return false;
  }
}

const ENV_KEYS = [
  "NETWORK",
  "RPC_URL",
  "PRIVATE_KEY",
  "WALLET_PASSWORD",
  "WALLET_ADDRESS",
  "WALLET_KIND",
  "ERC8183_COMMERCE_ADDRESS",
  "ERC8183_ROUTER_ADDRESS",
  "ERC8183_POLICY_ADDRESS",
  "ERC8183_SERVICE_PRICE",
  "ERC8183_AGENT_URL",
  "STORAGE_LOCAL_PATH",
] as const;

let saved: Record<string, string | undefined>;
let wdir: string;

beforeEach(() => {
  saved = {};
  for (const key of ENV_KEYS) {
    saved[key] = process.env[key];
    delete process.env[key];
  }
  const root = mkdtempSync(join(tmpdir(), "bnbagent-erc8183-config-test-"));
  wdir = join(root, "wallets");
});

afterEach(() => {
  for (const key of ENV_KEYS) {
    if (saved[key] === undefined) delete process.env[key];
    else process.env[key] = saved[key];
  }
  rmSync(wdir, { recursive: true, force: true });
  // Reset the shared fake-home keystore dir between tests so
  // `fromEnv()`-driven wallet creation never sees a leftover keystore from
  // a previous test (which would flip "auto-generate" into "load
  // existing" or trip the "multiple wallets found" guard).
  rmSync(join(FAKE_HOME, ".bnbagent"), { recursive: true, force: true });
});

afterAll(() => {
  rmSync(FAKE_HOME, { recursive: true, force: true });
});

describe("ERC8183Config: construction", () => {
  it("wraps private_key + wallet_password into a wallet_provider and clears both fields", () => {
    const config = new ERC8183Config({
      privateKey: VALID_PK,
      walletPassword: VALID_PASSWORD,
      walletsDir: wdir,
    });
    expect(config.privateKey).toBe("");
    expect(config.walletPassword).toBe("");
    expect(config.walletProvider).not.toBeNull();
    expect(config.effectiveChainId).toBe(97);
  });

  it("never leaks the plaintext key through toString() or JSON.stringify()", () => {
    const config = new ERC8183Config({
      privateKey: VALID_PK,
      walletPassword: VALID_PASSWORD,
      walletsDir: wdir,
    });
    expect(config.toString()).not.toContain(VALID_PK);
    expect(config.toString()).not.toContain(VALID_PASSWORD);
    expect(JSON.stringify(config)).not.toContain(VALID_PK);
    expect(JSON.stringify(config)).not.toContain(VALID_PASSWORD);
  });

  it("accepts an explicit wallet_provider as-is", () => {
    const wallet = new StubWallet(`0x${"ff".repeat(20)}`);
    const config = new ERC8183Config({ walletProvider: wallet });
    expect(config.walletProvider).toBe(wallet);
    expect(config.privateKey).toBe("");
  });

  it("resolves an explicit NetworkConfig object's fields as-is", () => {
    const custom: NetworkConfig = {
      name: "custom",
      chainId: 12345,
      rpcUrl: "https://rpc.example.com",
      usePaymaster: false,
      registryContract: "",
      commerceContract: `0x${"ab".repeat(20)}`,
      routerContract: `0x${"cd".repeat(20)}`,
      policyContract: `0x${"ef".repeat(20)}`,
    };
    const config = new ERC8183Config({
      network: custom,
      privateKey: VALID_PK,
      walletPassword: VALID_PASSWORD,
      walletsDir: wdir,
    });
    expect(config.effectiveRpcUrl).toBe("https://rpc.example.com");
    expect(config.effectiveChainId).toBe(12345);
    expect(config.effectiveCommerceAddress).toBe(`0x${"ab".repeat(20)}`);
    expect(config.effectiveRouterAddress).toBe(`0x${"cd".repeat(20)}`);
    expect(config.effectivePolicyAddress).toBe(`0x${"ef".repeat(20)}`);
  });

  it("ignores ERC8183_* env overrides when network is an explicit NetworkConfig", () => {
    process.env.ERC8183_COMMERCE_ADDRESS = `0x${"11".repeat(20)}`;
    const custom: NetworkConfig = {
      name: "custom",
      chainId: 12345,
      rpcUrl: "https://rpc.example.com",
      usePaymaster: false,
      registryContract: "",
      commerceContract: `0x${"ab".repeat(20)}`,
      routerContract: `0x${"cd".repeat(20)}`,
      policyContract: `0x${"ef".repeat(20)}`,
    };
    const config = new ERC8183Config({ network: custom });
    expect(config.effectiveCommerceAddress).toBe(`0x${"ab".repeat(20)}`);
  });

  it("defaults come from the bsc-testnet network preset", () => {
    const config = new ERC8183Config();
    expect(config.effectiveCommerceAddress.startsWith("0x")).toBe(true);
    expect(config.effectiveRouterAddress.startsWith("0x")).toBe(true);
    expect(config.effectivePolicyAddress.startsWith("0x")).toBe(true);
    expect(config.effectiveChainId).toBe(97);
  });

  it("requires wallet_password when private_key is set", () => {
    expect(() => new ERC8183Config({ privateKey: VALID_PK })).toThrow(
      /wallet_password is required/,
    );
  });

  it("password-only with no existing keystore auto-generates a wallet", () => {
    const config = new ERC8183Config({
      walletPassword: "test-pw",
      walletsDir: wdir,
    });
    expect(config.walletProvider).not.toBeNull();
  });

  it("has no wallet_provider when neither private_key nor wallet_password is given (read-only config)", () => {
    const config = new ERC8183Config();
    expect(config.walletProvider).toBeNull();
  });

  it("normalizes a private_key without a 0x prefix", () => {
    const config = new ERC8183Config({
      privateKey: "cd".repeat(32),
      walletPassword: VALID_PASSWORD,
      walletsDir: wdir,
    });
    expect(config.privateKey).toBe("");
    expect(config.walletProvider).not.toBeNull();
  });

  it("toString() includes wallet= and the address prefix", () => {
    const wallet = new StubWallet(`0x${"ff".repeat(20)}`);
    const config = new ERC8183Config({ walletProvider: wallet });
    const r = config.toString();
    expect(r).toContain("wallet=");
    expect(r.toLowerCase()).toContain("0xffffffff");
  });

  it("toString() reports wallet=None without a wallet_provider", () => {
    const config = new ERC8183Config();
    expect(config.toString()).toContain("wallet=None");
  });
});

describe("ERC8183Config: wallet_kind", () => {
  it("accepts wallet_kind='evm' and keeps the private_key path", () => {
    const config = new ERC8183Config({
      walletKind: "evm",
      privateKey: VALID_PK,
      walletPassword: VALID_PASSWORD,
      walletsDir: wdir,
    });
    expect(config.walletProvider).not.toBeNull();
  });

  it("rejects an unknown wallet_kind (twak now dispatches — see twakProvider.test.ts)", () => {
    expect(() => new ERC8183Config({ walletKind: "mpc" })).toThrow(
      /Unknown wallet kind: mpc/,
    );
  });
});

describe("ERC8183Config.fromEnv", () => {
  it("resolves rpc_url and ERC8183_*_ADDRESS overrides from env", () => {
    process.env.RPC_URL = "https://rpc.example.com";
    process.env.ERC8183_COMMERCE_ADDRESS = `0x${"ab".repeat(20)}`;
    process.env.ERC8183_ROUTER_ADDRESS = `0x${"cd".repeat(20)}`;
    process.env.ERC8183_POLICY_ADDRESS = `0x${"ef".repeat(20)}`;
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;

    const config = ERC8183Config.fromEnv(new FakeStorage());
    expect(config.effectiveRpcUrl).toBe("https://rpc.example.com");
    expect(config.effectiveCommerceAddress).toBe(`0x${"ab".repeat(20)}`);
    expect(config.effectiveRouterAddress).toBe(`0x${"cd".repeat(20)}`);
    expect(config.effectivePolicyAddress).toBe(`0x${"ef".repeat(20)}`);
  });

  it("requires WALLET_PASSWORD even when PRIVATE_KEY is set", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    expect(() => ERC8183Config.fromEnv(new FakeStorage())).toThrow(
      /WALLET_PASSWORD is required/,
    );
  });

  it("defaults ERC8183_SERVICE_PRICE to 1e18 and honors an override", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;
    const defaultConfig = ERC8183Config.fromEnv(new FakeStorage());
    expect(defaultConfig.servicePrice).toBe("1000000000000000000");

    process.env.ERC8183_SERVICE_PRICE = "5000000000000000000";
    const overridden = ERC8183Config.fromEnv(new FakeStorage());
    expect(overridden.servicePrice).toBe("5000000000000000000");
  });

  it("auto-creates a wallet_provider from PRIVATE_KEY/WALLET_PASSWORD", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;
    const config = ERC8183Config.fromEnv(new FakeStorage());
    expect(config.walletProvider).not.toBeNull();
    expect(config.privateKey).toBe("");
  });

  it("reads ERC8183_AGENT_URL", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;
    process.env.ERC8183_AGENT_URL = "http://localhost:8003/erc8183";
    const config = ERC8183Config.fromEnv(new FakeStorage());
    expect(config.agentUrl).toBe("http://localhost:8003/erc8183");
  });

  it("defaults agent_url to null when unset", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;
    const config = ERC8183Config.fromEnv(new FakeStorage());
    expect(config.agentUrl).toBeNull();
  });
});

describe("ERC8183Config.fromEnvOptional", () => {
  it("returns null when WALLET_PASSWORD/PRIVATE_KEY are both missing", () => {
    expect(ERC8183Config.fromEnvOptional()).toBeNull();
  });

  it("returns a config instance when the env is valid", () => {
    process.env.PRIVATE_KEY = VALID_PK;
    process.env.WALLET_PASSWORD = VALID_PASSWORD;
    // fromEnvOptional() has no storage param — let it fall back to
    // LocalStorageProvider.fromEnv(), pinned to a throwaway dir so the
    // test doesn't touch the repo's ./.agent-data.
    process.env.STORAGE_LOCAL_PATH = mkdtempSync(
      join(tmpdir(), "bnbagent-erc8183-config-storage-"),
    );
    const result = ERC8183Config.fromEnvOptional();
    expect(result).toBeInstanceOf(ERC8183Config);
    rmSync(process.env.STORAGE_LOCAL_PATH, { recursive: true, force: true });
  });
});
