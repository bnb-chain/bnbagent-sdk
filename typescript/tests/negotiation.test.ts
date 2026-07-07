/**
 * Ports `python/tests/test_negotiation.py`: TermSpecification / NegotiationRequest /
 * NegotiationResponse / NegotiationResult to/from dict + envelope + hashes
 * (response hash excludes reason fields), sanitizeForClaim, buildJobDescription
 * (compact JSON, rejects unaccepted/missing price/currency, over-length throws
 * — never truncates, quote_expires_at included, sig fields omitted when
 * absent), parseJobDescription, NegotiationHandler (accept, price override,
 * malformed price -> AMBIGUOUS, eta override, quote TTL cap/int validation,
 * wallet signing, no-wallet unsigned, invalid request -> AMBIGUOUS,
 * task-too-long -> TASK_TOO_LONG, quality_standards gate, fromErc8183Client),
 * and the chain-binding anti-replay invariants (chain_id + checksummed
 * verifying_contract embedded in signed content, different chain_id ->
 * different hash, on-chain round-trip re-keccak reproduces the signed hash,
 * sign_message failure -> unsigned quote + warning).
 */

import { getAddress, keccak256, toBytes } from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canonicalJson } from "../src/core/canonicalJson.js";
import {
  DescriptionTooLongError,
  MAX_DESCRIPTION_BYTES,
  type MessageSigner,
  NegotiationHandler,
  NegotiationRequest,
  NegotiationResponse,
  NegotiationResult,
  ReasonCode,
  TermSpecification,
  buildDescriptionContent,
  buildJobDescription,
  parseJobDescription,
  sanitizeForClaim,
} from "../src/erc8183/negotiation.js";

const FIXED_NOW = 1_700_000_000;

function keccakOfCanonical(value: unknown): string {
  return keccak256(toBytes(canonicalJson(value)));
}

function makeTerms(
  overrides: Partial<{
    deliverables: string;
    qualityStandards: string;
    successCriteria: string[] | null;
    price: string | null;
    currency: string | null;
  }> = {},
): TermSpecification {
  return new TermSpecification({
    deliverables: "news summary",
    qualityStandards: "accurate, sourced",
    ...overrides,
  });
}

function makeRequest(
  overrides: Partial<{
    taskDescription: string;
    terms: TermSpecification;
    contextUrls: string[] | null;
    requestId: string | null;
  }> = {},
): NegotiationRequest {
  return new NegotiationRequest({
    taskDescription: "Get latest news",
    terms: makeTerms(),
    ...overrides,
  });
}

function makeAcceptedResult(
  overrides: {
    task?: string;
    price?: string;
    currency?: string;
    negotiationHash?: string;
    providerSig?: string;
  } = {},
): Record<string, unknown> {
  const {
    task = "Get latest news",
    price = "20000000000000000000",
    currency = "0xToken",
    negotiationHash = "0xabc",
    providerSig = "0xsig",
  } = overrides;
  const now = FIXED_NOW;
  return {
    request: {
      task_description: task,
      terms: {
        deliverables: "news summary",
        quality_standards: "accurate, sourced",
        evaluation_required: true,
        evaluator_type: "uma_oov3",
      },
    },
    request_hash: "0xreq",
    response: {
      accepted: true,
      terms: {
        deliverables: "news summary",
        quality_standards: "accurate, sourced",
        evaluation_required: true,
        evaluator_type: "uma_oov3",
        price,
        currency,
      },
      quote_expires_at: now + 3600,
      negotiated_at: now,
    },
    response_hash: "0xresp",
    negotiation_hash: negotiationHash,
    provider_sig: providerSig,
  };
}

