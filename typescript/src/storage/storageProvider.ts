/**
 * StorageProvider — pluggable off-chain storage interface.
 *
 * Implementations handle upload/download of deliverable JSON. The chain
 * only stores hashes; full data lives off-chain.
 *
 * Unlike the Python SDK (which offers a `upload_sync` synchronous escape
 * hatch for non-async callers), the TypeScript SDK is async-native
 * end-to-end, so only the async `upload`/`download`/`exists` surface is
 * ported here.
 *
 * Port of `python/bnbagent/storage/storage_provider.py`.
 */

import { keccak256, toBytes } from "viem";
import { canonicalJson } from "../core/canonicalJson.js";

/**
 * Abstract base for pluggable off-chain storage.
 *
 * Built-in implementations ({@link LocalStorageProvider},
 * {@link IPFSStorageProvider}) each provide a `fromEnv()` static method that
 * reads their own env vars. Custom backends subclass this ABC and inject via
 * SDK config.
 */
export abstract class StorageProvider {
  /**
   * Set to `true` on providers whose `upload()` returns a `file://` URL.
   *
   * The SDK uses this flag at startup to require an externally reachable
   * agent URL and to know that the deliverable must be served through the
   * agent's own endpoint (instead of an externally reachable storage URL).
   */
  readonly usesFileUrl: boolean = false;

  /**
   * Upload JSON data. Returns a URL (`ipfs://...`, `file://...`,
   * `https://...`).
   *
   * Implementations MUST reject `filename` values that resolve outside
   * their storage scope and raise `StorageError`.
   */
  abstract upload(
    data: Record<string, unknown>,
    filename?: string,
  ): Promise<string>;

  /** Download and parse JSON data from a URL. */
  abstract download(url: string): Promise<Record<string, unknown>>;

  /** Check whether data at the given URL exists. */
  abstract exists(url: string): Promise<boolean>;

  /** Compute keccak256 of canonical JSON for on-chain verification. */
  static computeHash(data: Record<string, unknown>): Uint8Array {
    return keccak256(toBytes(canonicalJson(data)), "bytes");
  }

  /** Compute keccak256 of raw content string (for requestHash / responseHash). */
  static computeContentHash(content: string): Uint8Array {
    return keccak256(toBytes(content), "bytes");
  }
}
