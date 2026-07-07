/**
 * Canonical schema definitions for ERC-8183 on-chain and off-chain data
 * structures.
 *
 * Port of `python/bnbagent/erc8183/schema.py`. Two public classes:
 *
 * - `JobDescription`      — structured form of `job.description` stored on-chain.
 * - `DeliverableManifest` — structured form of the off-chain deliverable JSON
 *   whose URL is passed as `submit(optParams)`.
 *
 * Both classes are versioned. `fromDict` / `fromStr` throw on an unrecognised
 * `version` so indexers fail loudly on format changes rather than silently
 * misreading fields.
 *
 * On-chain hash contract
 * ----------------------
 * `DeliverableManifest.manifestHash()` returns the `bytes32` that the
 * provider passes to `AgenticCommerceUpgradeable.submit(jobId, deliverable,
 * optParams)`:
 *
 *     deliverable (bytes32) = keccak256(canonical manifest JSON)
 *     optParams   (bytes)   = JSON {"deliverable_url": "..."}  // retrieval pointer
 *
 * The canonical form is produced by {@link canonicalJson} — deterministic
 * across platforms and byte-identical to Python's
 * `json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))`. This
 * is a cross-SDK interop invariant: verifiers (voters, indexers) reproduce it
 * by fetching the manifest JSON and calling
 * `DeliverableManifest.fromDict(fetched).manifestHash()` from either SDK and
 * must get the same digest.
 *
 * CRITICAL: wire-format dict keys stay `snake_case` (`job_id`, `chain_id`,
 * `negotiated_at`, ...) since the JSON travels between the TypeScript and
 * Python SDKs; only the in-memory property names are camelCase.
 */

import { bytesToHex } from "viem";
import { keccakOfCanonicalJson } from "../core/canonicalJson.js";

export const SCHEMA_VERSION = 1;
const SUPPORTED_VERSIONS = new Set<number>([SCHEMA_VERSION]);

function typeName(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
  if (Array.isArray(v)) return "array";
  return typeof v;
}

function unsupportedVersionError(kind: string, version: unknown): Error {
  const supported = [...SUPPORTED_VERSIONS].sort((a, b) => a - b);
  return new Error(
    `Unsupported ${kind} version ${JSON.stringify(version)}. Supported: ${JSON.stringify(supported)}`,
  );
}

// ---------------------------------------------------------------------------
// DeliverableManifest
// ---------------------------------------------------------------------------

/** The actual delivery: `{ content, contentType }`. */
export interface DeliverableResponse {
  content: string;
  contentType: string;
}

/** Options accepted by the {@link DeliverableManifest} constructor. */
export interface DeliverableManifestOpts {
  version: number;
  jobId: number;
  chainId: number;
  contracts: Record<string, string>;
  response: DeliverableResponse;
  metadata?: Record<string, unknown>;
}

/**
 * Off-chain deliverable JSON uploaded to storage after `submit`.
 *
 * - `version`   — Schema version. Currently `1`.
 * - `jobId`     — On-chain job id.
 * - `chainId`   — EVM chain id (e.g. 97 for BSC testnet).
 * - `contracts` — Addresses of `{ commerce, router, policy }` at submit time.
 * - `response`  — `{ content, contentType }` — the actual delivery.
 * - `metadata`  — Arbitrary extra fields. Open for extensions; bump version
 *   when a field becomes required.
 */
export class DeliverableManifest {
  readonly version: number;
  readonly jobId: number;
  readonly chainId: number;
  readonly contracts: Record<string, string>;
  readonly response: DeliverableResponse;
  readonly metadata: Record<string, unknown>;

  constructor(opts: DeliverableManifestOpts) {
    this.version = opts.version;
    this.jobId = opts.jobId;
    this.chainId = opts.chainId;
    this.contracts = opts.contracts;
    this.response = opts.response;
    this.metadata = opts.metadata ?? {};
  }

  // ---------------------------------------------------------------- hash

  /**
   * Return `keccak256(canonical manifest JSON)` as a 32-byte hex string.
   *
   * This is the `deliverable` bytes32 passed to
   * `AgenticCommerceUpgradeable.submit`. Verifiers reproduce it by fetching
   * the manifest from the URL in `optParams` and calling
   * `DeliverableManifest.fromDict(fetched).manifestHash()`.
   */
  manifestHash(): `0x${string}` {
    return keccakOfCanonicalJson(this.toDict());
  }

  /** Return `true` if `onChainHash` matches `manifestHash()`. */
  verify(onChainHash: `0x${string}` | Uint8Array): boolean {
    const hash = this.manifestHash();
    const onChainHex =
      typeof onChainHash === "string" ? onChainHash : bytesToHex(onChainHash);
    return hash.toLowerCase() === onChainHex.toLowerCase();
  }

  // ------------------------------------------------------------ serialisation

  toDict(): Record<string, unknown> {
    return {
      version: this.version,
      job_id: this.jobId,
      chain_id: this.chainId,
      contracts: this.contracts,
      response: {
        content: this.response.content,
        content_type: this.response.contentType,
      },
      metadata: this.metadata,
    };
  }

