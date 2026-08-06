/**
 * LocalStorageProvider — file-system storage for development and testing.
 *
 * Stores deliverable JSON as local files. URLs use the `file://` scheme.
 *
 * Every path derived from caller input (an explicit `filename`, or the
 * remainder of a `file://` URL passed to `download`/`exists`) is resolved
 * and checked against the storage root *including symlink resolution*
 * before any filesystem I/O happens — see {@link resolveExisting}. A
 * string-prefix check on the un-resolved path would be bypassable by a
 * symlink planted inside the storage directory.
 *
 * Port of `python/bnbagent/storage/local_storage_provider.py`.
 */

import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";
import { bytesToHex } from "@noble/hashes/utils";
import { canonicalJson } from "../core/canonicalJson.js";
import { getEnv } from "../core/envUtil.js";
import { StorageError } from "../errors.js";
import { StorageProvider } from "./storageProvider.js";

/**
 * Resolve `path` the way `fs.realpathSync` would if every component
 * existed: symlinks are resolved for the longest existing ancestor, and any
 * remaining (not-yet-created) trailing components are appended literally.
 *
 * This mirrors Python's `Path.resolve()` (non-strict), which resolves
 * symlinks progressively for existing components without requiring the
 * full path to exist — needed because `upload()` must traversal-check a
 * path *before* the file has been written.
 */
function resolveExisting(candidate: string): string {
  const remainder: string[] = [];
  let current = candidate;
  while (!existsSync(current)) {
    const parent = dirname(current);
    if (parent === current) break; // reached filesystem root
    remainder.unshift(basename(current));
    current = parent;
  }
  const real = realpathSync(current);
  return remainder.length > 0 ? join(real, ...remainder) : real;
}

export class LocalStorageProvider extends StorageProvider {
  override readonly usesFileUrl = true;

  private readonly base: string;

  constructor(baseDir = ".agent-data") {
    super();
    this.base = baseDir;
    try {
      mkdirSync(this.base, { recursive: true });
      chmodSync(this.base, 0o700);
    } catch (e) {
      throw new StorageError(
        `Failed to create storage directory '${baseDir}': ${(e as Error).message}`,
      );
    }
  }

  static fromEnv(): LocalStorageProvider {
    return new LocalStorageProvider(
      getEnv("STORAGE_LOCAL_PATH", ".agent-data"),
    );
  }

  async upload(
    data: Record<string, unknown>,
    filename?: string,
  ): Promise<string> {
    let content: string;
    try {
      content = canonicalJson(data);
    } catch (e) {
      throw new StorageError(
        `Failed to serialize data to JSON: ${(e as Error).message}`,
      );
    }

    let fname: string;
    if (filename) {
      fname = filename.endsWith(".json") ? filename : `${filename}.json`;
    } else {
      const jobId = LocalStorageProvider.extractJobId(data);
      fname = jobId
        ? `job-${jobId}.json`
        : `${bytesToHex(StorageProvider.computeHash(data))}.json`;
    }

    const filepath = this.safeJoin(fname);
    try {
      writeFileSync(filepath, content, "utf-8");
      chmodSync(filepath, 0o600);
    } catch (e) {
      throw new StorageError(`Failed to save file: ${(e as Error).message}`);
    }
    return `file://${filepath}`;
  }

  async download(url: string): Promise<Record<string, unknown>> {
    const path = this.urlToPath(url);
    let content: string;
    try {
      content = readFileSync(path, "utf-8");
    } catch (e) {
      const err = e as NodeJS.ErrnoException;
      if (err.code === "ENOENT") {
        throw new StorageError(`File not found: ${path}`);
      }
      throw new StorageError(`Failed to read file '${path}': ${err.message}`);
    }
    try {
      return JSON.parse(content) as Record<string, unknown>;
    } catch (e) {
      throw new StorageError(
        `Invalid JSON in file '${path}': ${(e as Error).message}`,
      );
    }
  }

  async exists(url: string): Promise<boolean> {
    const path = this.urlToPath(url);
    try {
      return statSync(path).isFile();
    } catch (e) {
      const err = e as NodeJS.ErrnoException;
      if (err.code !== "ENOENT") {
        console.warn(
          `Error checking file existence for '${path}': ${err.message}`,
        );
      }
      return false;
    }
  }

  private urlToPath(url: string): string {
    const raw = url.startsWith("file://") ? url.slice(7) : url;
    return this.safeJoin(raw);
  }

  /**
   * Resolve `fname` against the storage root and verify the result cannot
   * escape it — via `../` segments, an absolute path, or a symlink planted
   * inside the storage directory.
   */
  private safeJoin(fname: string): string {
    // Mirror Python's `pathlib` `/` operator: joining an absolute path
    // discards the base entirely rather than concatenating it.
    const candidate = isAbsolute(fname)
      ? resolve(fname)
      : resolve(this.base, fname);
    const real = resolveExisting(candidate);
    const baseReal = realpathSync(this.base);
    if (real !== baseReal && !real.startsWith(baseReal + sep)) {
      throw new StorageError(
        "Path traversal blocked: path is outside storage directory",
      );
    }
    return real;
  }

  private static extractJobId(data: Record<string, unknown>): unknown {
    const job = data.job;
    if (job && typeof job === "object" && !Array.isArray(job)) {
      return (job as Record<string, unknown>).id;
    }
    return undefined;
  }
}
