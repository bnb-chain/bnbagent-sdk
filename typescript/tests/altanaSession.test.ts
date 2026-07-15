/**
 * Byte-exact Altana session serde (`src/wallets/altana/session.ts`) and the
 * default agent permissions builder (`src/wallets/altana/permissions.ts`).
 *
 * Uses the REAL `@altananetwork/sdk` (a devDependency) rather than a mock:
 * `signerFromPrivateKey` is pure offline crypto, and the whole point of the
 * serde contract is fidelity against the actual SDK's signer
 * reconstruction — a mock could drift from exactly the thing these tests
 * exist to pin (the on-chain hash commitment over the granted bytes).
 */

import { signerFromPrivateKey } from "@altananetwork/sdk";
import { getAddress } from "viem";
import { describe, expect, it } from "vitest";
import { NETWORKS } from "../src/config.js";
import { BNB_CHAIN_ADDRESSES } from "../src/networks/addresses.js";
import {
  DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
  defaultAgentPermissions,
} from "../src/wallets/altana/permissions.js";
import {
  ALTANA_SESSION_VERSION,
  deserializeSession,
  serializeSession,
} from "../src/wallets/altana/session.js";
import type {
  AltanaSession,
  AltanaSigner,
} from "../src/wallets/altana/types.js";

const SESSION_PK: `0x${string}` = `0x${"ab".repeat(32)}`;
const OTHER_PK: `0x${string}` = `0x${"cd".repeat(32)}`;
const WALLET: `0x${string}` = getAddress(`0x${"11".repeat(20)}`);
const TOKEN: `0x${string}` = getAddress(`0x${"22".repeat(20)}`);
const EXPIRY = 1_767_225_600;

/** A realistic granted session: token cap + native gas cap, one call rule. */
function makeSession(): AltanaSession {
  const signer = signerFromPrivateKey(SESSION_PK);
  return {
    walletAddress: WALLET,
    signer,
    publicKey: signer.publicKey,
    permissions: {
      calls: [{ to: TOKEN }],
      spend: [
        { limit: 10n ** 18n, period: "day", token: TOKEN },
        { limit: 10n ** 16n, period: "day" },
      ],
    },
    expiry: EXPIRY,
  };
}

describe("serializeSession / deserializeSession", () => {
  it("round-trips a session: bigint limits restored, fields verbatim, signer usable", async () => {
    const original = makeSession();
    const restored = await deserializeSession(serializeSession(original));

    expect(restored.walletAddress).toBe(original.walletAddress);
    expect(restored.publicKey).toBe(original.publicKey);
    expect(restored.expiry).toBe(original.expiry);
    // Deep-equality on permissions covers key order-insensitive structure;
    // the bigint checks pin the type (a number here breaks the on-chain
    // hash commitment).
    expect(restored.permissions).toEqual(original.permissions);
    const spend = restored.permissions.spend ?? [];
    expect(typeof spend[0]?.limit).toBe("bigint");
    expect(spend[0]?.limit).toBe(10n ** 18n);
    expect(typeof spend[1]?.limit).toBe("bigint");
    // The rebuilt signer is the same key: same publicKey, and it signs.
    expect(restored.signer.type).toBe("privateKey");
    expect(restored.signer.publicKey).toBe(original.signer.publicKey);
    const digest: `0x${string}` = `0x${"00".repeat(32)}`;
    expect(await restored.signer.signDigest(digest)).toBe(
      await original.signer.signDigest(digest),
    );
  });

  it("re-serializing a restored session is byte-identical (the on-chain hash contract)", async () => {
    const first = serializeSession(makeSession());
    const second = serializeSession(await deserializeSession(first));
    expect(second).toBe(first);
  });

  it("refuses to restore when the stored key does not derive the stored publicKey", async () => {
    const envelope = JSON.parse(serializeSession(makeSession())) as Record<
      string,
      unknown
    >;
    // Splice in another session's publicKey — simulates a corrupted or
    // hand-assembled file.
    envelope.publicKey = signerFromPrivateKey(OTHER_PK).publicKey;
    await expect(deserializeSession(JSON.stringify(envelope))).rejects.toThrow(
      /integrity check failed/,
    );
  });

  it("rejects non-privateKey signers with guidance (serialize and restore)", async () => {
    const passkeySigner: AltanaSigner = {
      type: "passkey",
      address: `0x${"00".repeat(20)}`,
      publicKey: `0x${"aa".repeat(33)}`,
      signDigest: async () => `0x${"bb".repeat(65)}`,
    };
    const session = { ...makeSession(), signer: passkeySigner };
    expect(() => serializeSession(session)).toThrow(/privateKey session/);
    expect(() => serializeSession(session)).toThrow(/passkey/);

    // A privateKey-typed signer without an accessible raw key is equally
    // unserializable (custom signer wrapping an HSM, say).
    const keylessSigner = {
      ...makeSession().signer,
      _privateKey: undefined,
    } as unknown as AltanaSigner;
    expect(() =>
      serializeSession({ ...makeSession(), signer: keylessSigner }),
    ).toThrow(/privateKey session/);

    // Restore path: a stored non-privateKey signer is refused too.
    const envelope = JSON.parse(serializeSession(makeSession())) as Record<
      string,
      unknown
    >;
    envelope.signer = { type: "passkey" };
    await expect(deserializeSession(JSON.stringify(envelope))).rejects.toThrow(
      /privateKey session/,
    );
  });

  it("rejects unknown envelope versions and non-envelope input", async () => {
    const envelope = JSON.parse(serializeSession(makeSession())) as Record<
      string,
      unknown
    >;
    envelope.version = ALTANA_SESSION_VERSION + 1;
    await expect(deserializeSession(JSON.stringify(envelope))).rejects.toThrow(
      /unsupported Altana session version/,
    );

    await expect(deserializeSession("not json at all")).rejects.toThrow(
      /invalid JSON/,
    );
    await expect(deserializeSession('"a string"')).rejects.toThrow(
      /expected a JSON object/,
    );
  });
});