describe("TermSpecification", () => {
  it("toDict includes required fields", () => {
    const t = makeTerms();
    const d = t.toDict();
    expect(d.deliverables).toBe("news summary");
    expect(d.quality_standards).toBe("accurate, sourced");
  });

  it("toDict includes optional fields when set", () => {
    const t = makeTerms({
      successCriteria: ["c1"],
      price: "100",
      currency: "0xToken",
    });
    const d = t.toDict();
    expect(d.success_criteria).toEqual(["c1"]);
    expect(d.price).toBe("100");
    expect(d.currency).toBe("0xToken");
  });

  it("fromDict round-trips", () => {
    const t = makeTerms({ price: "50", currency: "0xABC" });
    const d = t.toDict();
    const t2 = TermSpecification.fromDict(d);
    expect(t2.deliverables).toBe(t.deliverables);
    expect(t2.price).toBe(t.price);
  });

  it("defaults evaluationRequired/evaluatorType", () => {
    const t = makeTerms();
    expect(t.evaluationRequired).toBe(true);
    expect(t.evaluatorType).toBe("uma_oov3");
  });
});

describe("NegotiationRequest", () => {
  it("toDict has task_description and terms", () => {
    const req = makeRequest();
    const d = req.toDict();
    expect(d.task_description).toBeDefined();
    expect(d.terms).toBeDefined();
  });

  it("toDict includes optionals when set", () => {
    const req = makeRequest({
      contextUrls: ["http://example.com"],
      requestId: "r1",
    });
    const d = req.toDict();
    expect(d.context_urls).toEqual(["http://example.com"]);
    expect(d.request_id).toBe("r1");
  });

  it("computeHash is deterministic and 0x-prefixed", () => {
    const req = makeRequest();
    const h1 = req.computeHash();
    const h2 = req.computeHash();
    expect(h1).toBe(h2);
    expect(h1.startsWith("0x")).toBe(true);
    expect(h1.length).toBe(66);
  });

  it("round-trips through envelope", () => {
    const req = makeRequest();
    const env = req.toEnvelope();
    expect(env.request).toBeDefined();
    expect(env.request_hash).toBeDefined();
    const [req2, hash2] = NegotiationRequest.fromEnvelope(env);
    expect(req2.taskDescription).toBe(req.taskDescription);
    expect(hash2).toBe(env.request_hash);
  });

  it("fromDict parses a plain wire object", () => {
    const d = {
      task_description: "Do something",
      terms: { deliverables: "output", quality_standards: "high" },
    };
    const req = NegotiationRequest.fromDict(d);
    expect(req.taskDescription).toBe("Do something");
    expect(req.terms.deliverables).toBe("output");
  });
});

describe("NegotiationResponse", () => {
  it("toDict for accepted response", () => {
    const resp = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "100", currency: "0xTok" }),
      estimatedCompletionSeconds: 60,
    });
    const d = resp.toDict();
    expect(d.accepted).toBe(true);
    expect(d.terms).toBeDefined();
    expect(d.estimated_completion_seconds).toBe(60);
  });

  it("toDict includes quote_expires_at", () => {
    const exp = FIXED_NOW + 3600;
    const resp = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "100", currency: "0xTok" }),
      quoteExpiresAt: exp,
    });
    expect(resp.toDict().quote_expires_at).toBe(exp);
  });

  it("toDict for rejected response", () => {
    const resp = new NegotiationResponse({
      accepted: false,
      reasonCode: ReasonCode.PRICE_TOO_LOW,
      reason: "Too cheap",
    });
    const d = resp.toDict();
    expect(d.accepted).toBe(false);
    expect(d.reason_code).toBe("0x01");
    expect(d.reason).toBe("Too cheap");
  });

  it("computeHash returns a 32-byte 0x hash", () => {
    const resp = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "100", currency: "0xTok" }),
    });
    const h = resp.computeHash();
    expect(h.startsWith("0x")).toBe(true);
    expect(h.length).toBe(66);
  });

  it("computeHash includes quote_expires_at", () => {
    const exp = 9_999_999;
    const resp1 = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "1", currency: "0x" }),
    });
    const resp2 = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "1", currency: "0x" }),
      quoteExpiresAt: exp,
    });
    expect(resp1.computeHash()).not.toBe(resp2.computeHash());
  });

  it("computeHash EXCLUDES reason_code/reason", () => {
    const accepted = new NegotiationResponse({
      accepted: false,
      reasonCode: ReasonCode.PRICE_TOO_LOW,
      reason: "some reason",
    });
    const noReason = new NegotiationResponse({ accepted: false });
    expect(accepted.computeHash()).toBe(noReason.computeHash());
  });

  it("round-trips through envelope", () => {
    const resp = new NegotiationResponse({
      accepted: true,
      terms: makeTerms({ price: "100", currency: "0xTok" }),
    });
    const env = resp.toEnvelope();
    expect(env.response).toBeDefined();
    expect(env.response_hash).toBeDefined();
    const [resp2, hash2] = NegotiationResponse.fromEnvelope(env);
    expect(resp2.accepted).toBe(true);
    expect(hash2).toBe(env.response_hash);
  });

  it("fromDict parses a rejection", () => {
    const d = { accepted: false, reason_code: "0x03", reason: "Cannot do it" };
    const resp = NegotiationResponse.fromDict(d);
    expect(resp.accepted).toBe(false);
    expect(resp.reasonCode).toBe("0x03");
  });

  it("fromDict parses quote_expires_at", () => {
    const exp = FIXED_NOW + 1800;
    const resp = NegotiationResponse.fromDict({
      accepted: true,
      quote_expires_at: exp,
    });
    expect(resp.quoteExpiresAt).toBe(exp);
  });

  it("computeHash is deterministic", () => {
    const resp = new NegotiationResponse({
      accepted: false,
      reasonCode: "0x01",
    });
    expect(resp.computeHash()).toBe(resp.computeHash());
  });
});

