/**
 * Negotiation data structures and handler aligned with ERC-8183 (ERC-8183 Protocol).
 *
 * V1 implements single-round HTTP negotiation:
 *   User sends requirements + quality standards -> Agent returns price or rejects.
 *
 * The TermSpecification follows ERC-8183's structured terms:
 *   Agreed Service + Compensation + Evaluation.
 *
 * NegotiationHandler provides a ready-to-use negotiation processor for agents:
 * ```ts
 * const handler = new NegotiationHandler({ servicePrice: "20e18", currency: "0x..." });
 * const result = await handler.negotiate(requestData);
 * ```
 *
 * On-chain Description (v1 schema)
 * ---------------------------------
 * `buildJobDescription(result.toDict())` produces a compact JSON string for
 * `createJob()`. It embeds the full agreed terms + provider signature so
 * neither party can tamper with the negotiation record after the job is
 * on-chain.
 *
 * ```
 * {
 *   "version": 1,
 *   "negotiated_at": <unix ts>,
 *   "quote_expires_at": <unix ts>,
 *   "task": "<task_description>",
 *   "terms": { "deliverables", "quality_standards", "success_criteria"? },
 *   "price": "<wei>",
 *   "currency": "<token address>",
 *   "negotiation_hash": "0x...",   // keccak256 of above (without hash/sig fields)
 *   "provider_sig": "0x..."        // EIP-191 signature over negotiation_hash
 * }
 * ```
 *
 * UMA dispute voters read `job.description` verbatim from the assertion claim.
 *
 * Port of `python/bnbagent/erc8183/negotiation.py`.
 */

import { getAddress } from "viem";
import { canonicalJson, keccakOfText } from "../core/canonicalJson.js";
import type { ERC8183Client } from "./client.js";
import { JobDescription } from "./schema.js";

/**
 * Maximum byte length of the on-chain `job.description` string. It is stored
 * as a Solidity `string` (unbounded by type), so this is a self-imposed cap
 * to bound `createJob` gas and keep the UMA assertion claim readable. ~536
 * bytes are fixed overhead (negotiation_hash, provider_sig, addresses, JSON
 * keys); the remainder is user text shared across task + terms.
 */
export const MAX_DESCRIPTION_BYTES = 4096;

/**
 * Raised by {@link buildJobDescription} when the assembled on-chain
 * description exceeds `maxLength`. Truncating would invalidate
 * negotiation_hash / provider_sig, so the description is rejected instead.
 */
export class DescriptionTooLongError extends Error {
  constructor(message?: string) {
    super(message);
    this.name = "DescriptionTooLongError";
  }
}

/** ERC-8183 standard rejection codes (aligned with whitepaper + PRD FR-06). */
export const ReasonCode = {
  PRICE_TOO_LOW: "0x01",
  DEADLINE_TOO_TIGHT: "0x02",
  INCAPABLE: "0x03",
  AMBIGUOUS_TERMS: "0x04",
  BUSY: "0x05",
  UNSUPPORTED: "0x06",
  /** task + terms exceed the on-chain description cap */
  TASK_TOO_LONG: "0x07",
} as const;

export type ReasonCodeValue = (typeof ReasonCode)[keyof typeof ReasonCode];

function ensureHexPrefix(h: string): string {
  return h.startsWith("0x") ? h : `0x${h}`;
}

// ---------------------------------------------------------------------------
// TermSpecification
// ---------------------------------------------------------------------------

/** Options accepted by the {@link TermSpecification} constructor. */
export interface TermSpecificationOpts {
  deliverables: string;
  qualityStandards: string;
  successCriteria?: string[] | null;
  price?: string | null;
  currency?: string | null;
  evaluationRequired?: boolean;
  evaluatorType?: string;
}

/**
 * ERC-8183 protocol term specification — the core output of negotiation.
 * Shared between V1 (single-round HTTP) and V2 (multi-round Memo + on-chain
 * PoA).
 *
 * Fields map to ERC-8183's categories:
 *   - Agreed Service: deliverables, qualityStandards, successCriteria
 *   - Compensation: price, currency
 *   - Evaluation: evaluationRequired, evaluatorType
 */
export class TermSpecification {
  readonly deliverables: string;
  readonly qualityStandards: string;
  readonly successCriteria: string[] | null;
  readonly price: string | null;
  readonly currency: string | null;
  readonly evaluationRequired: boolean;
  readonly evaluatorType: string;