describe("defaultAgentPermissions", () => {
  it("whitelists the five protocol targets and caps token + unconditional native spend", () => {
    const permissions = defaultAgentPermissions({
      chainId: 97,
      tokenSpend: { limit: 5n * 10n ** 18n },
    });

    const testnet = NETWORKS["bsc-testnet"];
    const paymentToken = BNB_CHAIN_ADDRESSES[97]?.paymentToken;
    expect(testnet).toBeDefined();
    expect(paymentToken).toBeDefined();
    if (!testnet || !paymentToken) return;
    expect(permissions.calls).toEqual([
      { to: getAddress(testnet.registryContract) },
      { to: getAddress(testnet.commerceContract) },
      { to: getAddress(testnet.routerContract) },
      { to: getAddress(testnet.policyContract) },
      { to: paymentToken },
    ]);

    const spend = permissions.spend ?? [];
    expect(spend).toHaveLength(2);
    expect(spend[0]).toEqual({
      limit: 5n * 10n ** 18n,
      period: "day",
      token: paymentToken,
    });
    // The native entry is present even though nativeSpend was omitted —
    // without it the session cannot pay its own gas (NoSpendPermissions).
    expect(spend[1]).toEqual({
      limit: DEFAULT_NATIVE_GAS_ALLOWANCE_WEI,
      period: "day",
    });
    expect(spend[1] && "token" in spend[1]).toBe(false);
    expect(DEFAULT_NATIVE_GAS_ALLOWANCE_WEI).toBe(2n * 10n ** 16n);
  });

  it("applies address overrides, custom caps and extra calls; unknown chainId needs all five", () => {
    const commerceOverride = getAddress(`0x${"33".repeat(20)}`);
    const overridden = defaultAgentPermissions({
      chainId: 97,
      tokenSpend: { limit: 1n, period: "hour" },
      nativeSpend: { limit: 7n, period: "week" },
      addresses: { commerce: commerceOverride },
      extraCalls: [{ signature: "transfer(address,uint256)" }],
    });
    expect(overridden.calls?.[1]).toEqual({ to: commerceOverride });
    expect(overridden.calls).toHaveLength(6);
    expect(overridden.calls?.[5]).toEqual({
      signature: "transfer(address,uint256)",
    });
    expect(overridden.spend?.[0]?.period).toBe("hour");
    expect(overridden.spend?.[1]).toEqual({ limit: 7n, period: "week" });

    // Unknown chain, incomplete addresses → actionable error naming the gap.
    expect(() =>
      defaultAgentPermissions({
        chainId: 777,
        tokenSpend: { limit: 1n },
        addresses: { commerce: commerceOverride },
      }),
    ).toThrow(
      /chainId=777.*missing \[registry, router, policy, paymentToken\]/,
    );

    // Unknown chain with all five supplied works.
    const full = defaultAgentPermissions({
      chainId: 777,
      tokenSpend: { limit: 1n },
      addresses: {
        registry: getAddress(`0x${"44".repeat(20)}`),
        commerce: commerceOverride,
        router: getAddress(`0x${"55".repeat(20)}`),
        policy: getAddress(`0x${"66".repeat(20)}`),
        paymentToken: TOKEN,
      },
    });
    expect(full.calls).toHaveLength(5);
    expect(full.spend?.[0]?.token).toBe(TOKEN);
  });
});
