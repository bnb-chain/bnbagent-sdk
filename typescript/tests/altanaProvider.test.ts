/**
 * `AltanaWalletProvider` conformance (`src/wallets/altana/provider.ts`):
 * capability surface, dual admin/session construction, session-management
 * gating, env/keystore entry points, and the missing-peer install guidance.
 *
 * `@altananetwork/sdk` is mocked at the module level (the provider only
 * ever reaches it through the lazy loader), with `signerFromPrivateKey`
 * mocked faithfully via viem so address/publicKey derivation stays real.
 * Executor behavior lives in `altanaExecutor.test.ts`.
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BROADCAST_SELF,
  CALLS_ARBITRARY,
  INTENTS_ERC8004,
  INTENTS_ERC8183,
  SIGN_MESSAGE,
  SIGN_TRANSACTION,
  SIGN_TYPED_DATA,
  X402_PAY,
} from "../src/wallets/capabilities.js";
import { UnsupportedWalletOperation } from "../src/wallets/errors.js";
import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";
import type { ExecutionContext } from "../src/wallets/intents.js";
import { LocalExecutor } from "../src/wallets/localExecutor.js";
import { FAKE_TX_HASH } from "./helpers/mockTransport.js";

const sdkMocks = vi.hoisted(() => {
  const executeMock = vi.fn();
  const grantSessionMock = vi.fn();
  const revokeSessionMock = vi.fn();
  const createWalletMock = vi.fn();
  const signerFromPrivateKeyMock = vi.fn();
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: grantSessionMock,
    revokeSession: revokeSessionMock,
  }));
  return {
    executeMock,
    grantSessionMock,
    revokeSessionMock,
    createWalletMock,
    signerFromPrivateKeyMock,
    createClientMock,
    factoryRuns: { count: 0 },
  };
});

vi.mock("@altananetwork/sdk", async () => {
  sdkMocks.factoryRuns.count += 1;
  const { privateKeyToAccount: toAccount } = await import("viem/accounts");
  sdkMocks.signerFromPrivateKeyMock.mockImplementation(
    (privateKey: `0x${string}`) => {
      const account = toAccount(privateKey);
      return {
        type: "privateKey",
        address: account.address,
        publicKey: account.publicKey,
        signDigest: async () => `0x${"11".repeat(65)}`,
        _privateKey: privateKey,
      };
    },
  );
  return {
    createClient: sdkMocks.createClientMock,
    signerFromPrivateKey: sdkMocks.signerFromPrivateKeyMock,
    createPrivateKeySigner: () =>
      sdkMocks.signerFromPrivateKeyMock(`0x${"77".repeat(32)}`),
    BNB: { chainId: 56 },
  };
});

const {
  ALTANA_NONCE_RETRY_DELAY_MS,
  ALTANA_NONCE_RETRY_TRIES,
  AltanaIntentExecutor,
  AltanaWalletProvider,
} = await import("../src/wallets/altana/provider.js");
type AltanaSessionT = import("../src/wallets/altana/types.js").AltanaSession;
type AltanaSignerT = import("../src/wallets/altana/types.js").AltanaSigner;

const ADMIN_PK: `0x${string}` = `0x${"a1".repeat(32)}`;
const ADMIN_ADDRESS = privateKeyToAccount(ADMIN_PK).address;
const SESSION_PK: `0x${string}` = `0x${"b2".repeat(32)}`;
const WALLET = getAddress(`0x${"11".repeat(20)}`);
const CALLS_ID: `0x${string}` = `0x${"ca".repeat(32)}`;
const EXPIRY = 1_767_225_600;

function fakeSession(): AltanaSessionT {
  const account = privateKeyToAccount(SESSION_PK);
  const signer = {
    type: "privateKey",
    address: account.address,
    publicKey: account.publicKey,
    signDigest: async () => `0x${"22".repeat(65)}` as const,
    _privateKey: SESSION_PK,
  };
  return {
    walletAddress: WALLET,
    signer: signer as unknown as AltanaSignerT,
    publicKey: account.publicKey,
    permissions: {
      calls: [{ to: WALLET }],
      spend: [{ limit: 1n, period: "day" }],
    },
    expiry: EXPIRY,
  };
}

function makeExecutionContext(): ExecutionContext {
  return { client: {} as ExecutionContext["client"] };
}

beforeEach(() => {
  sdkMocks.executeMock.mockClear();
  sdkMocks.grantSessionMock.mockClear();
  sdkMocks.revokeSessionMock.mockClear();
  sdkMocks.createWalletMock.mockClear();
  sdkMocks.createClientMock.mockClear();
  sdkMocks.signerFromPrivateKeyMock.mockClear();
  sdkMocks.createWalletMock.mockImplementation(
    async ({ signer }: { signer: { address: `0x${string}` } }) => ({
      address: signer.address,
      signer,
    }),
  );
  sdkMocks.grantSessionMock.mockResolvedValue(fakeSession());
  sdkMocks.revokeSessionMock.mockResolvedValue({
    callsId: CALLS_ID,
    status: "CONFIRMED",
    transactionHash: FAKE_TX_HASH,
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("AltanaWalletProvider — identity and capabilities", () => {
  it("derives the admin address synchronously from the private key without touching the SDK", () => {
    // Runs first in this file: the module factory must not have executed
    // yet (construction + address are SDK-free by design).
    expect(sdkMocks.factoryRuns.count).toBe(0);
    const provider = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    expect(provider.address).toBe(ADMIN_ADDRESS);
    expect(provider.mode).toBe("admin");
    expect(sdkMocks.signerFromPrivateKeyMock).not.toHaveBeenCalled();
    expect(sdkMocks.createClientMock).not.toHaveBeenCalled();
    expect(sdkMocks.createWalletMock).not.toHaveBeenCalled();
  });

  it("declares exactly {broadcast.self, calls.arbitrary, intents.erc8004, intents.erc8183} — no sign.*, no x402.pay", () => {
    const provider = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    expect(provider.kind).toBe("altana");
    expect(AltanaWalletProvider.kind).toBe("altana");
    expect(provider.capabilities()).toEqual(
      new Set([
        BROADCAST_SELF,
        CALLS_ARBITRARY,
        INTENTS_ERC8004,
        INTENTS_ERC8183,
      ]),
    );
    for (const absent of [
      SIGN_MESSAGE,
      SIGN_TRANSACTION,
      SIGN_TYPED_DATA,
      X402_PAY,
    ]) {
      expect(provider.supports(absent)).toBe(false);
    }
  });

  it("sets fundBundlesApproval to the literal true (the ERC8183Client.fund gate is ===)", () => {
    const provider = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    expect(provider.fundBundlesApproval).toBe(true);
  });

  it("makeX402Payer throws UnsupportedWalletOperation with the dual-account guidance", () => {
    const provider = new AltanaWalletProvider({ session: fakeSession() });
    let thrown: unknown;
    try {
      provider.makeX402Payer();
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(UnsupportedWalletOperation);
    const message = (thrown as Error).message;
    expect(message).toContain(X402_PAY);
    expect(message).toMatch(/separate dedicated low-balance EOA/);
    expect(message).toMatch(/RECEIVING x402 payments.*works/);
  });

  it("session mode: address is the session's wallet and describe() leaks no key material", () => {
    const provider = new AltanaWalletProvider({ session: fakeSession() });
    expect(provider.mode).toBe("session");
    expect(provider.address).toBe(WALLET);
    const description = provider.describe();
    expect(description.kind).toBe("altana");
    expect(description.address).toBe(WALLET);
    const dumped = JSON.stringify(description);
    expect(dumped).not.toContain(SESSION_PK.slice(2));
    expect(dumped).not.toContain("privateKey");
  });

  it("makeExecutor returns an AltanaIntentExecutor (not LocalExecutor) despite having no sign.transaction", () => {
    const provider = new AltanaWalletProvider({ session: fakeSession() });
    expect(provider.supports(SIGN_TRANSACTION)).toBe(false);
    const executor = provider.makeExecutor(makeExecutionContext());
    expect(executor).toBeInstanceOf(AltanaIntentExecutor);
    expect(executor).not.toBeInstanceOf(LocalExecutor);
    expect(typeof executor.execute).toBe("function");
  });
});

describe("AltanaWalletProvider — construction validation", () => {
  it("requires exactly one of privateKey / signer / session", () => {
    expect(() => new AltanaWalletProvider({})).toThrow(/exactly one of/);
    expect(
      () =>
        new AltanaWalletProvider({
          privateKey: ADMIN_PK,
          session: fakeSession(),
        }),
    ).toThrow(/exactly one of/);
    expect(
      () =>
        new AltanaWalletProvider({
          privateKey: ADMIN_PK,
          signer: fakeSession().signer,
        }),
    ).toThrow(/exactly one of/);
    expect(() => new AltanaWalletProvider({ privateKey: "0x1234" })).toThrow(
      /Invalid private key/,
    );
  });

  it("rejects passkey admin signers with guidance and accepts EOA-backed ones", () => {
    const passkeySigner: AltanaSignerT = {
      type: "passkey",
      address: `0x${"00".repeat(20)}`,
      publicKey: `0x${"aa".repeat(33)}`,
      signDigest: async () => `0x${"bb".repeat(65)}`,
    };
    expect(() => new AltanaWalletProvider({ signer: passkeySigner })).toThrow(
      /passkey/,
    );

    const eoaSigner = fakeSession().signer;
    const provider = new AltanaWalletProvider({ signer: eoaSigner });
    expect(provider.address).toBe(eoaSigner.address);
    expect(provider.mode).toBe("admin");
  });
});

describe("AltanaWalletProvider — session management", () => {
  it("grantSession/revokeSession on a session-mode provider fail with admin guidance", async () => {
    const provider = new AltanaWalletProvider({ session: fakeSession() });
    await expect(
      provider.grantSession({ permissions: {}, expiry: EXPIRY }),
    ).rejects.toThrow(/requires an admin-mode AltanaWalletProvider/);
    await expect(provider.revokeSession(CALLS_ID)).rejects.toThrow(
      /requires an admin-mode AltanaWalletProvider/,
    );
    expect(sdkMocks.grantSessionMock).not.toHaveBeenCalled();
    expect(sdkMocks.revokeSessionMock).not.toHaveBeenCalled();
  });

  it("grantSession forwards wallet/signer/permissions/expiry to the SDK and returns its session", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    const granted = fakeSession();
    sdkMocks.grantSessionMock.mockResolvedValueOnce(granted);
    const permissions = {
      calls: [{ to: WALLET }],
      spend: [{ limit: 9n, period: "day" as const }],
    };
    const session = await provider.grantSession({
      permissions,
      expiry: EXPIRY,
    });

    expect(session).toBe(granted);
    expect(sdkMocks.createWalletMock).toHaveBeenCalledTimes(1);
    expect(sdkMocks.grantSessionMock).toHaveBeenCalledTimes(1);
    const forwarded = sdkMocks.grantSessionMock.mock.calls[0]?.[0] as Record<
      string,
      unknown
    >;
    expect(forwarded.wallet).toEqual({ address: ADMIN_ADDRESS });
    expect(forwarded.permissions).toBe(permissions);
    expect(forwarded.expiry).toBe(EXPIRY);
    expect((forwarded.signer as { address: `0x${string}` }).address).toBe(
      ADMIN_ADDRESS,
    );
    // Optional knobs stay absent rather than being forwarded as undefined.
    expect("sessionSigner" in forwarded).toBe(false);
    expect("feeToken" in forwarded).toBe(false);
  });

  it("revokeSession accepts a session or a bare public key and throws on relay FAILED", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    const toRevoke = fakeSession();
    const result = await provider.revokeSession(toRevoke);
    expect(result.status).toBe("CONFIRMED");
    expect(
      (sdkMocks.revokeSessionMock.mock.calls[0]?.[0] as { session: unknown })
        .session,
    ).toBe(toRevoke);

    sdkMocks.revokeSessionMock.mockResolvedValueOnce({
      callsId: CALLS_ID,
      status: "FAILED",
    });
    await expect(provider.revokeSession(CALLS_ID)).rejects.toThrow(
      new RegExp(`FAILED for revokeSession.*${CALLS_ID}`),
    );
  });
});

describe("AltanaWalletProvider — entry points", () => {
  it("sessionFromEnv prefers ALTANA_SESSION over ALTANA_SESSION_FILE and errors when neither is set", async () => {
    const account = privateKeyToAccount(SESSION_PK);
    const envelope = JSON.stringify({
      version: 1,
      walletAddress: WALLET,
      publicKey: account.publicKey,
      expiry: EXPIRY,
      permissions: { spend: [{ limit: { $bigint: "5" }, period: "day" }] },
      signer: { type: "privateKey", privateKey: SESSION_PK },
    });

    // Inline wins: the FILE path is unreadable garbage, and must never be read.
    vi.stubEnv("ALTANA_SESSION", envelope);
    vi.stubEnv("ALTANA_SESSION_FILE", "/nonexistent/never-read.json");
    const provider = await AltanaWalletProvider.sessionFromEnv();
    expect(provider.mode).toBe("session");
    expect(provider.address).toBe(WALLET);

    // File fallback.
    vi.unstubAllEnvs();
    const dir = mkdtempSync(join(tmpdir(), "altana-session-"));
    try {
      const file = join(dir, "session.json");
      writeFileSync(file, envelope, { mode: 0o600 });
      vi.stubEnv("ALTANA_SESSION_FILE", file);
      const fromFile = await AltanaWalletProvider.sessionFromEnv();
      expect(fromFile.address).toBe(WALLET);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }

    vi.unstubAllEnvs();
    await expect(AltanaWalletProvider.sessionFromEnv()).rejects.toThrow(
      /ALTANA_SESSION.*ALTANA_SESSION_FILE/,
    );
  });

  it("adminFromKeystore decrypts the EVM keystore and pins the same address", () => {
    const dir = mkdtempSync(join(tmpdir(), "altana-keystore-"));
    try {
      new EVMWalletProvider({
        password: "test-password-123",
        privateKey: ADMIN_PK,
        walletsDir: dir,
      });
      const provider = AltanaWalletProvider.adminFromKeystore({
        password: "test-password-123",
        walletsDir: dir,
      });
      expect(provider.address).toBe(ADMIN_ADDRESS);
      expect(provider.mode).toBe("admin");
      expect(provider.kind).toBe("altana");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("defaults nonce-retry tuning to 4 tries x 5s (documented relay race envelope)", () => {
    expect(ALTANA_NONCE_RETRY_TRIES).toBe(4);
    expect(ALTANA_NONCE_RETRY_DELAY_MS).toBe(5_000);
  });
});

describe("altana sdk loader", () => {
  it("maps a missing @altananetwork/sdk install to pnpm-add guidance, without caching the failure", async () => {
    const loader = await import("../src/wallets/altana/sdkLoader.js");
    try {
      loader._setAltanaSdkImporterForTests(() =>
        Promise.reject(
          Object.assign(
            new Error(
              "Cannot find package '@altananetwork/sdk' imported from provider.js",
            ),
            { code: "ERR_MODULE_NOT_FOUND" },
          ),
        ),
      );
      await expect(loader.loadAltanaSdk()).rejects.toThrow(
        /optional peer dependency.*pnpm add @altananetwork\/sdk/,
      );

      // Unrelated import failures pass through unmapped.
      loader._setAltanaSdkImporterForTests(() =>
        Promise.reject(new Error("boom: disk on fire")),
      );
      await expect(loader.loadAltanaSdk()).rejects.toThrow(/disk on fire/);
    } finally {
      loader._setAltanaSdkImporterForTests(null);
    }

    // Failures were not cached: the restored importer resolves (to the
    // file-level mock module) on the very next call.
    const sdk = await loader.loadAltanaSdk();
    expect(typeof sdk.createClient).toBe("function");
  });
});