  constructor(opts: TermSpecificationOpts) {
    this.deliverables = opts.deliverables;
    this.qualityStandards = opts.qualityStandards;
    this.successCriteria = opts.successCriteria ?? null;
    this.price = opts.price ?? null;
    this.currency = opts.currency ?? null;
    this.evaluationRequired = opts.evaluationRequired ?? true;
    this.evaluatorType = opts.evaluatorType ?? "uma_oov3";
  }

  toDict(): Record<string, unknown> {
    const result: Record<string, unknown> = {
      deliverables: this.deliverables,
      quality_standards: this.qualityStandards,
      evaluation_required: this.evaluationRequired,
      evaluator_type: this.evaluatorType,
    };
    if (this.successCriteria !== null) {
      result.success_criteria = this.successCriteria;
    }
    if (this.price !== null) {
      result.price = this.price;
    }
    if (this.currency !== null) {
      result.currency = this.currency;
    }
    return result;
  }

  static fromDict(data: Record<string, unknown>): TermSpecification {
    return new TermSpecification({
      deliverables: data.deliverables as string,
      qualityStandards: data.quality_standards as string,
      successCriteria: (data.success_criteria as string[] | undefined) ?? null,
      price: (data.price as string | undefined) ?? null,
      currency: (data.currency as string | undefined) ?? null,
      evaluationRequired:
        (data.evaluation_required as boolean | undefined) ?? true,
      evaluatorType: (data.evaluator_type as string | undefined) ?? "uma_oov3",
    });
  }
}

// ---------------------------------------------------------------------------
// NegotiationRequest
// ---------------------------------------------------------------------------

/** Options accepted by the {@link NegotiationRequest} constructor. */
export interface NegotiationRequestOpts {
  taskDescription: string;
  terms: TermSpecification;
  contextUrls?: string[] | null;
  requestId?: string | null;
}

/**
 * User -> Agent: pricing inquiry.
 *
 * User fills in taskDescription and terms (with qualityStandards as the
 * non-negotiable baseline). Agent must agree to standards before quoting.
 *
 * The requestHash is computed by the Client and anchored on-chain at
 * createJobAndLock to prevent post-hoc tampering of the request.
 */
export class NegotiationRequest {
  readonly taskDescription: string;
  readonly terms: TermSpecification;
  readonly contextUrls: string[] | null;
  readonly requestId: string | null;

  constructor(opts: NegotiationRequestOpts) {
    this.taskDescription = opts.taskDescription;
    this.terms = opts.terms;
    this.contextUrls = opts.contextUrls ?? null;
    this.requestId = opts.requestId ?? null;
  }

  /** Return the request content (without hash). */
  toDict(): Record<string, unknown> {
    const result: Record<string, unknown> = {
      task_description: this.taskDescription,
      terms: this.terms.toDict(),
    };
    if (this.contextUrls) {
      result.context_urls = this.contextUrls;
    }
    if (this.requestId) {
      result.request_id = this.requestId;
    }
    return result;
  }

  /**
   * Compute keccak256 hash of the canonical request for on-chain anchoring.
   * Returns hex string with 0x prefix.
   */
  computeHash(): string {
    return ensureHexPrefix(keccakOfText(canonicalJson(this.toDict())));
  }

  /**
   * Return wrapped structure with request content and its hash.
   *
   * ```
   * { request: { task_description, terms, ... }, request_hash: "0x..." }
   * ```
   */
  toEnvelope(): { request: Record<string, unknown>; request_hash: string } {
    return {
      request: this.toDict(),
      request_hash: this.computeHash(),
    };
  }

  static fromDict(data: Record<string, unknown>): NegotiationRequest {
    return new NegotiationRequest({
      taskDescription: data.task_description as string,
      terms: TermSpecification.fromDict(data.terms as Record<string, unknown>),
      contextUrls: (data.context_urls as string[] | undefined) ?? null,
      requestId: (data.request_id as string | undefined) ?? null,
    });
  }

  /**
   * Parse from envelope structure `{ request: {...}, request_hash: "0x..." }`.
   * Returns `[NegotiationRequest, requestHash]`.
   */
  static fromEnvelope(
    data: Record<string, unknown>,
  ): [NegotiationRequest, string] {
    const requestData =
      (data.request as Record<string, unknown> | undefined) ?? data;
    const requestHash = (data.request_hash as string | undefined) ?? "";
    return [NegotiationRequest.fromDict(requestData), requestHash];
  }
}

// ---------------------------------------------------------------------------
// NegotiationResponse
// ---------------------------------------------------------------------------

