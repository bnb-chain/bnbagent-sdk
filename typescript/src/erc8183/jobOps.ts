/**
 * `ERC8183JobOps` — headless job-lifecycle operations for a provider agent,
 * plus `fundedJobWatcher`, a signer-free polling loop that detects newly
 * funded jobs.
 *
 * Every write path (`submitResult`) and read path (`getJob`, `getResponse`,
 * `verifyJob`, `getPendingJobs`, `getSubmittedJobs`) returns a plain result
 * object instead of throwing, so a serving layer (HTTP handler, CLI, cron
 * job) can map `error_code` to its own protocol without unwrapping
 * exceptions. `error_code` values are stable, transport-neutral strings —
 * this module has no transport of its own.
 *
 * Retry contract: an error envelope carries `retryable: true` only for
 * transient failures (`chain_unavailable`, `internal_error`); its absence
 * means the failure is permanent and retrying cannot succeed.
 * `TransactionPendingError` is the one exception: it is NOT retryable (the
 * write already broadcast; a blind retry risks a double-broadcast) but
 * carries `tx_hash` so the caller can check later.
 *
 * Port of `python/bnbagent/erc8183/job_ops.py`.
 */

import { getAddress } from "viem";
import type { NetworkConfig } from "../config.js";
import { getEnv } from "../core/envUtil.js";
import { describeError } from "../core/txSender.js";
import { RpcRangeLimitError, TransactionPendingError } from "../errors.js";
import { LocalStorageProvider } from "../storage/localStorageProvider.js";
import type { StorageProvider } from "../storage/storageProvider.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { ERC8183Client } from "./client.js";
import { ERC8183_ENV_PREFIX } from "./constants.js";
import { parseJobDescription } from "./negotiation.js";
import { DeliverableManifest, SCHEMA_VERSION } from "./schema.js";
import { JobStatus } from "./types.js";

const DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024; // 5 MB
const DEFAULT_MAX_METADATA_BYTES = 256 * 1024; // 256 KB

/** Strict decimal-integer literal (optional sign, digits only) — mirrors
 * what Python's `int(str)` accepts, rejecting forms `Number()` would
 * otherwise silently coerce (`"1e3"`, `"0x10"`, `"5.0"`, `""`). */
const INT_LITERAL_RE = /^[+-]?\d+$/;

function readIntEnv(key: string, defaultValue: number): number {
  const raw = getEnv(key, undefined, ERC8183_ENV_PREFIX);
  if (raw === undefined) {
    return defaultValue;
  }
  const trimmed = raw.trim();
  const value = Number(trimmed);
  if (
    !INT_LITERAL_RE.test(trimmed) ||
    !Number.isSafeInteger(value) ||
    value <= 0
  ) {
    console.warn(
      `[ERC8183JobOps] ${ERC8183_ENV_PREFIX}${key}=${JSON.stringify(raw)} invalid, using default ${defaultValue}`,
    );
    return defaultValue;
  }
  return value;
}

