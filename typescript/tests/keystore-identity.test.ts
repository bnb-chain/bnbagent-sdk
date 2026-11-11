import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { EVMWalletProvider } from "../src/wallets/evmWalletProvider.js";

vi.mock("node:fs", async (importOriginal) => {
  const fs = await importOriginal<typeof import("node:fs")>();
  return { ...fs, existsSync: vi.fn(fs.existsSync) };
});

const address = `0x${"11".repeat(20)}` as const;
const fixtureUrl = new URL(
  "./fixtures/keystore-pbkdf2-interop.json",
  import.meta.url,
);

describe("keystore-identity", () => {
  it("rejects traversal selectors and valid files containing another wallet", () => {
    const dir = mkdtempSync(join(tmpdir(), "wallet-security-"));
    try {
      const fixture = readFileSync(fixtureUrl);
      writeFileSync(join(dir, `${address}.json`), fixture);
      expect(
        () =>
          new EVMWalletProvider({
            password: "test-password",
            walletsDir: dir,
            address: "../outside",
          }),
      ).toThrow(/invalid/);
      expect(
        () =>
          new EVMWalletProvider({
            password: "test-password",
            walletsDir: dir,
            address,
          }),
      ).toThrow(/identity does not match/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it.each(["explicit", "auto-select", "exists"])(
    "preserves a lowercase keystore filename on a case-sensitive filesystem (%s)",
    async (selection) => {
      const fs = await vi.importActual<typeof import("node:fs")>("node:fs");
      const dir = mkdtempSync(join(tmpdir(), "wallet-case-"));
      const fixture = readFileSync(fixtureUrl);
      const lowercaseAddress = `0x${JSON.parse(fixture.toString()).address.toLowerCase()}`;
      const filename = join(dir, `${lowercaseAddress}.json`);
      writeFileSync(filename, fixture);
      // Enforce exact file spelling even on a case-insensitive macOS volume.
      const probe = vi.mocked(existsSync).mockImplementation((path) => {
        if (
          typeof path === "string" &&
          path.startsWith(`${dir}/`) &&
          path.endsWith(".json")
        ) {
          return path === filename && fs.existsSync(path);
        }
        return fs.existsSync(path);
      });
      try {
        if (selection === "exists") {
          expect(EVMWalletProvider.keystoreExists(lowercaseAddress, dir)).toBe(
            true,
          );
        } else {
          const wallet = new EVMWalletProvider({
            password: "test-password",
            walletsDir: dir,
            ...(selection === "explicit" ? { address: lowercaseAddress } : {}),
          });
          expect(wallet.address.toLowerCase()).toBe(lowercaseAddress);
          expect(wallet.source).toBe("loaded_keystore");
        }
        expect(probe).toHaveBeenCalledWith(filename);
      } finally {
        probe.mockImplementation(fs.existsSync);
        rmSync(dir, { recursive: true, force: true });
      }
    },
  );
});