/** Options accepted by the {@link NegotiationResponse} constructor. */
export interface NegotiationResponseOpts {
  accepted: boolean;
  terms?: TermSpecification | null;
  estimatedCompletionSeconds?: number | null;
  quoteExpiresAt?: number | null;
  reasonCode?: string | null;
  reason?: string | null;
}

/**
 * Agent -> User: pricing response.
 *
 * If accepted, Agent fills in price/currency in terms. Agent may adjust
 * successCriteria but NOT qualityStandards.
 *
 * The responseHash is computed by the Agent and anchored on-chain by the
 * Client at createJobAndLock to prevent post-hoc tampering of agreed terms.
 */
export class NegotiationResponse {
  readonly accepted: boolean;
  readonly terms: TermSpecification | null;
  readonly estimatedCompletionSeconds: number | null;
  readonly quoteExpiresAt: number | null;
  readonly reasonCode: string | null;
  readonly reason: string | null;

  constructor(opts: NegotiationResponseOpts) {
    this.accepted = opts.accepted;
    this.terms = opts.terms ?? null;
    this.estimatedCompletionSeconds = opts.estimatedCompletionSeconds ?? null;
    this.quoteExpiresAt = opts.quoteExpiresAt ?? null;
    this.reasonCode = opts.reasonCode ?? null;
    this.reason = opts.reason ?? null;
  }

  /** Return the response content (without hash). */
  toDict(): Record<string, unknown> {
    const result: Record<string, unknown> = { accepted: this.accepted };
    if (this.terms !== null) {
      result.terms = this.terms.toDict();
    }
    if (this.estimatedCompletionSeconds !== null) {
      result.estimated_completion_seconds = this.estimatedCompletionSeconds;
    }
    if (this.quoteExpiresAt !== null) {
      result.quote_expires_at = this.quoteExpiresAt;
    }
    if (this.reasonCode !== null) {
      result.reason_code = this.reasonCode;
    }
    if (this.reason !== null) {
      result.reason = this.reason;
    }
    return result;
  }

  /**
   * Return wrapped structure with response content and its hash. The hash
   * is of the response content, so they are at different layers.
   *
   * ```
   * { response: { accepted, terms, ... }, response_hash: "0x..." }
   * ```
   */
  toEnvelope(): { response: Record<string, unknown>; response_hash: string } {
    return {
      response: this.toDict(),
      response_hash: this.computeHash(),
    };
  }

  /**
   * Compute keccak256 hash of the canonical response for on-chain
   * anchoring. Returns hex string with 0x prefix.
   *
   * Deliberately EXCLUDES `reason_code` / `reason` — those are advisory
   * text for rejected quotes and must not affect the signed digest of an
   * accepted quote.
   */
  computeHash(): string {
    const canonicalData: Record<string, unknown> = { accepted: this.accepted };
    if (this.terms !== null) {
      canonicalData.terms = this.terms.toDict();
    }
    if (this.estimatedCompletionSeconds !== null) {
      canonicalData.estimated_completion_seconds =
        this.estimatedCompletionSeconds;
    }
    if (this.quoteExpiresAt !== null) {
      canonicalData.quote_expires_at = this.quoteExpiresAt;
    }
    return ensureHexPrefix(keccakOfText(canonicalJson(canonicalData)));
  }

  static fromDict(data: Record<string, unknown>): NegotiationResponse {
    let terms: TermSpecification | null = null;
    // Mirrors Python's `if data.get("terms"):` — an empty `{}` is falsy in
    // Python (but truthy in JS), so an explicit empty-object `terms` must
    // also leave `terms` unset here for cross-SDK parity.
    const rawTerms = data.terms as Record<string, unknown> | undefined;
    if (rawTerms && Object.keys(rawTerms).length > 0) {
      terms = TermSpecification.fromDict(rawTerms);
    }
    return new NegotiationResponse({
      accepted: data.accepted as boolean,
      terms,
      estimatedCompletionSeconds:
        (data.estimated_completion_seconds as number | undefined) ?? null,
      quoteExpiresAt: (data.quote_expires_at as number | undefined) ?? null,
      reasonCode: (data.reason_code as string | undefined) ?? null,
      reason: (data.reason as string | undefined) ?? null,
    });
  }

  /**
   * Parse from envelope structure `{ response: {...}, response_hash: "0x..." }`.
   * Returns `[NegotiationResponse, responseHash]`.
   */
  static fromEnvelope(
    data: Record<string, unknown>,
  ): [NegotiationResponse, string] {
    const responseData =
      (data.response as Record<string, unknown> | undefined) ?? data;
    const responseHash = (data.response_hash as string | undefined) ?? "";
    return [NegotiationResponse.fromDict(responseData), responseHash];
  }
}

