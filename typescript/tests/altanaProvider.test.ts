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
  const registerSessionKeyMock = vi.fn();
  const balancesMock = vi.fn();
  const signOrderMock = vi.fn();
  // 0.5.0-surface toggle: tests flip this off to simulate a pre-0.5.0
  // install (no registerSessionKey/balances on the client, no BNB_TESTNET
  // on the module).
  const v050 = { enabled: true };
  const createClientMock = vi.fn(() => ({
    createWallet: createWalletMock,
    execute: executeMock,
    grantSession: grantSessionMock,
    revokeSession: revokeSessionMock,
    ...(v050.enabled
      ? { registerSessionKey: registerSessionKeyMock, balances: balancesMock }
      : {}),
  }));
  return {
    executeMock,
    grantSessionMock,
    revokeSessionMock,
    createWalletMock,
    signerFromPrivateKeyMock,
    registerSessionKeyMock,
    balancesMock,
    signOrderMock,
    v050,
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
    get BNB_TESTNET() {
      return sdkMocks.v050.enabled ? { chainId: 97 } : undefined;
    },
    signOrder: sdkMocks.signOrderMock,
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
// 2100-01-01 — a valid future expiry for grantSession's DOA pre-flight.
const EXPIRY = 4_102_444_800;

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
  sdkMocks.registerSessionKeyMock.mockClear();
  sdkMocks.balancesMock.mockClear();
  sdkMocks.signOrderMock.mockReset();
  sdkMocks.signOrderMock.mockResolvedValue(`0x${"cd".repeat(98)}`);
  sdkMocks.v050.enabled = true;
  sdkMocks.grantSessionMock.mockResolvedValue(fakeSession());
  sdkMocks.revokeSessionMock.mockResolvedValue({
    callsId: CALLS_ID,
    status: "CONFIRMED",
    transactionHash: FAKE_TX_HASH,
  });
  sdkMocks.registerSessionKeyMock.mockResolvedValue({
    alreadyRegistered: false,
    callsId: CALLS_ID,
    status: "CONFIRMED",
    transactionHash: FAKE_TX_HASH,
  });
  sdkMocks.balancesMock.mockResolvedValue({ native: 0n });
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

  it("admin mode declares exactly {broadcast.self, calls.arbitrary, intents.erc8004, intents.erc8183} — no sign.*, no x402.pay", () => {
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

  it("makeX402Payer: session mode returns a payer (x402.pay declared); admin mode refuses with the session path", () => {
    // Deeper x402 coverage lives in altanaX402.test.ts; this pins the
    // capability split at the provider seam.
    const session = new AltanaWalletProvider({ session: fakeSession() });
    expect(session.supports(X402_PAY)).toBe(true);
    expect(typeof session.makeX402Payer().request).toBe("function");

    const admin = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    let thrown: unknown;
    try {
      admin.makeX402Payer();
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(UnsupportedWalletOperation);
    const message = (thrown as Error).message;
    expect(message).toContain(X402_PAY);
    expect(message).toMatch(/session/);
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

  it("sessionQuoteSigner signs the EIP-191 negotiation digest as the wallet account", async () => {
    const session = fakeSession();
    const provider = new AltanaWalletProvider({ session });

    const signer = provider.sessionQuoteSigner();
    const signature = await signer.signQuote(`0x${"22".repeat(32)}`);

    expect(signer.address).toBe(WALLET);
    expect(signer.validUntil).toBe(session.expiry);
    expect(signature).toBe(`0x${"cd".repeat(98)}`);
    expect(sdkMocks.signOrderMock).toHaveBeenCalledWith(
      session,
      "0x49d4c1d50ce22680c719e4b76e670399384808e6fb3f649cd025033ce29cbb9a",
    );
    expect(provider.supports(SIGN_MESSAGE)).toBe(false);
  });

  it("sessionQuoteSigner refuses admin mode instead of falling back to the EOA", () => {
    const admin = new AltanaWalletProvider({ privateKey: ADMIN_PK });

    expect(() => admin.sessionQuoteSigner()).toThrow(
      /quote signing needs the session/,
    );
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
    expect("register" in forwarded).toBe(false);
  });

  it("grantSession forwards register: false on 0.5.0+, and refuses it with upgrade guidance on older SDKs", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    await provider.grantSession({
      permissions: {},
      expiry: EXPIRY,
      register: false,
    });
    expect(
      (sdkMocks.grantSessionMock.mock.calls[0]?.[0] as { register?: boolean })
        .register,
    ).toBe(false);

    // Pre-0.5.0 install: the SDK would silently ignore the flag, register
    // the key and charge the fee — the provider must refuse up front.
    sdkMocks.v050.enabled = false;
    const old = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    await expect(
      old.grantSession({ permissions: {}, expiry: EXPIRY, register: false }),
    ).rejects.toThrow(/requires '@altananetwork\/sdk' >= 0\.5\.0/);
    expect(sdkMocks.grantSessionMock).toHaveBeenCalledTimes(1);
  });

  it("registerSessionKey forwards wallet/signer/session, passes FAILED through as an error, and is admin-only", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    const session = fakeSession();
    const result = await provider.registerSessionKey(session);
    expect(result).toEqual({
      alreadyRegistered: false,
      callsId: CALLS_ID,
      status: "CONFIRMED",
      transactionHash: FAKE_TX_HASH,
    });
    const forwarded = sdkMocks.registerSessionKeyMock.mock
      .calls[0]?.[0] as Record<string, unknown>;
    expect(forwarded.wallet).toEqual({ address: ADMIN_ADDRESS });
    expect(forwarded.session).toBe(session);
    expect("feeToken" in forwarded).toBe(false);

    // Idempotent short-circuit passes through untouched.
    sdkMocks.registerSessionKeyMock.mockResolvedValueOnce({
      alreadyRegistered: true,
    });
    await expect(provider.registerSessionKey(session)).resolves.toEqual({
      alreadyRegistered: true,
    });

    sdkMocks.registerSessionKeyMock.mockResolvedValueOnce({
      alreadyRegistered: false,
      callsId: CALLS_ID,
      status: "FAILED",
    });
    await expect(provider.registerSessionKey(session)).rejects.toThrow(
      new RegExp(`FAILED for registerSessionKey.*${CALLS_ID}`),
    );

    const sessionMode = new AltanaWalletProvider({ session: fakeSession() });
    await expect(sessionMode.registerSessionKey(session)).rejects.toThrow(
      /requires an admin-mode AltanaWalletProvider/,
    );
  });

  it("registerSessionKey on a pre-0.5.0 SDK fails with upgrade guidance", async () => {
    sdkMocks.v050.enabled = false;
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    await expect(provider.registerSessionKey(fakeSession())).rejects.toThrow(
      /requires '@altananetwork\/sdk' >= 0\.5\.0/,
    );
    expect(sdkMocks.registerSessionKeyMock).not.toHaveBeenCalled();
  });

  it("rejects past/zero/non-integer/milliseconds expiry before paying the registration fee", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      nonceRetry: { delayMs: 0 },
    });
    const nowSeconds = Math.floor(Date.now() / 1000);
    await expect(
      provider.grantSession({ permissions: {}, expiry: nowSeconds - 10 }),
    ).rejects.toThrow(/not in the future/);
    await expect(
      provider.grantSession({ permissions: {}, expiry: Date.now() }),
    ).rejects.toThrow(/looks like a milliseconds timestamp/);
    await expect(
      provider.grantSession({ permissions: {}, expiry: 1.5 }),
    ).rejects.toThrow(/positive integer/);
    await expect(
      provider.grantSession({ permissions: {}, expiry: 0 }),
    ).rejects.toThrow(/positive integer/);
    // All refused client-side: nothing reached the SDK, no fee risked.
    expect(sdkMocks.grantSessionMock).not.toHaveBeenCalled();
    expect(sdkMocks.createWalletMock).not.toHaveBeenCalled();
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

describe("AltanaWalletProvider — balances", () => {
  it("reads balances for the provider address in both modes, forwarding tokens verbatim", async () => {
    sdkMocks.balancesMock.mockResolvedValue({
      native: 5n,
      tokens: [],
    });
    const admin = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    await expect(admin.balances()).resolves.toEqual({
      native: 5n,
      tokens: [],
    });
    expect(sdkMocks.balancesMock).toHaveBeenCalledWith({
      wallet: ADMIN_ADDRESS,
    });
    // Pure read: no admin wallet handle needed, nothing queued to the relay.
    expect(sdkMocks.createWalletMock).not.toHaveBeenCalled();

    const token = getAddress(`0x${"33".repeat(20)}`);
    const session = new AltanaWalletProvider({ session: fakeSession() });
    await session.balances({ tokens: [token] });
    expect(sdkMocks.balancesMock).toHaveBeenLastCalledWith({
      wallet: WALLET,
      tokens: [token],
    });
  });

  it("requires >= 0.4.0 for balances at all and >= 0.5.0 for the tokens option", async () => {
    sdkMocks.v050.enabled = false;
    const provider = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    await expect(provider.balances()).rejects.toThrow(
      /requires '@altananetwork\/sdk' >= 0\.4\.0/,
    );

    // A 0.4.0-shaped client: balances exists (native-only), but the 0.5.0
    // markers are absent — passing tokens must refuse, not silently drop.
    sdkMocks.createClientMock.mockReturnValueOnce({
      createWallet: sdkMocks.createWalletMock,
      execute: sdkMocks.executeMock,
      grantSession: sdkMocks.grantSessionMock,
      revokeSession: sdkMocks.revokeSessionMock,
      balances: sdkMocks.balancesMock,
    });
    const v040 = new AltanaWalletProvider({ privateKey: ADMIN_PK });
    await expect(
      v040.balances({ tokens: [getAddress(`0x${"33".repeat(20)}`)] }),
    ).rejects.toThrow(/requires '@altananetwork\/sdk' >= 0\.5\.0/);
    expect(sdkMocks.balancesMock).not.toHaveBeenCalled();
  });
});

describe("AltanaWalletProvider — network presets", () => {
  it("resolves 'bnb-testnet' to the SDK's BNB_TESTNET config on 0.5.0+, with upgrade guidance on older SDKs", async () => {
    const provider = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      network: "bnb-testnet",
    });
    await provider.balances();
    expect(sdkMocks.createClientMock).toHaveBeenCalledWith({
      chains: [{ chainId: 97 }],
    });

    sdkMocks.v050.enabled = false;
    const old = new AltanaWalletProvider({
      privateKey: ADMIN_PK,
      network: "bnb-testnet",
    });
    await expect(old.balances()).rejects.toThrow(
      /'bnb-testnet' network preset requires '@altananetwork\/sdk' >= 0\.5\.0/,
    );
    expect(sdkMocks.createClientMock).toHaveBeenCalledTimes(1);
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

    // File fallback. A 0600 file loads silently; a group/other-readable
    // one still loads but warns (the file holds the session private key).
    vi.unstubAllEnvs();
    const dir = mkdtempSync(join(tmpdir(), "altana-session-"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const file = join(dir, "session.json");
      writeFileSync(file, envelope, { mode: 0o600 });
      vi.stubEnv("ALTANA_SESSION_FILE", file);
      const fromFile = await AltanaWalletProvider.sessionFromEnv();
      expect(fromFile.address).toBe(WALLET);
      expect(warnSpy).not.toHaveBeenCalled();

      const loose = join(dir, "loose.json");
      writeFileSync(loose, envelope, { mode: 0o644 });
      vi.stubEnv("ALTANA_SESSION_FILE", loose);
      await AltanaWalletProvider.sessionFromEnv();
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("chmod 600"),
      );
    } finally {
      warnSpy.mockRestore();
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
      loader.setAltanaSdkImporter(() =>
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
      loader.setAltanaSdkImporter(() =>
        Promise.reject(new Error("boom: disk on fire")),
      );
      await expect(loader.loadAltanaSdk()).rejects.toThrow(/disk on fire/);
    } finally {
      loader.setAltanaSdkImporter(null);
    }

    // Failures were not cached: the restored importer resolves (to the
    // file-level mock module) on the very next call.
    const sdk = await loader.loadAltanaSdk();
    expect(typeof sdk.createClient).toBe("function");
  });
});