function maxResponseBytes(): number {
  return readIntEnv("MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES);
}

function maxMetadataBytes(): number {
  return readIntEnv("MAX_METADATA_BYTES", DEFAULT_MAX_METADATA_BYTES);
}

/** Plain exception message — mirrors Python's `str(exc)` (the top-level
 * message only; unlike {@link describeError}, this does NOT walk a `.cause`
 * chain). Used wherever this module classifies/redacts an exception's text,
 * so behavior matches the Python reference exactly. `describeError` is still
 * used for the informational console logging elsewhere in this module. */
function plainMessage(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

/** Narrow keyword list used by `getJob`/`verifyJob`'s inline net-error
 * classification (kept separate from {@link TRANSIENT_ERROR_KEYWORDS} —
 * mirrors Python's two independent lists). */
const NET_ERR_KEYWORDS = ["timeout", "connection", "network", "rpc"];

/**
 * Depth-first walk of `exc` and its `.cause` chain (cycle-guarded, mirroring
 * `isOpaqueRevert` in `txSender.ts`) looking for the first numeric `.code`
 * property.
 *
 * Covers two shapes that both occur in production:
 *  - a raw RPC-shaped payload thrown directly, `{ code, message }`
 *    (e.g. web3.py-style `ValueError`s ported as plain objects); and
 *  - a viem `Error` subclass with `.code` set on the instance itself, or
 *    nested several `.cause` levels deep — viem wraps RPC failures as
 *    `ContractFunctionExecutionError -> ... -> RpcRequestError`, and only
 *    the innermost `RpcRequestError` carries the numeric JSON-RPC code.
 */
function findRpcErrorCode(exc: unknown): number | undefined {
  let current: unknown = exc;
  const seen = new Set<unknown>();
  while (
    current !== null &&
    typeof current === "object" &&
    !seen.has(current)
  ) {
    seen.add(current);
    const code = (current as { code?: unknown }).code;
    if (typeof code === "number") {
      return code;
    }
    current = (current as { cause?: unknown }).cause;
  }
  return undefined;
}

/** Full transient-keyword list used by {@link excErrorFields}. */
const TRANSIENT_ERROR_KEYWORDS = [
  "timeout",
  "connection",
  "network",
  "rpc",
  "429",
  "too many requests",
  "rate limit",
  "limit exceeded",
];

// ── Semantic error codes (transport-neutral) ──
//
// `error_code` values are stable machine-readable strings, NOT HTTP status
// codes — this module has no transport. A serving layer maps them to its
// own protocol's rejection.
export const ERR_BUDGET_TOO_LOW = "budget_too_low"; // budget < service_price
export const ERR_NOT_ASSIGNED = "not_assigned"; // job.provider != this agent
export const ERR_NOT_FOUND = "not_found"; // job / stored response missing
export const ERR_JOB_EXPIRED = "job_expired"; // past job.expiredAt
export const ERR_WRONG_STATUS = "wrong_status"; // job not in the required status
export const ERR_DESCRIPTION_INVALID = "description_invalid"; // malformed description (fail closed)
export const ERR_SUBMIT_DEADLINE_PASSED = "submit_deadline_passed"; // past expiredAt - disputeWindow
export const ERR_PAYLOAD_TOO_LARGE = "payload_too_large"; // response/metadata size cap hit
export const ERR_METADATA_INVALID = "metadata_invalid"; // metadata not JSON-serializable (e.g. a bigint) — permanent
export const ERR_INTERNAL = "internal_error"; // unexpected failure (retryable)
export const ERR_CHAIN_UNAVAILABLE = "chain_unavailable"; // transient chain/RPC trouble (retryable)
export const ERR_TX_PENDING = "tx_pending"; // tx broadcast but unconfirmed (NOT retryable)

/**
 * Safe `{ error, error_code }` fields for an exception.
 *
 * Transport errors are replaced by a generic message and any URL-shaped
 * token is redacted (RPC endpoints embed API keys in the path); other
 * messages (e.g. revert reasons) pass through. `error_code` is
 * `chain_unavailable` for transient chain/RPC trouble, `internal_error`
 * otherwise — both retryable. A raw JSON-RPC error code (e.g. `-32005`),
 * when present, rides along separately as `rpc_error_code` — never mixed
 * into `error_code`.
 *
 * A {@link TransactionPendingError} is reported as `tx_pending` and is NOT
 * retryable: the write was already broadcast, so a blind retry would risk a
 * double-broadcast — the caller should check `tx_hash` later.
 */
export function excErrorFields(exc: unknown): Record<string, unknown> {
  if (exc instanceof TransactionPendingError) {
    return {
      error: exc.message,
      error_code: ERR_TX_PENDING,
      retryable: false,
      tx_hash: exc.txHash,
    };
  }

  let message: string;

  if (
    exc !== null &&
    typeof exc === "object" &&
    !(exc instanceof Error) &&
    "message" in exc
  ) {
    // A raw RPC-shaped payload: `{ code, message }` thrown directly rather
    // than wrapped in an Error instance — surface the inner message instead
    // of a dict-repr / "[object Object]".
    const payload = exc as { code?: unknown; message: unknown };
    message = String(payload.message);
  } else {
    // Mirrors Python's `else: message = str(exc)`.
    message = plainMessage(exc);
  }

  // Unlike `message`, `rpc_error_code` extraction is NOT limited to the
  // plain-object shape above: viem's `RpcRequestError` sets `.code` on an
  // `Error` instance (often nested in `.cause`), and `callWithRetry`
  // rethrows it unmodified — that is how every real RPC failure actually
  // arrives here.
  const rpcCode = findRpcErrorCode(exc);

  const lower = message.toLowerCase();
  let fields: Record<string, unknown>;
  if (TRANSIENT_ERROR_KEYWORDS.some((k) => lower.includes(k))) {
    fields = {
      error: "Temporary chain/RPC error",
      error_code: ERR_CHAIN_UNAVAILABLE,
      retryable: true,
    };
  } else {
    const redacted = message.replace(/\S+:\/\/\S+/g, "<redacted>");
    fields = { error: redacted, error_code: ERR_INTERNAL, retryable: true };
  }
  if (rpcCode !== undefined) {
    fields.rpc_error_code = rpcCode;
  }
  return fields;
}

/** Generic result bag returned by every `ERC8183JobOps` method. The
 * error-envelope wire keys stay `snake_case` (`error_code`, `tx_hash`,
 * `retryable`, `rpc_error_code`) so the failure shape survives JSON transport
 * unchanged; everything else is open-ended (`jobs`, `job`, `warnings`,
 * `deliverable`, ...).
 *
 * Note the two distinct tx-hash fields, on mutually exclusive branches:
 * `txHash` (camelCase, the declared member below) is the SUCCESS-path field,
 * set by `submitResult` on a successful `submit`. `tx_hash` (snake_case) is
 * the error-envelope field `excErrorFields` attaches on a
 * `TransactionPendingError` FAILURE; it is not a declared member and rides
 * the index signature. Don't collapse them — they carry different semantics
 * on different paths. */
export interface OpResult {
  success?: boolean;
  valid?: boolean;
  error?: string;
  error_code?: string;
  retryable?: boolean;
  txHash?: string;
  [key: string]: unknown;
}

/** Options accepted by {@link ERC8183JobOps.create}. */
export interface ERC8183JobOpsCreateOpts {
  /** Provider signing material. Required for `submitResult`; omit for a
   * read/poll-only instance built from `providerAddress`. */
  walletProvider?: WalletProvider | null;
  /** Preset name or a `NetworkConfig` for custom deployments. Defaults to
   * `"bsc-testnet"`. */
  network?: string | NetworkConfig;
  /** Agent address for a keyless read/poll-only instance. Required when
   * `walletProvider` is omitted. */
  providerAddress?: string;
  /** Optional off-chain storage for deliverable payloads. */
  storageProvider?: StorageProvider | null;
  /** Minimum acceptable budget in token raw units. Used by `verifyJob` to
   * reject under-priced jobs (`budget_too_low`). */
  servicePrice?: bigint;
  /** Public base URL of this agent (required when storage returns a
   * `file://` URL). */
  agentUrl?: string | null;
}

/**
 * Headless job-lifecycle operations for a provider agent.
 *
 * Construct via the async {@link ERC8183JobOps.create} factory (keeps the
 * async-factory convention shared with {@link ERC8183Client}, even though
 * construction itself performs no I/O — the underlying `ERC8183Client` is
 * built lazily on first use).
 */
export class ERC8183JobOps {
  private readonly walletProvider: WalletProvider | null;
  private readonly agentAddressValue: `0x${string}`;
  private readonly network: string | NetworkConfig;
  private readonly storage: StorageProvider | null;
  private readonly servicePrice: bigint;
  private readonly agentUrl: string | null;

  private client: ERC8183Client | null = null;
  private readonly deliverableUrls = new Map<number, string>();
  private lastKnownCounter = 0;
  private startupScanDone = false;
  private readonly pendingOpenIds = new Set<number>();

  private constructor(opts: {
    walletProvider: WalletProvider | null;
    agentAddress: `0x${string}`;
    network: string | NetworkConfig;
    storage: StorageProvider | null;
    servicePrice: bigint;
    agentUrl: string | null;
  }) {
    this.walletProvider = opts.walletProvider;
    this.agentAddressValue = opts.agentAddress;
    this.network = opts.network;
    this.storage = opts.storage;
    this.servicePrice = opts.servicePrice;
    this.agentUrl = opts.agentUrl;
  }

  static async create(
    opts: ERC8183JobOpsCreateOpts = {},
  ): Promise<ERC8183JobOps> {
    const walletProvider = opts.walletProvider ?? null;
    if (walletProvider === null && !opts.providerAddress) {
      throw new Error(
        "ERC8183JobOps needs a walletProvider (to sign) or a providerAddress (read/poll-only)",
      );
    }
    const agentAddress =
      walletProvider !== null
        ? walletProvider.address
        : getAddress(opts.providerAddress as string);

    return new ERC8183JobOps({
      walletProvider,
      agentAddress,
      network: opts.network ?? "bsc-testnet",
      storage: opts.storageProvider ?? null,
      servicePrice: opts.servicePrice ?? 0n,
      agentUrl: opts.agentUrl ?? null,
    });
  }

  get agentAddress(): `0x${string}` {
    return this.agentAddressValue;
  }

  /** The underlying `ERC8183Client`, or `null` if it has not been built
   * yet (built lazily by the first async method call). */
  get erc8183Client(): ERC8183Client | null {
    return this.client;
  }

  private async getClient(): Promise<ERC8183Client> {
    if (this.client === null) {
      this.client = await ERC8183Client.create({
        walletProvider: this.walletProvider,
        network: this.network,
      });
    }
    return this.client;
  }

  // -------------------------------------------------------- URL resolution

  /**
   * Return a URL that is reachable by client/voter.
   *
   * Non-`file://` URLs (`ipfs://`, `https://`, ...) are passed through
   * unchanged. `file://` (or empty) URLs fall back to the agent's own HTTP
   * endpoint `{agentUrl}/job/{jobId}/response`. Throws when the fallback is
   * needed but `agentUrl` was not configured.
   */
  private publicDeliverableUrl(jobId: number, storageUrl: string): string {
    if (storageUrl && !storageUrl.startsWith("file://")) {
      return storageUrl;
    }
    if (!this.agentUrl) {
      throw new Error(
        "Cannot publish deliverable: storage returned a non-public URL " +
          "and ERC8183_AGENT_URL is not set. " +
          "Set ERC8183_AGENT_URL to the agent's public base URL including /erc8183 " +
          "(e.g. http://localhost:8003/erc8183).",
      );
    }
    return `${this.agentUrl.replace(/\/+$/, "")}/job/${jobId}/response`;
  }

  // ------------------------------------------------------------- submission

  /**
   * Build a structured deliverable, upload it, and call `submit` on-chain.
   *
   * The on-chain `deliverable` (bytes32) is
   * `DeliverableManifest.manifestHash()` — keccak256 of the canonical
   * manifest JSON (all fields, not just content). The full manifest JSON is
   * uploaded to storage and its URL is passed as `optParams` so verifiers
   * can fetch, re-hash, and confirm integrity.
   */
  async submitResult(
    jobId: number,
    responseContent: string,
    metadata?: Record<string, unknown>,
  ): Promise<OpResult> {
    if (this.walletProvider === null) {
      throw new Error("submit_result requires a signing wallet_provider");
    }
    try {
      const verification = await this.verifyJob(jobId);
      if (!verification.valid) {
        const fields: Record<string, unknown> = {
          success: false,
          error: `Job verification failed: ${verification.error ?? "unknown"}`,
          error_code: verification.error_code,
        };
        if (verification.retryable) {
          fields.retryable = true;
        }
        return fields;
      }

      const maxResp = maxResponseBytes();
      const actualResp = new TextEncoder().encode(responseContent).length;
      if (actualResp > maxResp) {
        return {
          success: false,
          error: `response_content size ${actualResp} bytes exceeds limit ${maxResp} bytes`,
          error_code: ERR_PAYLOAD_TOO_LARGE,
        };
      }

      if (metadata !== undefined) {
        // Validate serializability up front: a bigint (or other non-JSON
        // value) in caller-supplied metadata makes both this size check and
        // the manifest hash / storage upload below throw. That is a
        // deterministic, permanent input error — return it WITHOUT
        // `retryable` so a retry-driven caller doesn't loop forever (the
        // catch-all below would otherwise misclassify it as retryable).
        let metaJson: string;
        try {
          metaJson = JSON.stringify(metadata);
        } catch (error) {
          return {
            success: false,
            error: `metadata is not JSON-serializable (e.g. contains a bigint — stringify values first): ${describeError(error)}`,
            error_code: ERR_METADATA_INVALID,
          };
        }
        const maxMeta = maxMetadataBytes();
        const actualMeta = new TextEncoder().encode(metaJson).length;
        if (actualMeta > maxMeta) {
          return {
            success: false,
            error: `metadata size ${actualMeta} bytes exceeds limit ${maxMeta} bytes`,
            error_code: ERR_PAYLOAD_TOO_LARGE,
          };
        }
      }

      const client = await this.getClient();

      const manifest = new DeliverableManifest({
        version: SCHEMA_VERSION,
        jobId,
        chainId: client.network.chainId,
        contracts: {
          commerce: client.commerce.address,
          router: client.router.address,
          policy: client.policy.address,
        },
        response: { content: responseContent, contentType: "text/plain" },
        metadata: metadata ?? {},
      });
      const data = manifest.toDict();
      const deliverable = manifest.manifestHash();

      let storageUrl = "";
      if (this.storage) {
        storageUrl = await this.storage.upload(
          data,
          `erc8183-job-${jobId}.json`,
        );
        console.info(`[ERC8183JobOps] Deliverable uploaded: ${storageUrl}`);
        this.deliverableUrls.set(jobId, storageUrl);
      }

      const publicUrl = this.publicDeliverableUrl(jobId, storageUrl);
      const result = await client.submit(BigInt(jobId), deliverable, {
        deliverable_url: publicUrl,
      });
      console.info(
        `[ERC8183JobOps] submit(${jobId}) tx: ${result.transactionHash}`,
      );
      return {
        success: true,
        txHash: result.transactionHash,
        deliverableUrl: publicUrl,
        deliverable,
      };
    } catch (error) {
      console.error(
        `[ERC8183JobOps] submit(${jobId}) failed: ${describeError(error)}`,
      );
      return { success: false, ...excErrorFields(error) };
    }
  }

  // ------------------------------------------------------------------ reads

  async getJob(jobId: number): Promise<OpResult> {
    try {
      const client = await this.getClient();
      const job = await client.getJob(BigInt(jobId));
      return {
        success: true,
        jobId: Number(job.id),
        client: job.client,
        provider: job.provider,
        evaluator: job.evaluator,
        description: job.description,
        // bigint fields are stringified so the result is JSON-serializable
        // for a serving layer (JSON.stringify throws on a raw bigint);
        // callers doing arithmetic re-parse with BigInt(). Matches the
        // service_price treatment in verifyJob.
        budget: job.budget.toString(),
        expiredAt: job.expiredAt.toString(),
        submittedAt: job.submittedAt.toString(),
        status: job.status,
        hook: job.hook,
        deliverable: job.deliverable,
      };
    } catch (error) {
      console.error(
        `[ERC8183JobOps] get_job(${jobId}) failed: ${describeError(error)}`,
      );
      // Return a generic message — the raw exception can embed the RPC URL
      // (and its API key) on transport errors. Classify here so callers
      // still get the right status without parsing the message.
      const isNet = NET_ERR_KEYWORDS.some((k) =>
        plainMessage(error).toLowerCase().includes(k),
      );
      return {
        success: false,
        error: isNet
          ? "Temporary chain/RPC error"
          : "Failed to fetch job from chain",
        error_code: isNet ? ERR_CHAIN_UNAVAILABLE : ERR_INTERNAL,
        retryable: true,
      };
    }
  }

  async getJobStatus(jobId: number): Promise<OpResult> {
    const result = await this.getJob(jobId);
    if (!result.success) {
      return result;
    }
    return { success: true, status: result.status };
  }

  /** Retrieve a stored deliverable (cache -> local file -> on-chain URL). */
  async getResponse(jobId: number): Promise<OpResult> {
    if (!this.storage) {
      return { success: false, error: "No storage configured" };
    }

    const url = this.deliverableUrls.get(jobId);
    if (url) {
      try {
        const data = await this.storage.download(url);
        return { success: true, ...data };
      } catch (error) {
        console.warn(
          `[ERC8183JobOps] get_response(${jobId}) download failed: ${describeError(error)}`,
        );
      }
    }

    if (this.storage instanceof LocalStorageProvider) {
      const filename = `erc8183-job-${jobId}.json`;
      try {
        // Mirrors Python's `filepath.exists()` guard: a job whose result
        // was never locally cached is the common case, not a failure — only
        // warn when a file exists but fails to read/parse.
        if (await this.storage.exists(filename)) {
          const data = await this.storage.download(filename);
          return { success: true, ...data };
        }
      } catch (error) {
        console.warn(
          `[ERC8183JobOps] get_response(${jobId}) file read failed: ${describeError(error)}`,
        );
      }
    }

    try {
      const client = await this.getClient();
      const deliverableUrl = await client.getDeliverableUrl(BigInt(jobId));
      if (deliverableUrl) {
        this.deliverableUrls.set(jobId, deliverableUrl);
        const data = await this.storage.download(deliverableUrl);
        return { success: true, ...data };
      }
    } catch (error) {
      if (error instanceof RpcRangeLimitError) {
        console.warn(
          `[ERC8183JobOps] get_response(${jobId}) rate-limited: ${describeError(error)}`,
        );
        return {
          success: false,
          error: `Deliverable for job ${jobId} temporarily unresolvable (RPC rate limit); retry`,
          error_code: ERR_CHAIN_UNAVAILABLE,
          retryable: true,
        };
      }
      console.warn(
        `[ERC8183JobOps] get_response(${jobId}) on-chain fallback failed: ${describeError(error)}`,
      );
    }

    // A job that has been submitted on-chain MUST have a JobInitialised
    // event, so failing to resolve its URL above (rate-limited RPC, submit
    // older than the fallback scan window, storage hiccup) is a resolution
    // failure — retryable, not proof of absence. Only a job that never
    // reached SUBMITTED genuinely has no response.
    const statusResult = await this.getJobStatus(jobId);
    if (
      !statusResult.success ||
      statusResult.status === JobStatus.SUBMITTED ||
      statusResult.status === JobStatus.COMPLETED
    ) {
      return {
        success: false,
        error: `Deliverable for job ${jobId} temporarily unresolvable; retry`,
        error_code: ERR_CHAIN_UNAVAILABLE,
        retryable: true,
      };
    }
    return {
      success: false,
      error: `Response not found for job ${jobId}`,
      error_code: ERR_NOT_FOUND,
    };
  }

  // ---------------------------------------------------- verification helper

  /** Check whether a job can be worked by this agent. Returns
   * `{ valid, error?, error_code?, job?, warnings? }`. */
  async verifyJob(jobId: number): Promise<OpResult> {
    try {
      const jobResult = await this.getJob(jobId);
      if (!jobResult.success) {
        // getJob already returns a sanitized message + error_code.
        const fields: Record<string, unknown> = {
          valid: false,
          error: jobResult.error ?? "Failed to fetch job from chain",
          error_code: jobResult.error_code ?? ERR_INTERNAL,
        };
        if (jobResult.retryable) {
          fields.retryable = true;
        }
        return fields;
      }

      const me = this.agentAddressValue.toLowerCase();

      const status = jobResult.status as JobStatus;
      if (status !== JobStatus.FUNDED) {
        const statusName = JobStatus[status] ?? String(status);
        return {
          valid: false,
          error: `Job status is ${statusName}, expected FUNDED`,
          error_code: ERR_WRONG_STATUS,
        };
      }

      if (String(jobResult.provider ?? "").toLowerCase() !== me) {
        return {
          valid: false,
          error: "This agent is not the provider for this job",
          error_code: ERR_NOT_ASSIGNED,
        };
      }

      const now = BigInt(Math.floor(Date.now() / 1000));
      // getJob stringifies bigint fields for transport; re-parse for compare.
      const expiredAt = BigInt(
        (jobResult.expiredAt as string | undefined) ?? "0",
      );
      if (expiredAt <= now) {
        return {
          valid: false,
          error: "Job has expired",
          error_code: ERR_JOB_EXPIRED,
        };
      }

      const client = await this.getClient();

      // OptimisticPolicy reverts `commerce.submit` with `SubmissionTooLate`
      // once `now > expiredAt - disputeWindow`. Detect that here so the
      // agent doesn't keep retrying every funded-poll tick on a job whose
      // submit deadline has already passed.
      try {
        const disputeWindow = await client.policy.disputeWindow();
        const submitDeadline = expiredAt - disputeWindow;
        if (now > submitDeadline) {
          return {
            valid: false,
            error: `Submission deadline has passed (expiredAt - disputeWindow = ${submitDeadline}, now = ${now})`,
            error_code: ERR_SUBMIT_DEADLINE_PASSED,
          };
        }
      } catch (error) {
        console.warn(
          `[ERC8183JobOps] dispute_window lookup failed; proceeding without submit-deadline check: ${describeError(error)}`,
        );
      }

      const description = (jobResult.description as string | undefined) ?? "";
      if (description) {
        try {
          // Fail closed on a malformed / type-confused description. The
          // negotiation quote TTL (quoteExpiresAt) is intentionally NOT
          // enforced here: verifyJob only runs once a job is FUNDED (price
          // already escrowed on-chain), so re-checking the TTL post-fund
          // can only strand funds — it cannot undo the commit. The TTL
          // guards a signed quote pre-commit; after funding the budget
          // check below is the economic guard that matters.
          parseJobDescription(description);
        } catch (error) {
          return {
            valid: false,
            error: `Malformed job description: ${describeError(error)}`,
            error_code: ERR_DESCRIPTION_INVALID,
          };
        }
      }

      if (this.servicePrice > 0n) {
        const budget = BigInt((jobResult.budget as string | undefined) ?? "0");
        if (budget < this.servicePrice) {
          const decimals = await client.tokenDecimals();
          return {
            valid: false,
            error:
              `Job budget (${budget}) is below agent's` +
              ` service price (${this.servicePrice})`,
            error_code: ERR_BUDGET_TOO_LOW,
            service_price: this.servicePrice.toString(),
            decimals,
          };
        }
      }

      const warnings: Array<{ code: string; message: string }> = [];
      const evaluator = String(jobResult.evaluator ?? "").toLowerCase();
      const clientAddr = String(jobResult.client ?? "").toLowerCase();
      if (evaluator === clientAddr) {
        warnings.push({
          code: "CLIENT_AS_EVALUATOR",
          message:
            "Evaluator equals client — client can self-reject" +
            " and refund after you submit.",
        });
      }

      return {
        valid: true,
        job: jobResult,
        warnings: warnings.length > 0 ? warnings : null,
      };
    } catch (error) {
      console.error(
        `[ERC8183JobOps] verify_job(${jobId}) failed: ${describeError(error)}`,
      );
      const isNet = NET_ERR_KEYWORDS.some((k) =>
        plainMessage(error).toLowerCase().includes(k),
      );
      return {
        valid: false,
        error: isNet ? "Temporary chain/RPC error" : "Failed to verify job",
        error_code: isNet ? ERR_CHAIN_UNAVAILABLE : ERR_INTERNAL,
        retryable: true,
      };
    }
  }

  // ----------------------------------------------------- pending-job scanner

  private async multicallScan(jobIds: number[]): Promise<OpResult> {
    if (jobIds.length === 0) {
      return { success: true, jobs: [] };
    }

    const client = await this.getClient();
    const me = this.agentAddressValue.toLowerCase();

    const jobs = await client.commerce.getJobsBatch(
      jobIds.map((id) => BigInt(id)),
    );

    const now = BigInt(Math.floor(Date.now() / 1000));
    const pending: Record<string, unknown>[] = [];
    for (const job of jobs) {
      if (job === null) {
        continue;
      }
      const id = Number(job.id);
      if (job.provider.toLowerCase() !== me) {
        this.pendingOpenIds.delete(id);
        continue;
      }
      if (job.status === JobStatus.FUNDED && job.expiredAt > now) {
        pending.push({
          success: true,
          jobId: id,
          client: job.client,
          provider: job.provider,
          evaluator: job.evaluator,
          description: job.description,
          // Stringified for JSON transport (see getJob).
          budget: job.budget.toString(),
          expiredAt: job.expiredAt.toString(),
          status: job.status,
          hook: job.hook,
          deliverable: job.deliverable,
        });
        this.pendingOpenIds.delete(id);
      } else if (job.status === JobStatus.OPEN) {
        this.pendingOpenIds.add(id);
      } else {
        this.pendingOpenIds.delete(id);
      }
    }

    return { success: true, jobs: pending };
  }

  private async startupScan(): Promise<OpResult> {
    const client = await this.getClient();
    let counter: bigint;
    try {
      counter = await client.commerce.jobCounter();
    } catch (error) {
      console.warn(
        `[ERC8183JobOps] startup scan counter failed: ${describeError(error)}`,
      );
      this.startupScanDone = true;
      return { success: false, ...excErrorFields(error), jobs: [] };
    }

    if (counter === 0n) {
      this.startupScanDone = true;
      return { success: true, jobs: [] };
    }

    const ids = Array.from({ length: Number(counter) }, (_, i) => i + 1);
    const result = await this.multicallScan(ids);
    this.lastKnownCounter = Number(counter);
    this.startupScanDone = true;
    console.info(
      `[ERC8183JobOps] Startup scan: ${(result.jobs as unknown[]).length} pending of ${counter} total` +
        ` (agent=${this.agentAddressValue})`,
    );
    return result;
  }

  /** Return funded, non-expired jobs assigned to this provider. */
  async getPendingJobs(): Promise<OpResult> {
    try {
      if (!this.startupScanDone) {
        return await this.startupScan();
      }

      const client = await this.getClient();
      const counter = await client.commerce.jobCounter();
      const scanSet = new Set<number>();
      if (Number(counter) > this.lastKnownCounter) {
        for (let i = this.lastKnownCounter + 1; i <= Number(counter); i++) {
          scanSet.add(i);
        }
      }
      for (const id of this.pendingOpenIds) {
        scanSet.add(id);
      }
      if (scanSet.size === 0) {
        return { success: true, jobs: [] };
      }

      const result = await this.multicallScan(
        [...scanSet].sort((a, b) => a - b),
      );
      this.lastKnownCounter = Number(counter);
      return result;
    } catch (error) {
      console.error(
        `[ERC8183JobOps] get_pending_jobs failed: ${describeError(error)}`,
      );
      return { success: false, ...excErrorFields(error), jobs: [] };
    }
  }

  /**
   * Return SUBMITTED jobs assigned to this provider (opt-in auto-settle).
   *
   * Unlike {@link getPendingJobs} (FUNDED, incremental cursor), this does a
   * full scan each call — SUBMITTED jobs sit awaiting the dispute window, so
   * there is no monotonic cursor. Each entry includes `submittedAt` so the
   * caller can check the window exactly instead of approximating it with
   * `expiredAt`.
   */
  async getSubmittedJobs(): Promise<OpResult> {
    try {
      const client = await this.getClient();
      const me = this.agentAddressValue.toLowerCase();
      const counter = await client.commerce.jobCounter();
      if (counter === 0n) {
        return { success: true, jobs: [] };
      }
      const ids = Array.from({ length: Number(counter) }, (_, i) =>
        BigInt(i + 1),
      );
      const jobs = await client.commerce.getJobsBatch(ids);
      const submitted = jobs
        .filter(
          (job) =>
            job !== null &&
            job.provider.toLowerCase() === me &&
            job.status === JobStatus.SUBMITTED,
        )
        .map((job) => {
          const j = job as NonNullable<typeof job>;
          return {
            jobId: Number(j.id),
            client: j.client,
            provider: j.provider,
            evaluator: j.evaluator,
            description: j.description,
            // Stringified for JSON transport (see getJob).
            budget: j.budget.toString(),
            expiredAt: j.expiredAt.toString(),
            submittedAt: j.submittedAt.toString(),
            status: j.status,
            hook: j.hook,
            deliverable: j.deliverable,
          };
        });
      return { success: true, jobs: submitted };
    } catch (error) {
      console.error(
        `[ERC8183JobOps] get_submitted_jobs failed: ${describeError(error)}`,
      );
      return { success: false, ...excErrorFields(error), jobs: [] };
    }
  }
}

// ---------------------------------------------------------------------------
// fundedJobWatcher
// ---------------------------------------------------------------------------

/** Options accepted by {@link fundedJobWatcher}. */
export interface FundedJobWatcherOpts {
  /** Poll interval in seconds. Default `30`. */
  interval?: number;
  /** Ends the loop when aborted; otherwise it runs until the process exits. */
  stop?: AbortSignal;
}

/** Resolves `true` if `signal` aborts before `ms` elapses, `false` if the
 * timer fires first. Resolves promptly on abort (event-driven, not
 * polling), so the watcher's loop can react to `stop` mid-wait. */
function waitForAbortOrTimeout(
  signal: AbortSignal,
  ms: number,
): Promise<boolean> {
  if (signal.aborted) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const onAbort = () => {
      cleanup();
      resolve(true);
    };
    const timer = setTimeout(() => {
      cleanup();
      resolve(false);
    }, ms);
    function cleanup() {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
    signal.addEventListener("abort", onAbort);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Poll `jobOps.getPendingJobs()` and fire `onFunded(job)` per FUNDED job.
 *
 * Signer-free detection loop for keyless services: it NEVER submits or
 * settles — the caller decides what to do (e.g. delegate signing to a
 * separate agent). `onFunded` may be sync or async.
 *
 * Retry contract: a job fires once on success. `onFunded` throwing, or
 * returning `false` / `{ retry: true }`, marks the job for retry on the next
 * tick (after re-checking on-chain that it is still FUNDED and unexpired —
 * `getPendingJobs` reports each job only once, so retries are re-validated
 * via `getJob`). Retries stop naturally when the job leaves FUNDED or
 * expires. Any other return value (incl. `undefined`) keeps fire-once
 * behavior. Pass an `AbortSignal` as `stop` to end the loop; otherwise it
 * runs until cancelled.
 */
export async function fundedJobWatcher(
  jobOps: ERC8183JobOps,
  onFunded: (job: Record<string, unknown>) => unknown | Promise<unknown>,
  opts: FundedJobWatcherOpts = {},
): Promise<void> {
  const interval = opts.interval ?? 30;
  const stop = opts.stop;

  const seen = new Set<number>();
  const retry = new Set<number>();

  async function fire(job: Record<string, unknown>): Promise<void> {
    const jobId = job.jobId as number;
    let result: unknown;
    try {
      result = await onFunded(job);
    } catch (error) {
      console.error(
        `[fundedJobWatcher] onFunded(${jobId}) failed; will retry: ${describeError(error)}`,
      );
      retry.add(jobId);
      return;
    }
    // Mirrors Python's `result.get("retry")` truthy check (not `is True`) —
    // any truthy `retry` value opts into a retry, not just the literal `true`.
    const wantsRetry =
      result === false ||
      (typeof result === "object" &&
        result !== null &&
        Boolean((result as { retry?: unknown }).retry));
    if (wantsRetry) {
      retry.add(jobId);
    } else {
      retry.delete(jobId);
      seen.add(jobId);
    }
  }

  while (true) {
    try {
      // Re-validate + re-fire previously failed jobs first, so a job
      // failing below is not retried within the same tick.
      for (const jobId of [...retry]) {
        const fresh = await jobOps.getJob(jobId);
        if (!fresh.success) {
          continue; // transient read error — keep for next tick
        }
        const status = fresh.status as JobStatus;
        // getJob stringifies bigint fields for transport; re-parse to compare.
        const expiredAt = BigInt(
          (fresh.expiredAt as string | undefined) ?? "0",
        );
        if (
          status !== JobStatus.FUNDED ||
          expiredAt <= BigInt(Math.floor(Date.now() / 1000))
        ) {
          retry.delete(jobId); // job moved on — stop retrying
          continue;
        }
        await fire(fresh);
      }

      const result = await jobOps.getPendingJobs();
      if (result.success) {
        for (const job of (result.jobs as Record<string, unknown>[]) ?? []) {
          const jobId = job.jobId as number;
          if (seen.has(jobId) || retry.has(jobId)) {
            continue;
          }
          await fire(job);
        }
      } else {
        console.warn(`[fundedJobWatcher] poll error: ${result.error}`);
      }
    } catch (error) {
      console.error(
        `[fundedJobWatcher] iteration failed: ${describeError(error)}`,
      );
    }

    if (stop) {
      const aborted = await waitForAbortOrTimeout(stop, interval * 1000);
      if (aborted) {
        return;
      }
    } else {
      await sleep(interval * 1000);
    }
  }
}