// ---------------------------------------------------------------------------
// NegotiationResult
// ---------------------------------------------------------------------------

/** Options accepted by the {@link NegotiationResult} constructor. */
export interface NegotiationResultOpts {
  request: Record<string, unknown>;
  requestHash: string;
  response: Record<string, unknown>;
  responseHash: string;
  negotiationHash?: string;
  providerSig?: string;
  chainId?: number | null;
  verifyingContract?: string | null;
}

/** Result of `NegotiationHandler.negotiate()` containing all components needed for the flow. */
export class NegotiationResult {
  readonly request: Record<string, unknown>;
  readonly requestHash: string;
  readonly response: Record<string, unknown>;
  readonly responseHash: string;
  readonly negotiationHash: string;
  readonly providerSig: string;
  readonly chainId: number | null;
  readonly verifyingContract: string | null;

  constructor(opts: NegotiationResultOpts) {
    this.request = opts.request;
    this.requestHash = opts.requestHash;
    this.response = opts.response;
    this.responseHash = opts.responseHash;
    this.negotiationHash = opts.negotiationHash ?? "";
    this.providerSig = opts.providerSig ?? "";
    this.chainId = opts.chainId ?? null;
    this.verifyingContract = opts.verifyingContract ?? null;
  }

  /** Whether the negotiation was accepted. */
  get accepted(): boolean {
    return (this.response.accepted as boolean | undefined) ?? false;
  }

  /** Return the full negotiation envelope. */
  toDict(): Record<string, unknown> {
    const result: Record<string, unknown> = {
      request: this.request,
      request_hash: this.requestHash,
      response: this.response,
      response_hash: this.responseHash,
    };
    if (this.negotiationHash) {
      result.negotiation_hash = this.negotiationHash;
    }
    if (this.providerSig) {
      result.provider_sig = this.providerSig;
    }
    if (this.chainId !== null) {
      result.chain_id = this.chainId;
    }
    if (this.verifyingContract !== null) {
      result.verifying_contract = this.verifyingContract;
    }
    return result;
  }
}

// ---------------------------------------------------------------------------
// sanitizeForClaim / buildDescriptionContent / buildJobDescription
// ---------------------------------------------------------------------------

/**
 * Sanitize a string for embedding in the UMA assertion claim.
 *
 * Replaces `[` and `]` with `(` and `)` to prevent injection into the UMA
 * claim's section markers (`[REQUEST]`, `[RESPONSE]`, `[VERIFY]`). Also
 * strips ASCII control characters (except tab/newline which are benign in
 * JSON).
 */
export function sanitizeForClaim(s: unknown): string {
  if (typeof s !== "string") {
    return String(s);
  }
  let result = s.replaceAll("[", "(").replaceAll("]", ")");
  let out = "";
  for (const ch of result) {
    const code = ch.codePointAt(0) ?? 0;
    if (code >= 0x20 || ch === "\t" || ch === "\n") {
      out += ch;
    }
  }
  result = out;
  return result;
}

/**
 * Extract and sanitize the signable content from a negotiation result dict.
 *
 * Returns the content dict (without negotiation_hash and provider_sig) that
 * is used as input to keccak256 for the negotiation_hash.
 *
 * When `chainId` and/or `verifyingContract` are provided, they are embedded
 * in the content so the resulting signature is bound to a specific chain +
 * commerce contract. This prevents replaying the same `providerSig` across
 * EVM networks where the same provider key is configured.
 */