describe("NegotiationResult", () => {
  it("accepted property reflects response.accepted", () => {
    const result = new NegotiationResult({
      request: {},
      requestHash: "0x123",
      response: { accepted: true },
      responseHash: "0x456",
    });
    expect(result.accepted).toBe(true);
  });

  it("toDict omits sig fields when absent", () => {
    const result = new NegotiationResult({
      request: { task: "x" },
      requestHash: "0xabc",
      response: { accepted: false },
      responseHash: "0xdef",
    });
    const d = result.toDict();
    expect(d.request).toEqual({ task: "x" });
    expect(d.request_hash).toBe("0xabc");
    expect(d.response).toEqual({ accepted: false });
    expect("negotiation_hash" in d).toBe(false);
    expect("provider_sig" in d).toBe(false);
  });

  it("toDict includes sig fields when present", () => {
    const result = new NegotiationResult({
      request: {},
      requestHash: "0x1",
      response: { accepted: true },
      responseHash: "0x2",
      negotiationHash: "0xhash",
      providerSig: "0xsig",
    });
    const d = result.toDict();
    expect(d.negotiation_hash).toBe("0xhash");
    expect(d.provider_sig).toBe("0xsig");
  });
});

describe("sanitizeForClaim", () => {
  it("replaces brackets", () => {
    expect(sanitizeForClaim("[REQUEST]")).toBe("(REQUEST)");
    expect(sanitizeForClaim("[RESPONSE]")).toBe("(RESPONSE)");
    expect(sanitizeForClaim("[VERIFY]")).toBe("(VERIFY)");
  });

  it("strips null bytes", () => {
    expect(sanitizeForClaim("hello\x00world")).not.toContain("\x00");
  });

  it("strips ASCII control chars below 0x20 except tab/newline", () => {
    expect(sanitizeForClaim("a\x01b")).not.toContain("\x01");
    expect(sanitizeForClaim("a\x1fb")).not.toContain("\x1f");
    expect(sanitizeForClaim("a\tb\nc")).toBe("a\tb\nc");
  });

  it("preserves normal text", () => {
    const s = "Accurate, well-sourced, covers at least 5 news items";
    expect(sanitizeForClaim(s)).toBe(s);
  });

  it("handles non-string input", () => {
    expect(typeof sanitizeForClaim(42)).toBe("string");
    expect(sanitizeForClaim(42)).toBe("42");
  });
});