  static fromDict(d: Record<string, unknown>): DeliverableManifest {
    const version = d.version as number;
    if (!SUPPORTED_VERSIONS.has(version)) {
      throw unsupportedVersionError("DeliverableManifest", version);
    }
    const response = d.response as Record<string, unknown> | undefined;
    if (!response || typeof response !== "object" || !("content" in response)) {
      throw new Error("DeliverableManifest.response must contain 'content'");
    }
    for (const field of ["job_id", "chain_id", "contracts"]) {
      if (!(field in d)) {
        throw new Error(
          `DeliverableManifest missing required field: '${field}'`,
        );
      }
    }
    return new DeliverableManifest({
      version,
      jobId: d.job_id as number,
      chainId: d.chain_id as number,
      contracts: d.contracts as Record<string, string>,
      response: {
        content: response.content as string,
        contentType: (response.content_type as string) ?? "",
      },
      metadata: (d.metadata as Record<string, unknown>) ?? {},
    });
  }
}

// ---------------------------------------------------------------------------
// JobDescription
// ---------------------------------------------------------------------------

/** Options accepted by the {@link JobDescription} constructor. */
export interface JobDescriptionOpts {
  version: number;
  negotiatedAt: number;
  task: string;
  terms: Record<string, unknown>;
  price: string;
  currency: string;
  quoteExpiresAt?: number | null;
  negotiationHash?: string | null;
  providerSig?: string | null;
}

/**
 * Structured form of `job.description` stored on-chain at `createJob`.
 *
 * Built by `bnbagent.erc8183.negotiation.buildJobDescription` (Python) and
 * parsed back by `JobDescription.fromStr`.
 *
 * - `version`         — Schema version. Currently `1`.
 * - `negotiatedAt`    — Unix timestamp when negotiation completed.
 * - `task`            — Human-readable task description (sanitised for on-chain).
 * - `terms`           — `{ deliverables, qualityStandards, successCriteria? }`
 *   (Python keeps these keys as-provided; not remapped here).
 * - `price`           — Agreed price in token smallest unit (string to avoid overflow).
 * - `currency`        — Payment token address.
 * - `quoteExpiresAt`  — Optional quote expiry timestamp.
 * - `negotiationHash` — Optional keccak256 of canonical negotiation content (0x-prefixed).
 * - `providerSig`     — Optional EIP-191 provider signature over `negotiationHash`.
 */
export class JobDescription {
  readonly version: number;
  readonly negotiatedAt: number;
  readonly task: string;
  readonly terms: Record<string, unknown>;
  readonly price: string;
  readonly currency: string;
  readonly quoteExpiresAt: number | null;
  readonly negotiationHash: string | null;
  readonly providerSig: string | null;

  constructor(opts: JobDescriptionOpts) {
    this.version = opts.version;
    this.negotiatedAt = opts.negotiatedAt;
    this.task = opts.task;
    this.terms = opts.terms;
    this.price = opts.price;
    this.currency = opts.currency;
    this.quoteExpiresAt = opts.quoteExpiresAt ?? null;
    this.negotiationHash = opts.negotiationHash ?? null;
    this.providerSig = opts.providerSig ?? null;
  }

  // ------------------------------------------------------------ serialisation

  toDict(): Record<string, unknown> {
    const d: Record<string, unknown> = {
      version: this.version,
      negotiated_at: this.negotiatedAt,
      task: this.task,
      terms: this.terms,
      price: this.price,
      currency: this.currency,
    };
    if (this.quoteExpiresAt !== null) {
      d.quote_expires_at = this.quoteExpiresAt;
    }
    if (this.negotiationHash !== null) {
      d.negotiation_hash = this.negotiationHash;
    }
    if (this.providerSig !== null) {
      d.provider_sig = this.providerSig;
    }
    return d;
  }

  static fromDict(d: Record<string, unknown>): JobDescription {
    const version = d.version as number;
    if (!SUPPORTED_VERSIONS.has(version)) {
      throw unsupportedVersionError("JobDescription", version);
    }

    const negotiatedAt = d.negotiated_at;
    if (typeof negotiatedAt !== "number" || !Number.isInteger(negotiatedAt)) {
      throw new Error(
        `negotiated_at must be int, got ${typeName(negotiatedAt)}`,
      );
    }

    const quoteExpiresAtRaw = d.quote_expires_at;
    let quoteExpiresAt: number | null = null;
    if (quoteExpiresAtRaw !== undefined && quoteExpiresAtRaw !== null) {
      if (
        typeof quoteExpiresAtRaw !== "number" ||
        !Number.isInteger(quoteExpiresAtRaw)
      ) {
        throw new Error(
          `quote_expires_at must be int or null, got ${typeName(quoteExpiresAtRaw)}`,
        );
      }
      quoteExpiresAt = quoteExpiresAtRaw;
    }

    return new JobDescription({
      version,
      negotiatedAt,
      task: d.task as string,
      terms: d.terms as Record<string, unknown>,
      price: d.price as string,
      currency: d.currency as string,
      quoteExpiresAt,
      negotiationHash: (d.negotiation_hash as string | undefined) ?? null,
      providerSig: (d.provider_sig as string | undefined) ?? null,
    });
  }

  /**
   * Parse a `job.description` string.
   *
   * Returns `null` for plain-text descriptions (legacy or unstructured).
   * Returns `null` if the JSON has no `version` field.
   * Throws if the version is present but unsupported.
   */
  static fromStr(description: string): JobDescription | null {
    if (!description || !description.trim().startsWith("{")) {
      return null;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(description);
    } catch {
      return null;
    }
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed) ||
      !("version" in parsed)
    ) {
      return null;
    }
    return JobDescription.fromDict(parsed as Record<string, unknown>);
  }
}