export function buildDescriptionContent(
  negotiationResult: Record<string, unknown>,
  chainId?: number | null,
  verifyingContract?: string | null,
): Record<string, unknown> {
  const response =
    (negotiationResult.response as Record<string, unknown> | undefined) ?? {};
  const request =
    (negotiationResult.request as Record<string, unknown> | undefined) ?? {};

  if (!response.accepted) {
    throw new Error("Cannot build description from a rejected negotiation");
  }

  const responseTerms =
    (response.terms as Record<string, unknown> | undefined) ?? {};
  const price = (responseTerms.price as string | undefined) || "";
  const currency = (responseTerms.currency as string | undefined) || "";

  if (!price) {
    throw new Error("Negotiation response missing price");
  }
  if (!currency) {
    throw new Error("Negotiation response missing currency");
  }

  // Build terms section (quality fields only, no price/currency)
  const terms: Record<string, unknown> = {
    deliverables: sanitizeForClaim(
      (responseTerms.deliverables as string | undefined) ?? "",
    ),
    quality_standards: sanitizeForClaim(
      (responseTerms.quality_standards as string | undefined) ?? "",
    ),
  };
  const successCriteria = responseTerms.success_criteria as
    | string[]
    | undefined;
  if (successCriteria && successCriteria.length > 0) {
    terms.success_criteria = successCriteria.map((c) => sanitizeForClaim(c));
  }

  const negotiatedAt =
    (negotiationResult.negotiated_at as number | undefined) ||
    (response.negotiated_at as number | undefined) ||
    Math.floor(Date.now() / 1000);
  // Mirrors Python's `a or b` fallback (falsy — not just nullish — values
  // fall through), for parity with the reference implementation.
  const quoteExpiresAt =
    (negotiationResult.quote_expires_at as number | undefined) ||
    (response.quote_expires_at as number | undefined);

  const content: Record<string, unknown> = {
    version: 1,
    negotiated_at: negotiatedAt,
    task: sanitizeForClaim(
      (request.task_description as string | undefined) ?? "",
    ),
    terms,
    price,
    currency,
  };
  if (quoteExpiresAt !== undefined && quoteExpiresAt !== null) {
    content.quote_expires_at = quoteExpiresAt;
  }
  if (chainId !== undefined && chainId !== null) {
    content.chain_id = chainId;
  }
  if (verifyingContract !== undefined && verifyingContract !== null) {
    content.verifying_contract = getAddress(verifyingContract);
  }

  return content;
}

/**
 * Build a compact JSON description string for `createJob()` from a
 * negotiation result.
 *
 * The description is stored on-chain in `Job.description` and is embedded
 * verbatim in the UMA assertion claim so dispute voters can see the agreed
 * terms directly.
 *
 * The `provider_sig` (if present) allows anyone to verify the provider
 * agreed to these exact terms:
 * `ecrecover(negotiation_hash, provider_sig) == job.provider`.
 *
 * @param negotiationResult Dict from `NegotiationResult.toDict()` or the
 *   HTTP `/negotiate` endpoint response.
 * @param maxLength Maximum byte length of the output string (default
 *   {@link MAX_DESCRIPTION_BYTES}). If exceeded, throws
 *   {@link DescriptionTooLongError} — truncating here would change the
 *   signed content and break provider_sig verification.
 * @throws {Error} If the negotiation was not accepted or required fields
 *   are missing.
 * @throws {DescriptionTooLongError} If the assembled description exceeds
 *   `maxLength`.
 */
export function buildJobDescription(
  negotiationResult: Record<string, unknown>,
  maxLength: number = MAX_DESCRIPTION_BYTES,
): string {
  // Propagate chain_id / verifying_contract from the result so the on-chain
  // description string contains the SAME fields that were keccak'd to
  // produce negotiation_hash. Without this, downstream verifiers that
  // re-derive the hash from the on-chain JSON would always get a different
  // value than what provider_sig actually signed.
  const content = buildDescriptionContent(
    negotiationResult,
    (negotiationResult.chain_id as number | undefined) ?? null,
    (negotiationResult.verifying_contract as string | undefined) ?? null,
  );

  // Append negotiation_hash and provider_sig from the result
  const negotiationHash =
    (negotiationResult.negotiation_hash as string | undefined) ?? "";
  const providerSig =
    (negotiationResult.provider_sig as string | undefined) ?? "";
  if (negotiationHash) {
    content.negotiation_hash = negotiationHash;
  }
  if (providerSig) {
    content.provider_sig = providerSig;
  }

  const description = canonicalJson(content);

  if (description.length > maxLength) {
    throw new DescriptionTooLongError(
      `on-chain description is ${description.length} bytes, exceeds max_length=${maxLength}; shorten task_description / terms. Truncating would invalidate negotiation_hash / provider_sig.`,
    );
  }

  return description;
}

/**
 * Parse a structured on-chain job description (schema v1+).
 *
 * Returns a `JobDescription` if the description is a valid structured JSON,
 * or `null` for plain-text / unstructured descriptions.
 *
 * @param description The `job.description` string from on-chain.
 */
export function parseJobDescription(
  description: string,
): JobDescription | null {
  return JobDescription.fromStr(description);
}

// ---------------------------------------------------------------------------
// NegotiationHandler
// ---------------------------------------------------------------------------

