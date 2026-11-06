import type { PublicClient } from "viem";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NonceManager } from "../src/core/nonceManager.js";

afterEach(() => {
  vi.useRealTimers();
  NonceManager._clearAll();
});
const address = `0x${"11".repeat(20)}` as const;
import { encryptKeystoreV3 } from "../src/wallets/keystore.js";
vi.mock("@noble/hashes/scrypt", () => ({
  scrypt: vi.fn(() => {
    throw new Error("KDF must not run for invalid keys");
  }),
}));

describe("keystore-key-validation", () => {
  it.each([0, 1, 33, 1024 * 1024])(
    "rejects a %s-byte key before KDF",
    (size) => {
      expect(() => encryptKeystoreV3(new Uint8Array(size), "pw")).toThrow(
        /exactly 32 bytes/,
      );
    },
  );
  it("rejects a zero scalar before KDF", () => {
    expect(() => encryptKeystoreV3(new Uint8Array(32), "pw")).toThrow(
      /private key/,
    );
  });
});
