/**
 * Tests for LocalStorageProvider — filesystem storage.
 *
 * Ports `python/tests/test_local_storage.py`. `test_upload_sync` and its
 * traversal variant are skipped — the TS SDK is async-native and has no
 * `upload_sync` escape hatch.
 */

import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  statSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { StorageError } from "../src/errors";
import { LocalStorageProvider } from "../src/storage/localStorageProvider";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "bnbagent-local-storage-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("LocalStorageProvider", () => {
  it("creates the storage directory", () => {
    const base = join(root, "storage");
    new LocalStorageProvider(base);
    expect(existsSync(base)).toBe(true);
  });

  it("sets directory permissions to owner-only (0700)", () => {
    const base = join(root, "storage");
    new LocalStorageProvider(base);
    const mode = statSync(base).mode & 0o777;
    expect(mode).toBe(0o700);
  });

  it("upload returns a file:// URL", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const url = await provider.upload({ key: "value" });
    expect(url.startsWith("file://")).toBe(true);
  });

  it("upload with filename includes it in the URL", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const url = await provider.upload({ test: 1 }, "myfile.json");
    expect(url).toContain("myfile.json");
  });

  it("upload without filename uses job.id", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const url = await provider.upload({ job: { id: 42 } });
    expect(url).toContain("job-42.json");
  });

  it("upload without filename or job.id uses the content hash", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const url = await provider.upload({ random: "data" });
    expect(url.endsWith(".json")).toBe(true);
  });

  it("sets file permissions to owner read/write only (0600)", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    await provider.upload({ key: "val" }, "test.json");
    const filepath = join(root, "data", "test.json");
    const mode = statSync(filepath).mode & 0o777;
    expect(mode).toBe(0o600);
  });

  it("round-trips data through upload/download", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const original = { download: "test" };
    const url = await provider.upload(original, "dl.json");
    const result = await provider.download(url);
    expect(result.download).toBe("test");
  });

  it("download of a missing file raises StorageError", async () => {
    const base = join(root, "data");
    const provider = new LocalStorageProvider(base);
    const missing = join(base, "nonexistent.json");
    await expect(provider.download(`file://${missing}`)).rejects.toThrow(
      /not found/,
    );
    await expect(provider.download(`file://${missing}`)).rejects.toThrow(
      StorageError,
    );
  });

  it("exists returns true for an uploaded file", async () => {
    const provider = new LocalStorageProvider(join(root, "data"));
    const url = await provider.upload({ exists: true }, "check.json");
    expect(await provider.exists(url)).toBe(true);
  });

  it("exists returns false for a missing file", async () => {
    const base = join(root, "data");
    const provider = new LocalStorageProvider(base);
    const missing = join(base, "nosuch.json");
    expect(await provider.exists(`file://${missing}`)).toBe(false);
  });

  describe("path traversal guard", () => {
    it("blocks a relative ../ escape on upload", async () => {
      const provider = new LocalStorageProvider(join(root, "data"));
      await expect(
        provider.upload({ k: "v" }, "../escape.json"),
      ).rejects.toThrow(/Path traversal blocked/);
      expect(existsSync(join(root, "escape.json"))).toBe(false);
    });

    it("blocks an absolute path escape on upload", async () => {
      const provider = new LocalStorageProvider(join(root, "data"));
      const outside = join(root, "outside.json");
      await expect(provider.upload({ k: "v" }, outside)).rejects.toThrow(
        /Path traversal blocked/,
      );
      expect(existsSync(outside)).toBe(false);
    });

    it("blocks a nested a/../../ escape on upload", async () => {
      const provider = new LocalStorageProvider(join(root, "data"));
      await expect(
        provider.upload({ k: "v" }, "a/../../escape.json"),
      ).rejects.toThrow(/Path traversal blocked/);
      expect(existsSync(join(root, "escape.json"))).toBe(false);
    });

    it("blocks a symlink escape inside the base dir", async () => {
      const base = join(root, "data");
      const outside = join(root, "outside");
      const provider = new LocalStorageProvider(base);
      // Directory must exist before the provider is constructed so the
      // symlink target pre-exists (mirrors the Python fixture ordering).
      mkdirSync(outside);
      symlinkSync(outside, join(base, "link"), "dir");

      await expect(
        provider.upload({ k: "v" }, "link/escape.json"),
      ).rejects.toThrow(/Path traversal blocked/);
      expect(existsSync(join(outside, "escape.json"))).toBe(false);
    });

    it("blocks download of an absolute path outside the base dir", async () => {
      const provider = new LocalStorageProvider(join(root, "data"));
      await expect(provider.download("file:///etc/passwd")).rejects.toThrow(
        /Path traversal blocked/,
      );
    });

    it("blocks exists() for an absolute path outside the base dir", async () => {
      const provider = new LocalStorageProvider(join(root, "data"));
      await expect(provider.exists("file:///etc/passwd")).rejects.toThrow(
        /Path traversal blocked/,
      );
    });
  });
});