/**
 * The narrow contract ERC-8183 negotiation actually depends on.
 *
 * Matching is structural: any object with the right shape can be passed —
 * no inheritance from `WalletProvider` required. `EVMWalletProvider`
 * satisfies this shape.
 */
export interface MessageSigner {
  readonly address: string;
  signMessage(message: string): Promise<{ signature: string }>;
}

/** Constructor options for {@link NegotiationHandler}. */
export interface NegotiationHandlerOpts {
  /** Price in token smallest unit (e.g. "20000000000000000000" for 20 tokens). */
  servicePrice: string;
  /** BEP20 token contract address. */
  currency: string;
  /** Estimated time to complete the service. Default 120. */
  estimatedCompletionSeconds?: number;
  /** Whether to require quality_standards in request. Default true. */
  requireQualityStandards?: boolean;
  /**
   * Wallet for signing negotiation_hash. When set, the NegotiationResult
   * will include provider_sig allowing clients to verify the agent agreed
   * to the terms.
   */
  walletProvider?: MessageSigner | null;
  /**
   * How long the price quote is valid (default: 300s). Capped at
   * `MAX_QUOTE_TTL_SECONDS` so leaked / replayed provider_sig values cannot
   * accumulate value over time. Must be an integer in `(0, 900]`.
   */
  quoteTtlSeconds?: number;
  /**
   * When set, embedded in the signed content so the signature is bound to
   * a specific chain. Prevents cross-chain replay when the same provider
   * key is configured on multiple EVMs.
   */
  chainId?: number | null;
  /**
   * When set, embedded in the signed content to bind the signature to a
   * specific commerce contract. Use {@link NegotiationHandler.fromErc8183Client}
   * to auto-populate both fields from a live ERC-8183 client.
   */
  verifyingContract?: string | null;
  /**
   * Clock override for testability. Returns unix seconds (integer).
   * Defaults to `Math.floor(Date.now() / 1000)`.
   */
  now?: () => number;
}

/** Options accepted by {@link NegotiationHandler.fromErc8183Client}. */
export interface FromErc8183ClientOpts {
  servicePrice: string;
  estimatedCompletionSeconds?: number;
  requireQualityStandards?: boolean;
  walletProvider?: MessageSigner | null;
  quoteTtlSeconds?: number;
  now?: () => number;
}

/** Options accepted by {@link NegotiationHandler.negotiate}. */
export interface NegotiateOpts {
  /**
   * Optional per-request price (token smallest-unit uint256 string)
   * overriding the construction-time `servicePrice` for this call only.
   * Must be seller-controlled (e.g. an effort estimate), NOT echoed from
   * untrusted client input.
   */
  price?: string;
  /** Optional per-request ETA override. */
  estimatedCompletionSeconds?: number;
}

/**
 * Ready-to-use negotiation handler for agents.
 *
 * Encapsulates the common negotiation logic:
 * - Validates incoming requests
 * - Checks service type support
 * - Validates required fields (quality_standards)
 * - Returns properly structured response with hashes
 * - Signs the negotiation hash with the agent's wallet (if walletProvider set)
 *
 * ```ts
 * const handler = new NegotiationHandler({
 *   servicePrice: "20000000000000000000", // 20 tokens (18 decimals)
 *   currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
 *   walletProvider: wallet,               // enables provider_sig
 *   quoteTtlSeconds: 900,                 // quote valid for 15 minutes
 * });
 *
 * // Or auto-fetch currency from contract:
 * const handler2 = await NegotiationHandler.fromErc8183Client(erc8183Client, {
 *   servicePrice: "20000000000000000000",
 * });
 *
 * // In your /negotiate endpoint:
 * const result = await handler.negotiate(requestData);
 * return result.toDict();
 * ```
 */
export class NegotiationHandler {
  /** Bounds the lifetime of provider_sig (15 minutes). */
  static readonly MAX_QUOTE_TTL_SECONDS = 900;

  private readonly servicePrice: string;
  private readonly currency: string;
  private readonly estimatedCompletion: number;
  private readonly requireQualityStandards: boolean;
  private readonly walletProvider: MessageSigner | null;
  private readonly quoteTtlSeconds: number;
  private readonly chainId: number | null;
  private readonly verifyingContract: string | null;
  private readonly now: () => number;