describe("buildJobDescription", () => {
  it("basic structure", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect(parsed.version).toBe(1);
    expect(parsed.task).toBe("Get latest news");
    expect(parsed.terms).toBeDefined();
    expect(parsed.price).toBe("20000000000000000000");
    expect(parsed.currency).toBe("0xToken");
    expect(parsed.negotiation_hash).toBe("0xabc");
    expect(parsed.provider_sig).toBe("0xsig");
  });

  it("terms content excludes non-quality fields", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    const terms = parsed.terms;
    expect(terms.deliverables).toBe("news summary");
    expect(terms.quality_standards).toBe("accurate, sourced");
    expect("service_type" in terms).toBe(false);
    expect("deadline_seconds" in terms).toBe(false);
    expect("price" in terms).toBe(false);
    expect("currency" in terms).toBe(false);
  });

  it("emits compact canonical JSON", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect(desc).toBe(canonicalJson(parsed));
  });

  it("sanitizes brackets in task", () => {
    const result = makeAcceptedResult({
      task: "[REQUEST] tricky task [VERIFY]",
    });
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect(parsed.task).not.toContain("[");
  });

  it("raises on rejected negotiation", () => {
    const rejected = {
      request: { task_description: "x", terms: {} },
      request_hash: "0x",
      response: { accepted: false, reason: "No" },
      response_hash: "0x",
    };
    expect(() => buildJobDescription(rejected)).toThrow(/rejected/);
  });

  it("raises on missing price", () => {
    const result = makeAcceptedResult({ price: "" });
    expect(() => buildJobDescription(result)).toThrow(/price/);
  });

  it("raises on missing currency", () => {
    const result = makeAcceptedResult({ currency: "" });
    expect(() => buildJobDescription(result)).toThrow(/currency/);
  });

  it("raises (never truncates) when over max_length", () => {
    const result = makeAcceptedResult({ task: "A".repeat(1000) });
    expect(() => buildJobDescription(result, 500)).toThrow(
      /exceeds max_length/,
    );
    expect(() => buildJobDescription(result, 500)).toThrow(
      DescriptionTooLongError,
    );
  });

  it("includes quote_expires_at", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect(parsed.quote_expires_at).toBeDefined();
    expect(parsed.quote_expires_at).toBeGreaterThan(FIXED_NOW);
  });

  it("omits sig fields when absent", () => {
    const result = makeAcceptedResult({ negotiationHash: "", providerSig: "" });
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect("negotiation_hash" in parsed).toBe(false);
    expect("provider_sig" in parsed).toBe(false);
  });

  it("includes success_criteria when set", () => {
    const result = makeAcceptedResult();
    (
      result.response as Record<string, unknown> & {
        terms: Record<string, unknown>;
      }
    ).terms.success_criteria = ["criterion 1", "criterion 2"];
    const desc = buildJobDescription(result);
    const parsed = JSON.parse(desc);
    expect(parsed.terms.success_criteria).toEqual([
      "criterion 1",
      "criterion 2",
    ]);
  });
});

describe("parseJobDescription", () => {
  it("parses a valid structured description", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = parseJobDescription(desc);
    expect(parsed).not.toBeNull();
    expect(parsed?.version).toBe(1);
    expect(parsed?.task).toBeTruthy();
  });

  it("returns null for plain text", () => {
    expect(parseJobDescription("Search for BNB Chain news")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(parseJobDescription("")).toBeNull();
  });

  it("returns null for invalid JSON", () => {
    expect(parseJobDescription("{not valid json}")).toBeNull();
  });

  it("returns null for JSON without version", () => {
    expect(parseJobDescription('{"task": "something"}')).toBeNull();
  });

  it("round-trips task/price", () => {
    const result = makeAcceptedResult();
    const desc = buildJobDescription(result);
    const parsed = parseJobDescription(desc);
    expect(parsed?.task).toBe("Get latest news");
    expect(parsed?.price).toBe("20000000000000000000");
  });
});

// ---------------------------------------------------------------------------
// NegotiationHandler
// ---------------------------------------------------------------------------

function basicRequest(): Record<string, unknown> {
  return {
    task_description: "Get news",
    terms: { deliverables: "summary", quality_standards: "accurate" },
  };
}

function makeMockWallet(
  overrides: Partial<MessageSigner> = {},
): MessageSigner & {
  signMessage: ReturnType<typeof vi.fn>;
} {
  const signMessage = vi.fn(async () => ({
    signature: `0x${"ab".repeat(65)}`,
  }));
  return {
    address: "0x0000000000000000000000000000000000dEaD",
    signMessage,
    ...overrides,
  } as MessageSigner & { signMessage: ReturnType<typeof vi.fn> };
}

