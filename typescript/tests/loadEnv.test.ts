import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { loadEnv } from "../src/core/env";

/**
 * Precedence contract (Next.js semantics, both files loaded with
 * override: false):
 *
 *     real environment  >  .env.local  >  .env
 *
 * The local-first load order is the safety property: loading `.env` first
 * with override on the local file would let a stale dev `.env.local` stomp
 * a deployment-injected secret (e.g. `TWAK_WALLET_PASSWORD`).
 */

const KEY = "BNBAGENT_TEST_LOAD_ENV_KEY";

let dir: string;
let savedEnv: NodeJS.ProcessEnv;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "bnbagent-load-env-"));
  savedEnv = { ...process.env };
  delete process.env[KEY];
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  for (const key of Object.keys(process.env)) {
    if (!(key in savedEnv)) delete process.env[key];
  }
  for (const [key, value] of Object.entries(savedEnv)) {
    process.env[key] = value;
  }
});

describe("loadEnv", () => {
  it("env.local wins over .env", () => {
    writeFileSync(join(dir, ".env"), `${KEY}=a\n`);
    writeFileSync(join(dir, ".env.local"), `${KEY}=b\n`);
    const loaded = loadEnv(dir);
    // override: false + local-first: the first loader to set the key wins.
    expect(process.env[KEY]).toBe("b");
    // Returned in load order: .env.local first, then .env.
    expect(loaded).toEqual([join(dir, ".env.local"), join(dir, ".env")]);
  });

  it("real environment wins over both files", () => {
    writeFileSync(join(dir, ".env"), `${KEY}=a\n`);
    writeFileSync(join(dir, ".env.local"), `${KEY}=b\n`);
    process.env[KEY] = "c"; // deployment-injected value
    const loaded = loadEnv(dir);
    expect(process.env[KEY]).toBe("c"); // survives both files (override: false)
    expect(loaded.length).toBe(2); // the files were still loaded (for other keys)
  });

  it("env-only loads and sets", () => {
    writeFileSync(join(dir, ".env"), `${KEY}=a\n`);
    const loaded = loadEnv(dir);
    expect(process.env[KEY]).toBe("a");
    expect(loaded).toEqual([join(dir, ".env")]);
  });

  it("missing files returns empty list", () => {
    expect(loadEnv(dir)).toEqual([]);
    expect(process.env[KEY]).toBeUndefined();
  });

  it("does not search upward directories", () => {
    // A .env in the parent must NOT be picked up when loading from a child:
    // the SDK has no project-root marker, so callers anchor the root
    // explicitly.
    writeFileSync(join(dir, ".env"), `${KEY}=parent\n`);
    const child = join(dir, "child");
    mkdirSync(child);
    expect(loadEnv(child)).toEqual([]);
    expect(process.env[KEY]).toBeUndefined();
  });
});