  constructor(opts: NegotiationHandlerOpts) {
    const quoteTtlSeconds = opts.quoteTtlSeconds ?? 300;
    if (
      !Number.isInteger(quoteTtlSeconds) ||
      typeof quoteTtlSeconds === "boolean"
    ) {
      throw new Error(
        `quote_ttl_seconds must be int, got ${typeof opts.quoteTtlSeconds}`,
      );
    }
    if (
      quoteTtlSeconds <= 0 ||
      quoteTtlSeconds > NegotiationHandler.MAX_QUOTE_TTL_SECONDS
    ) {
      throw new Error(
        `quote_ttl_seconds must be in (0, ${NegotiationHandler.MAX_QUOTE_TTL_SECONDS}], ` +
          `got ${quoteTtlSeconds}`,
      );
    }

    this.servicePrice = opts.servicePrice;
    this.currency = opts.currency;
    this.estimatedCompletion = opts.estimatedCompletionSeconds ?? 120;
    this.requireQualityStandards = opts.requireQualityStandards ?? true;
    this.walletProvider = opts.walletProvider ?? null;
    this.quoteTtlSeconds = quoteTtlSeconds;
    this.chainId = opts.chainId ?? null;
    this.verifyingContract = opts.verifyingContract ?? null;
    this.now = opts.now ?? (() => Math.floor(Date.now() / 1000));

    if (this.walletProvider !== null && this.chainId === null) {
      console.warn(
        "[NegotiationHandler] wallet_provider is set but chain_id is None; " +
          "provider_sig will not be bound to a specific chain. " +
          "Pass chain_id (or use from_erc8183_client) to prevent cross-chain replay.",
      );
    }
  }

  /** Test-only accessor mirroring the Python `_currency` attribute used in ports. */
  get _currency(): string {
    return this.currency;
  }

  /** Test-only accessor mirroring the Python `_wallet_provider` attribute. */
  get _walletProvider(): MessageSigner | null {
    return this.walletProvider;
  }

  /** Test-only accessor mirroring the Python `_chain_id` attribute. */
  get _chainId(): number | null {
    return this.chainId;
  }

  /** Test-only accessor mirroring the Python `_verifying_contract` attribute. */
  get _verifyingContract(): string | null {
    return this.verifyingContract;
  }

  /**
   * Create a `NegotiationHandler` with currency fetched from the ERC-8183
   * contract.
   *
   * ```ts
   * import { ERC8183Client, EVMWalletProvider, NegotiationHandler } from "@bnb-chain/bnbagent";
   *
   * const wallet = new EVMWalletProvider({ password: "...", privateKey: process.env.PRIVATE_KEY });
   * const erc8183 = await ERC8183Client.create({ walletProvider: wallet, network: "bsc-testnet" });
   *
   * const handler = await NegotiationHandler.fromErc8183Client(erc8183, {
   *   servicePrice: process.env.ERC8183_SERVICE_PRICE,
   * });
   * ```
   */
  static async fromErc8183Client(
    erc8183Client: ERC8183Client,
    opts: FromErc8183ClientOpts,
  ): Promise<NegotiationHandler> {
    const currency = await erc8183Client.paymentToken();
    return new NegotiationHandler({
      servicePrice: opts.servicePrice,
      currency,
      estimatedCompletionSeconds: opts.estimatedCompletionSeconds,
      requireQualityStandards: opts.requireQualityStandards,
      walletProvider: opts.walletProvider,
      quoteTtlSeconds: opts.quoteTtlSeconds,
      chainId: erc8183Client.network.chainId,
      verifyingContract: erc8183Client.commerce.address,
      now: opts.now,
    });
  }

