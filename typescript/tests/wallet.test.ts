/**
 * Tests for EVMWalletProvider (~/.bnbagent/wallets/ keystore).
 *
 * Ports `python/tests/test_wallet.py`, plus the cross-SDK keystore interop
 * invariant (a keystore produced by Python's `eth_account.Account.encrypt`
 * MUST decrypt in TS) which has no Python-side analog to port from.
 */

import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  hexToBytes as fromHexString,
  bytesToHex as toHexString,
} from "@noble/hashes/utils";
import { type Hex, recoverMessageAddress, recoverTypedDataAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { PolicyViolation, SigningPolicy } from "../src/signing/index.js";
import {
  EVMWalletProvider,
  type KeystoreV3,
  decryptKeystoreV3,
  encryptKeystoreV3,
} from "../src/wallets/index.js";

const PW = "test-secure-password-123";
const PK = `0x${"a".repeat(64)}` as Hex;

const FIXTURE_PATH = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "keystore-interop.json",
);

let wdir: string;

beforeEach(() => {
  const root = mkdtempSync(join(tmpdir(), "bnbagent-wallet-test-"));
  wdir = join(root, "wallets");
});

afterEach(() => {
  rmSync(wdir, { recursive: true, force: true });
});

describe("EVMWalletProvider — creation & import", () => {
  it("creates a new wallet, writes a keystore file, source=created_new", () => {
    const wallet = new EVMWalletProvider({ password: PW, walletsDir: wdir });
    expect(wallet.address.startsWith("0x")).toBe(true);
    expect(wallet.address.length).toBe(42);
    expect(wallet.source).toBe("created_new");
    expect(existsSync(join(wdir, `${wallet.address}.json`))).toBe(true);
  });

  it("imports a private key, source=imported", () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const expected = privateKeyToAccount(PK).address;
    expect(wallet.address).toBe(expected);
    expect(wallet.source).toBe("imported");
    expect(existsSync(join(wdir, `${expected}.json`))).toBe(true);
  });

  it("writes the wallets dir 0700 and the keystore file 0600 (owner-only)", () => {
    // Windows chmod semantics don't map onto POSIX bits the same way; this
    // invariant only matters (and is only enforced) on POSIX.
    if (process.platform === "win32") return;
    const wallet = new EVMWalletProvider({ password: PW, walletsDir: wdir });
    const dirMode = statSync(wdir).mode & 0o777;
    const fileMode =
      statSync(join(wdir, `${wallet.address}.json`)).mode & 0o777;
    expect(dirMode).toBe(0o700);
    expect(fileMode).toBe(0o600);
  });

  it("imports a private key without 0x prefix", () => {
    const raw = "a".repeat(64);
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: raw,
      walletsDir: wdir,
    });
    const expected = privateKeyToAccount(`0x${raw}` as Hex).address;
    expect(wallet.address).toBe(expected);
  });

  it("rejects an invalid private key", () => {
    expect(
      () =>
        new EVMWalletProvider({
          password: PW,
          privateKey: "invalid-key",
          walletsDir: wdir,
        }),
    ).toThrow(/Invalid private key/);
  });

  it("requires a non-empty password", () => {
    expect(
      () => new EVMWalletProvider({ password: "", walletsDir: wdir }),
    ).toThrow(/Password is required/);
  });

  it("requires private_key when persist=false", () => {
    expect(
      () => new EVMWalletProvider({ password: PW, persist: false }),
    ).toThrow(/private_key is required/);
  });
});

describe("EVMWalletProvider — load from keystore", () => {
  it("loads the existing single wallet when constructed without private_key/address", () => {
    const w1 = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const w2 = new EVMWalletProvider({ password: PW, walletsDir: wdir });
    expect(w2.address).toBe(w1.address);
    expect(w2.source).toBe("loaded_keystore");
  });

  it("loads by explicit address", () => {
    const w1 = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const w2 = new EVMWalletProvider({
      password: PW,
      address: w1.address,
      walletsDir: wdir,
    });
    expect(w2.address).toBe(w1.address);
  });

  it("fails with the wrong password", () => {
    new EVMWalletProvider({
      password: "correct",
      privateKey: PK,
      walletsDir: wdir,
    });
    expect(
      () => new EVMWalletProvider({ password: "wrong", walletsDir: wdir }),
    ).toThrow(/wrong password/);
  });

  it("requires an explicit address when multiple wallets exist", () => {
    new EVMWalletProvider({
      password: PW,
      privateKey: `0x${"a".repeat(64)}`,
      walletsDir: wdir,
    });
    new EVMWalletProvider({
      password: PW,
      privateKey: `0x${"b".repeat(64)}`,
      walletsDir: wdir,
    });
    expect(
      () => new EVMWalletProvider({ password: PW, walletsDir: wdir }),
    ).toThrow(/Multiple wallets/);
  });

  it("fails when the requested address has no keystore", () => {
    mkdirSync(wdir, { recursive: true });
    expect(
      () =>
        new EVMWalletProvider({
          password: PW,
          address: "0xdead",
          walletsDir: wdir,
        }),
    ).toThrow(/Keystore not found/);
  });
});