describe("NegotiationHandler", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  function makeHandler(
    overrides: Partial<
      ConstructorParameters<typeof NegotiationHandler>[0]
    > = {},
  ) {
    return new NegotiationHandler({
      servicePrice: "20000000000000000000",
      currency: "0xToken",
      now: () => FIXED_NOW,
      ...overrides,
    });
  }

  it("accepts a basic request", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate({
      task_description: "Get news",
      terms: { deliverables: "summary", quality_standards: "accurate" },
    });
    expect(result.accepted).toBe(true);
    expect((result.response.terms as Record<string, unknown>).price).toBe(
      "20000000000000000000",
    );
    expect(result.requestHash.startsWith("0x")).toBe(true);
    expect(result.responseHash.startsWith("0x")).toBe(true);
  });

  it("applies a per-request price override", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate(basicRequest(), { price: "123" });
    expect(result.accepted).toBe(true);
    expect((result.response.terms as Record<string, unknown>).price).toBe(
      "123",
    );
  });

  it("falls back to the constructed service price without override", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate(basicRequest());
    expect((result.response.terms as Record<string, unknown>).price).toBe(
      "20000000000000000000",
    );
  });

  it("applies a per-request ETA override", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate(basicRequest(), {
      estimatedCompletionSeconds: 999,
    });
    expect(result.response.estimated_completion_seconds).toBe(999);
  });

  it("rejects a malformed price override", async () => {
    const handler = makeHandler();
    for (const bad of ["-5", "abc", ""]) {
      const result = await handler.negotiate(basicRequest(), { price: bad });
      expect(result.accepted).toBe(false);
      expect(result.response.reason_code).toBe(ReasonCode.AMBIGUOUS_TERMS);
    }
  });

  it("includes quote_expires_at bounded by quoteTtlSeconds", async () => {
    const handler = makeHandler({ quoteTtlSeconds: 180 });
    const result = await handler.negotiate(basicRequest());
    expect(result.accepted).toBe(true);
    expect(result.response.quote_expires_at).toBe(FIXED_NOW + 180);
  });

  it("enforces the quote TTL cap and floor", () => {
    expect(() => makeHandler({ quoteTtlSeconds: 901 })).toThrow(
      /quote_ttl_seconds/,
    );
    expect(() => makeHandler({ quoteTtlSeconds: 0 })).toThrow(
      /quote_ttl_seconds/,
    );
    expect(() => makeHandler({ quoteTtlSeconds: -1 })).toThrow(
      /quote_ttl_seconds/,
    );
    // Boundary: 900 is the max and must succeed.
    expect(() => makeHandler({ quoteTtlSeconds: 900 })).not.toThrow();
  });

  it("requires quoteTtlSeconds to be an integer (rejects strings/booleans)", () => {
    expect(() =>
      makeHandler({ quoteTtlSeconds: "120" as unknown as number }),
    ).toThrow(/quote_ttl_seconds/);
    expect(() =>
      makeHandler({ quoteTtlSeconds: true as unknown as number }),
    ).toThrow(/quote_ttl_seconds/);
  });

  it("signs with a configured wallet provider", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({ walletProvider: wallet, chainId: 97 });
    const result = await handler.negotiate(basicRequest());
    expect(result.accepted).toBe(true);
    expect(result.negotiationHash.startsWith("0x")).toBe(true);
    expect(result.providerSig.startsWith("0x")).toBe(true);
    expect(wallet.signMessage).toHaveBeenCalledTimes(1);
    expect(wallet.signMessage).toHaveBeenCalledWith(result.negotiationHash);
  });

  it("has no signature without a wallet provider", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate(basicRequest());
    expect(result.negotiationHash).toBe("");
    expect(result.providerSig).toBe("");
  });

  it("negotiationHash is keccak256 of the canonical description content", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({ walletProvider: wallet, chainId: 97 });
    const result = await handler.negotiate(basicRequest());
    expect(result.accepted).toBe(true);

    const content = buildDescriptionContent(result.toDict(), 97);
    const expectedHash = keccakOfCanonical(content);
    expect(result.negotiationHash).toBe(expectedHash);
  });

  it("rejects an invalid/ambiguous request format", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate({ bad: "data" });
    expect(result.accepted).toBe(false);
    expect(result.response.reason_code).toBe(ReasonCode.AMBIGUOUS_TERMS);
  });

  it("rejects with TASK_TOO_LONG when the description would overflow the cap", async () => {
    const handler = makeHandler();
    const result = await handler.negotiate({
      task_description: "x".repeat(10_000),
      terms: { deliverables: "summary", quality_standards: "accurate" },
    });
    expect(result.accepted).toBe(false);
    expect(result.response.reason_code).toBe(ReasonCode.TASK_TOO_LONG);
  });

  it("rejects when quality_standards is required but missing", async () => {
    const handler = makeHandler({ requireQualityStandards: true });
    const result = await handler.negotiate({
      task_description: "Do something",
      terms: { deliverables: "output", quality_standards: "" },
    });
    expect(result.accepted).toBe(false);
    expect(result.response.reason_code).toBe(ReasonCode.AMBIGUOUS_TERMS);
  });

  it("fromErc8183Client pulls currency from the contract", async () => {
    const mockClient = {
      paymentToken: vi.fn(async () => "0xTokenAddr"),
      network: { chainId: 97 },
      commerce: { address: "0xa206c0517B6371c6638cD9E4A42cC9F02A33B0de" },
    };
    const handler = await NegotiationHandler.fromErc8183Client(
      mockClient as unknown as Parameters<
        typeof NegotiationHandler.fromErc8183Client
      >[0],
      { servicePrice: "20000000000000000000" },
    );
    expect(handler._currency).toBe("0xTokenAddr");
    expect(handler._chainId).toBe(97);
    expect(handler._verifyingContract).toBe(
      "0xa206c0517B6371c6638cD9E4A42cC9F02A33B0de",
    );
  });

  it("fromErc8183Client passes through the wallet provider", async () => {
    const mockClient = {
      paymentToken: vi.fn(async () => "0xTokenAddr"),
      network: { chainId: 97 },
      commerce: { address: "0xa206c0517B6371c6638cD9E4A42cC9F02A33B0de" },
    };
    const wallet = makeMockWallet();
    const handler = await NegotiationHandler.fromErc8183Client(
      mockClient as unknown as Parameters<
        typeof NegotiationHandler.fromErc8183Client
      >[0],
      { servicePrice: "20000000000000000000", walletProvider: wallet },
    );
    expect(handler._walletProvider).toBe(wallet);
  });

  it("warns when a wallet is configured without a chain_id", () => {
    makeHandler({ walletProvider: makeMockWallet() });
    expect(warnSpy).toHaveBeenCalled();
    expect(String(warnSpy.mock.calls[0]?.[0])).toContain("chain_id is None");
  });
});