  /**
   * Process a negotiation request and return the result.
   *
   * If walletProvider is set, the result includes:
   *   - negotiationHash: keccak256 of the canonical description content
   *   - providerSig: EIP-191 signature over negotiationHash
   */
  async negotiate(
    requestData: Record<string, unknown>,
    opts: NegotiateOpts = {},
  ): Promise<NegotiationResult> {
    let req: NegotiationRequest;
    try {
      req = NegotiationRequest.fromDict(requestData);
    } catch (e) {
      return this.reject({
        requestData,
        reasonCode: ReasonCode.AMBIGUOUS_TERMS,
        reason: `Invalid request format: ${(e as Error).message}`,
      });
    }

    const requestHash = ensureHexPrefix(req.computeHash());

    if (this.requireQualityStandards && !req.terms.qualityStandards) {
      return this.reject({
        requestData: req.toDict(),
        requestHash,
        reasonCode: ReasonCode.AMBIGUOUS_TERMS,
        reason: "quality_standards is required in terms.",
      });
    }

    // Per-request overrides fall back to the construction-time defaults.
    const { price } = opts;
    if (price !== undefined) {
      let valid = true;
      try {
        if (!/^-?\d+$/.test(price) || BigInt(price) < 0n) {
          valid = false;
        }
      } catch {
        valid = false;
      }
      if (!valid) {
        return this.reject({
          requestData: req.toDict(),
          requestHash,
          reasonCode: ReasonCode.AMBIGUOUS_TERMS,
          reason: `price must be a non-negative integer string, got ${JSON.stringify(price)}`,
        });
      }
    }
    const effectivePrice = price !== undefined ? price : this.servicePrice;
    const effectiveEta =
      opts.estimatedCompletionSeconds !== undefined
        ? opts.estimatedCompletionSeconds
        : this.estimatedCompletion;

    const now = this.now();
    const quoteExpiresAt = now + this.quoteTtlSeconds;

    const responseTerms = new TermSpecification({
      deliverables: req.terms.deliverables,
      qualityStandards: req.terms.qualityStandards,
      successCriteria: req.terms.successCriteria,
      price: effectivePrice,
      currency: this.currency,
    });

    const response = new NegotiationResponse({
      accepted: true,
      terms: responseTerms,
      estimatedCompletionSeconds: effectiveEta,
      quoteExpiresAt,
    });

    const responseHash = ensureHexPrefix(response.computeHash());

    // Build partial result to compute negotiation_hash
    const partialResult = new NegotiationResult({
      request: req.toDict(),
      requestHash,
      response: response.toDict(),
      responseHash,
    });
    const partialDict = partialResult.toDict();
    partialDict.negotiated_at = now;

    let negotiationHash = "";
    let providerSig = "";

    if (this.walletProvider) {
      try {
        const content = buildDescriptionContent(
          partialDict,
          this.chainId,
          this.verifyingContract,
        );
        const canonical = canonicalJson(content);
        negotiationHash = ensureHexPrefix(keccakOfText(canonical));

        const sigResult =
          await this.walletProvider.signMessage(negotiationHash);
        providerSig = sigResult.signature
          ? ensureHexPrefix(sigResult.signature)
          : "";
      } catch (e) {
        // Signing failure is non-fatal: return the quote without a
        // provider_sig, but log so operators can detect wallet issues.
        console.warn(
          `[NegotiationHandler] sign_message failed: ${(e as Error).message}; returning quote without provider_sig`,
        );
        negotiationHash = "";
        providerSig = "";
      }
    }

    // Store negotiated_at in the response dict for build_job_description
    const responseDict = response.toDict();
    responseDict.negotiated_at = now;

    // Echo chain_id / verifying_contract into the result so
    // build_job_description writes them into the on-chain JSON. Without
    // this, the on-chain description would lack the fields that
    // negotiation_hash was computed over, and downstream verifiers
    // couldn't reconstruct the signed digest.
    const boundChainId = negotiationHash ? this.chainId : null;
    const boundContract = negotiationHash ? this.verifyingContract : null;

    const result = new NegotiationResult({
      request: req.toDict(),
      requestHash,
      response: responseDict,
      responseHash,
      negotiationHash,
      providerSig,
      chainId: boundChainId,
      verifyingContract: boundContract,
    });

    // Reject up front if the on-chain description would exceed the cap.
    // Measuring the fully-assembled string (incl. negotiation_hash +
    // provider_sig) means we never hand back a quote the client cannot
    // turn into a job, and never silently truncate a signed record.
    try {
      buildJobDescription(result.toDict());
    } catch (e) {
      if (e instanceof DescriptionTooLongError) {
        return this.reject({
          requestData: req.toDict(),
          requestHash,
          reasonCode: ReasonCode.TASK_TOO_LONG,
          reason: `task_description + terms exceed the on-chain description size limit (${MAX_DESCRIPTION_BYTES} bytes)`,
        });
      }
      throw e;
    }

    return result;
  }

  /** Build a rejection response. */
  private reject(opts: {
    requestData: Record<string, unknown>;
    reasonCode: string;
    reason: string;
    requestHash?: string;
  }): NegotiationResult {
    const requestHash = opts.requestHash ?? "";
    const response = new NegotiationResponse({
      accepted: false,
      reasonCode: opts.reasonCode,
      reason: opts.reason,
    });
    const responseHash = requestHash
      ? ensureHexPrefix(response.computeHash())
      : "";
    return new NegotiationResult({
      request: opts.requestData,
      requestHash,
      response: response.toDict(),
      responseHash,
    });
  }
}