describe("EVMWalletProvider — static helpers", () => {
  it("keystoreExists reflects presence per-address and overall", () => {
    expect(EVMWalletProvider.keystoreExists(undefined, wdir)).toBe(false);
    const w = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    expect(EVMWalletProvider.keystoreExists(undefined, wdir)).toBe(true);
    expect(EVMWalletProvider.keystoreExists(w.address, wdir)).toBe(true);
    expect(EVMWalletProvider.keystoreExists("0xdead", wdir)).toBe(false);
  });

  it("listWallets returns checksummed addresses", () => {
    expect(EVMWalletProvider.listWallets(wdir)).toEqual([]);
    const w1 = new EVMWalletProvider({
      password: PW,
      privateKey: `0x${"a".repeat(64)}`,
      walletsDir: wdir,
    });
    expect(EVMWalletProvider.listWallets(wdir)).toEqual([w1.address]);
  });
});

describe("EVMWalletProvider — signing", () => {
  it("signTransaction returns rawTransaction + hash", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const signed = await wallet.signTransaction({
      to: `0x${"b".repeat(40)}`,
      value: 10n ** 18n,
      gas: 21000n,
      gasPrice: 20_000_000_000n,
      nonce: 0,
      chainId: 97,
    } as never);
    expect(signed.rawTransaction.startsWith("0x")).toBe(true);
    expect(signed.hash.startsWith("0x")).toBe(true);
  });

  it("signMessage round-trips via recoverMessageAddress", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const signed = await wallet.signMessage("Hello, World!");
    expect(signed.messageHash.startsWith("0x")).toBe(true);
    expect(signed.signature.startsWith("0x")).toBe(true);
    const recovered = await recoverMessageAddress({
      message: "Hello, World!",
      signature: signed.signature,
    });
    expect(recovered).toBe(wallet.address);
  });

  it("signTypedData round-trips via recoverTypedDataAddress (permissive policy)", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
      signingPolicy: SigningPolicy.permissive(),
    });
    const domain = {
      name: "United Stables",
      version: "1",
      chainId: 56,
      verifyingContract: "0xcE24439F2D9C6a2289F741120FE202248B666666",
    } as const;
    const types = {
      EIP712Domain: [
        { name: "name", type: "string" },
        { name: "version", type: "string" },
        { name: "chainId", type: "uint256" },
        { name: "verifyingContract", type: "address" },
      ],
      TransferWithAuthorization: [
        { name: "from", type: "address" },
        { name: "to", type: "address" },
        { name: "value", type: "uint256" },
        { name: "validAfter", type: "uint256" },
        { name: "validBefore", type: "uint256" },
        { name: "nonce", type: "bytes32" },
      ],
    };
    const message = {
      from: wallet.address,
      to: `0x${"b".repeat(40)}`,
      value: 1_000_000n,
      validAfter: 0n,
      validBefore: 2_000_000_000n,
      nonce: `0x${"c".repeat(64)}`,
    };
    const signed = await wallet.signTypedData(domain, types, message);
    expect(signed.messageHash.startsWith("0x")).toBe(true);
    expect(signed.signature.startsWith("0x")).toBe(true);

    const recovered = await recoverTypedDataAddress({
      domain,
      types: { TransferWithAuthorization: types.TransferWithAuthorization },
      primaryType: "TransferWithAuthorization",
      message,
      signature: signed.signature,
    });
    expect(recovered).toBe(wallet.address);
  });

  it("produces an identical signature whether or not EIP712Domain is included in types", async () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
      signingPolicy: SigningPolicy.permissive(),
    });
    const domain = {
      name: "Test",
      version: "1",
      chainId: 56,
      verifyingContract: `0x${"1".repeat(40)}`,
    } as const;
    const typesWithDomain = {
      EIP712Domain: [
        { name: "name", type: "string" },
        { name: "version", type: "string" },
        { name: "chainId", type: "uint256" },
        { name: "verifyingContract", type: "address" },
      ],
      Mail: [{ name: "contents", type: "string" }],
    };
    const typesWithoutDomain = { Mail: [{ name: "contents", type: "string" }] };
    const msg = { contents: "hello" };
    const sig1 = await wallet.signTypedData(domain, typesWithDomain, msg);
    const sig2 = await wallet.signTypedData(domain, typesWithoutDomain, msg);
    expect(sig1.signature).toBe(sig2.signature);
  });
});