// ---------------------------------------------------------------------------
// Chain-binding anti-replay
// ---------------------------------------------------------------------------

describe("NegotiationSignatureBinding", () => {
  const commerceAddr = getAddress("0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de");

  function makeHandler(
    overrides: Partial<
      ConstructorParameters<typeof NegotiationHandler>[0]
    > = {},
  ) {
    return new NegotiationHandler({
      servicePrice: "20000000000000000000",
      currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
      now: () => FIXED_NOW,
      ...overrides,
    });
  }

  it("content includes chain_id when set, and the hash is derived from it", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({ walletProvider: wallet, chainId: 56 });
    const result = await handler.negotiate(basicRequest());

    const content = buildDescriptionContent(result.toDict(), 56);
    expect(content.chain_id).toBe(56);
    expect(result.negotiationHash).toBe(keccakOfCanonical(content));
  });

  it("content includes checksummed verifying_contract when set", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({
      walletProvider: wallet,
      chainId: 97,
      verifyingContract: commerceAddr,
    });
    const result = await handler.negotiate(basicRequest());

    const content = buildDescriptionContent(result.toDict(), 97, commerceAddr);
    expect(content.verifying_contract).toBe(commerceAddr);
    expect(result.negotiationHash).toBe(keccakOfCanonical(content));
  });

  it("omits chain_id/verifying_contract from content when not set", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({ walletProvider: wallet });
    const result = await handler.negotiate(basicRequest());

    const content = buildDescriptionContent(result.toDict());
    expect("chain_id" in content).toBe(false);
    expect("verifying_contract" in content).toBe(false);
  });

  it("different chain_id produces a different negotiation hash", async () => {
    const wallet = makeMockWallet();
    const testnetResult = await makeHandler({
      walletProvider: wallet,
      chainId: 97,
    }).negotiate(basicRequest());
    const mainnetResult = await makeHandler({
      walletProvider: wallet,
      chainId: 56,
    }).negotiate(basicRequest());
    expect(testnetResult.negotiationHash).not.toBe(
      mainnetResult.negotiationHash,
    );
  });

  it("fromErc8183Client populates chain_id and checksummed verifying_contract", async () => {
    const mockClient = {
      paymentToken: vi.fn(async () => "0xTokenAddr"),
      network: { chainId: 97 },
      commerce: { address: commerceAddr },
    };
    const handler = await NegotiationHandler.fromErc8183Client(
      mockClient as unknown as Parameters<
        typeof NegotiationHandler.fromErc8183Client
      >[0],
      { servicePrice: "20000000000000000000" },
    );
    expect(handler._chainId).toBe(97);
    expect(handler._verifyingContract).toBe(commerceAddr);
  });
});