describe("EVMWalletProvider — export", () => {
  it("exportPrivateKey returns 0x + 64 hex chars", () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const exported = wallet.exportPrivateKey();
    expect(exported.startsWith("0x")).toBe(true);
    expect(exported.length).toBe(66);
    expect(exported.toLowerCase()).toBe(PK.toLowerCase());
  });

  it("exportKeystore round-trips through decryptKeystoreV3", () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const ks = wallet.exportKeystore();
    expect(ks.version).toBe(3);
    expect(ks.crypto).toBeDefined();
    const recoveredKey = decryptKeystoreV3(ks, PW);
    const recovered = privateKeyToAccount(`0x${toHexString(recoveredKey)}`);
    expect(recovered.address).toBe(wallet.address);
  });
});

describe("EVMWalletProvider — in-memory only", () => {
  it("persist=false signs but never touches disk", () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      persist: false,
      walletsDir: wdir,
    });
    expect(wallet.address).toBe(privateKeyToAccount(PK).address);
    expect(existsSync(wdir)).toBe(false);
    expect(wallet.exists()).toBe(false);
    expect(wallet.keyLocation).toBe("in-memory (not persisted)");
  });
});

describe("EVMWalletProvider — capabilities", () => {
  it("declares sign.*, calls.arbitrary and paymaster.sponsor", () => {
    const wallet = new EVMWalletProvider({
      password: PW,
      privateKey: PK,
      walletsDir: wdir,
    });
    const caps = wallet.capabilities();
    expect(caps.has("sign.message")).toBe(true);
    expect(caps.has("sign.transaction")).toBe(true);
    expect(caps.has("sign.typed_data")).toBe(true);
    expect(caps.has("calls.arbitrary")).toBe(true);
    expect(caps.has("paymaster.sponsor")).toBe(true);
  });
});

// ── Cross-SDK keystore interop (THE headline invariant) ────────────────────

describe("keystore interop — Python eth_account -> TS", () => {
  it("decrypts a keystore produced by eth_account.Account.encrypt, recovering 0xab..ab", () => {
    const fixture = JSON.parse(
      readFileSync(FIXTURE_PATH, "utf8"),
    ) as KeystoreV3;
    const recovered = decryptKeystoreV3(fixture, "test-password");
    expect(toHexString(recovered)).toBe("ab".repeat(32));
  });

  it("throws the documented MAC-mismatch error on the wrong password", () => {
    const fixture = JSON.parse(
      readFileSync(FIXTURE_PATH, "utf8"),
    ) as KeystoreV3;
    expect(() => decryptKeystoreV3(fixture, "wrong-password")).toThrow(
      "Failed to decrypt keystore (wrong password?): MAC mismatch",
    );
  });
});

describe("keystore round-trip — encryptKeystoreV3 / decryptKeystoreV3", () => {
  it("recovers the original private key", () => {
    const pk = fromHexString("cd".repeat(32));
    const ks = encryptKeystoreV3(pk, "some-password");
    expect(ks.version).toBe(3);
    expect(ks.crypto.kdf).toBe("scrypt");
    const recovered = decryptKeystoreV3(ks, "some-password");
    expect(toHexString(recovered)).toBe(toHexString(pk));
  });

  it("rejects the wrong password with a MAC mismatch", () => {
    const pk = fromHexString("cd".repeat(32));
    const ks = encryptKeystoreV3(pk, "some-password");
    expect(() => decryptKeystoreV3(ks, "not-the-password")).toThrow(
      "Failed to decrypt keystore (wrong password?): MAC mismatch",
    );
  });
});