describe("ChainBindingRoundtrip", () => {
  const commerceAddr = getAddress("0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de");

  function makeHandler(
    overrides: Partial<
      ConstructorParameters<typeof NegotiationHandler>[0]
    > = {},
  ) {
    return new NegotiationHandler({
      servicePrice: "20000000000000000000",
      currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
      now: () => FIXED_NOW,
      ...overrides,
    });
  }

  it("build_job_description includes chain_id/verifying_contract when present", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({
      walletProvider: wallet,
      chainId: 56,
      verifyingContract: commerceAddr,
    });
    const result = await handler.negotiate(basicRequest());

    const descriptionJson = buildJobDescription(result.toDict());
    const parsed = JSON.parse(descriptionJson);
    expect(parsed.chain_id).toBe(56);
    expect(parsed.verifying_contract).toBe(commerceAddr);
  });

  it("the signed hash matches a downstream verifier's stripped-and-rehashed digest", async () => {
    const wallet = makeMockWallet();
    const handler = makeHandler({
      walletProvider: wallet,
      chainId: 97,
      verifyingContract: commerceAddr,
    });
    const result = await handler.negotiate(basicRequest());

    // Simulate downstream verifier:
    const descriptionJson = buildJobDescription(result.toDict());
    const parsed = JSON.parse(descriptionJson);
    parsed.negotiation_hash = undefined;
    parsed.provider_sig = undefined;
    const recomputed = keccakOfCanonical(parsed);

    expect(recomputed).toBe(result.negotiationHash);
  });
});

describe("SigningFailureLogging", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
  });

  it("logs and returns an unsigned quote when signing fails (non-fatal)", async () => {
    const wallet: MessageSigner = {
      address: "0x0000000000000000000000000000000000dEaD",
      signMessage: vi.fn(async () => {
        throw new Error("hardware key offline");
      }),
    };
    const handler = new NegotiationHandler({
      servicePrice: "20000000000000000000",
      currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
      walletProvider: wallet,
      chainId: 97,
      now: () => FIXED_NOW,
    });

    const result = await handler.negotiate(basicRequest());

    // Quote still returned but without sig.
    expect(result.accepted).toBe(true);
    expect(result.negotiationHash).toBe("");
    expect(result.providerSig).toBe("");

    // The failure must be visible to operators.
    const warnedText = warnSpy.mock.calls.map((c) => c.join(" ")).join("\n");
    expect(warnedText).toContain("sign_message failed");
    expect(warnedText).toContain("hardware key offline");
  });
});
