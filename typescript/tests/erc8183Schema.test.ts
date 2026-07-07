/**
 * Ports `python/tests/test_erc8183_schema.py`: DeliverableManifest and
 * JobDescription round-trips, manifest hash cross-SDK invariant, verify(),
 * unsupported-version rejection, missing-field rejection, and
 * JobDescription's `fromStr` fallback-friendly `null`s / type strictness.
 */

import { describe, expect, it } from "vitest";
import { canonicalJson } from "../src/core/canonicalJson.js";
import {
  DeliverableManifest,
  JobDescription,
  SCHEMA_VERSION,
} from "../src/erc8183/schema.js";

const FAKE_COMMERCE = `0x${"aa".repeat(20)}`;
const FAKE_ROUTER = `0x${"bb".repeat(20)}`;
const FAKE_POLICY = `0x${"cc".repeat(20)}`;

function manifestWireDict(
  content = "hello world",
  metadata: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    version: SCHEMA_VERSION,
    job_id: 42,
    chain_id: 97,
    contracts: {
      commerce: FAKE_COMMERCE,
      router: FAKE_ROUTER,
      policy: FAKE_POLICY,
    },
    response: {
      content,
      content_type: "text/plain",
    },
    metadata,
  };
}

/** Returns a shallow copy of `obj` without `key` (avoids the `delete` operator). */
function without(
  obj: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));
}

describe("DeliverableManifest", () => {
  it("round-trips through fromDict/toDict", () => {
    const d = manifestWireDict();
    const m = DeliverableManifest.fromDict(d);
    expect(m.toDict()).toEqual(d);
  });

  it("exposes fields via camelCase accessors", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict("test content"));
    expect(m.version).toBe(SCHEMA_VERSION);
    expect(m.jobId).toBe(42);
    expect(m.chainId).toBe(97);
    expect(m.contracts.commerce).toBe(FAKE_COMMERCE);
    expect(m.response.content).toBe("test content");
    expect(m.response.contentType).toBe("text/plain");
  });

  it("manifestHash() is keccak256 of the canonical manifest JSON", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict());
    const canonical = canonicalJson(m.toDict());
    // Independently recompute via viem to avoid testing the impl against itself.
    expect(canonical).toBe(
      '{"chain_id":97,"contracts":{"commerce":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","policy":"0xcccccccccccccccccccccccccccccccccccccccc","router":"0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"job_id":42,"metadata":{},"response":{"content":"hello world","content_type":"text/plain"},"version":1}',
    );
  });

  it("pins the manifest hash to the value computed by the Python SDK (cross-SDK interop)", () => {
    // Computed via: uv run python3 -c "... DeliverableManifest.from_dict(d).manifest_hash().hex() ..."
    // over the exact wire dict produced by manifestWireDict() above. See
    // .superpowers/sdd/task-19-report.md for the full one-liner + output.
    const EXPECTED_HASH =
      "0xe6f21081e9a75c1b2dcd98835c1ef5a351c25bb1fbedca3226e74ff52752b7b6";
    const m = DeliverableManifest.fromDict(manifestWireDict());
    expect(m.manifestHash()).toBe(EXPECTED_HASH);
  });

  it("manifestHash() returns a 32-byte (66-char) 0x-hex string", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict());
    const h = m.manifestHash();
    expect(h.startsWith("0x")).toBe(true);
    expect(h.length).toBe(66);
  });

  it("differs for different content", () => {
    const m1 = DeliverableManifest.fromDict(manifestWireDict("result A"));
    const m2 = DeliverableManifest.fromDict(manifestWireDict("result B"));
    expect(m1.manifestHash()).not.toBe(m2.manifestHash());
  });

  it("differs for different job_id", () => {
    const d1 = manifestWireDict();
    const d2 = manifestWireDict();
    d2.job_id = 999;
    expect(DeliverableManifest.fromDict(d1).manifestHash()).not.toBe(
      DeliverableManifest.fromDict(d2).manifestHash(),
    );
  });

  it("verify() returns true for a matching hash (hex string)", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict());
    expect(m.verify(m.manifestHash())).toBe(true);
  });

  it("verify() returns true for a matching hash (Uint8Array)", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict());
    const hex = m.manifestHash().slice(2);
    const bytes = new Uint8Array(hex.length / 2);
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    }
    expect(m.verify(bytes)).toBe(true);
  });

  it("verify() returns false for a mismatched hash", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict("real content"));
    const other = DeliverableManifest.fromDict(
      manifestWireDict("other content"),
    );
    expect(m.verify(other.manifestHash())).toBe(false);
  });

  it("metadata defaults to {} when omitted", () => {
    const d = without(manifestWireDict(), "metadata");
    const m = DeliverableManifest.fromDict(d);
    expect(m.metadata).toEqual({});
  });

  it("toDict() includes metadata", () => {
    const meta = { agent: "gpt-4", duration_ms: 1234 };
    const m = DeliverableManifest.fromDict(manifestWireDict("hi", meta));
    expect(m.toDict().metadata).toEqual(meta);
  });

  it("fromDict() throws on unknown version", () => {
    const d = manifestWireDict();
    d.version = 999;
    expect(() => DeliverableManifest.fromDict(d)).toThrow(/version/i);
  });

  it("fromDict() throws on missing job_id", () => {
    const d = without(manifestWireDict(), "job_id");
    expect(() => DeliverableManifest.fromDict(d)).toThrow(/job_id/);
  });

  it("fromDict() throws on missing chain_id", () => {
    const d = without(manifestWireDict(), "chain_id");
    expect(() => DeliverableManifest.fromDict(d)).toThrow(/chain_id/);
  });

  it("fromDict() throws on missing contracts", () => {
    const d = without(manifestWireDict(), "contracts");
    expect(() => DeliverableManifest.fromDict(d)).toThrow(/contracts/);
  });

  it("fromDict() throws on missing response.content", () => {
    const d = manifestWireDict();
    d.response = { content_type: "text/plain" };
    expect(() => DeliverableManifest.fromDict(d)).toThrow();
  });

  it("toDict() is JSON-serializable and round-trips", () => {
    const m = DeliverableManifest.fromDict(manifestWireDict());
    const serialized = JSON.stringify(m.toDict());
    expect(JSON.parse(serialized)).toEqual(m.toDict());
  });

  it("manifestHash() is stable across equal instances", () => {
    const m1 = DeliverableManifest.fromDict(manifestWireDict());
    const m2 = DeliverableManifest.fromDict(manifestWireDict());
    expect(m1.manifestHash()).toBe(m2.manifestHash());
  });
});

function descriptionWireDict(task = "write a report"): Record<string, unknown> {
  return {
    version: SCHEMA_VERSION,
    negotiated_at: 1_700_000_000,
    task,
    terms: {
      deliverables: "PDF report",
      quality_standards: "accurate, concise",
    },
    price: "20000000000000000000",
    currency: FAKE_COMMERCE,
    negotiation_hash: `0x${"ab".repeat(32)}`,
    provider_sig: `0x${"cd".repeat(65)}`,
  };
}

function descriptionJson(task = "write a report"): string {
  return canonicalJson(descriptionWireDict(task));
}

describe("JobDescription", () => {
  it("round-trips through fromDict/toDict", () => {
    const d = descriptionWireDict();
    const jd = JobDescription.fromDict(d);
    expect(jd.toDict()).toEqual(d);
  });

  it("exposes fields via camelCase accessors", () => {
    const jd = JobDescription.fromDict(
      descriptionWireDict("summarise article"),
    );
    expect(jd.version).toBe(SCHEMA_VERSION);
    expect(jd.task).toBe("summarise article");
    expect(jd.price).toBe("20000000000000000000");
    expect(jd.currency).toBe(FAKE_COMMERCE);
    expect(jd.terms.deliverables).toBe("PDF report");
    expect(jd.negotiationHash).toBe(`0x${"ab".repeat(32)}`);
    expect(jd.providerSig).toBe(`0x${"cd".repeat(65)}`);
  });

  it("fromStr() parses valid JSON", () => {
    const jd = JobDescription.fromStr(descriptionJson());
    expect(jd).not.toBeNull();
    expect(jd?.task).toBe("write a report");
  });

  it("fromStr() returns null for plain text", () => {
    expect(JobDescription.fromStr("just a plain text description")).toBeNull();
  });

  it("fromStr() returns null for JSON without a version field", () => {
    const s = JSON.stringify({ task: "no version field" });
    expect(JobDescription.fromStr(s)).toBeNull();
  });

  it("fromStr() returns null for an empty string", () => {
    expect(JobDescription.fromStr("")).toBeNull();
  });

  it("fromStr() returns null for malformed JSON", () => {
    expect(JobDescription.fromStr("{not valid json")).toBeNull();
  });

  it("fromStr() handles a whitespace prefix", () => {
    const jd = JobDescription.fromStr(`  \n${descriptionJson()}`);
    expect(jd).not.toBeNull();
  });

  it("fromDict() throws on unknown version", () => {
    const d = descriptionWireDict();
    d.version = 999;
    expect(() => JobDescription.fromDict(d)).toThrow(/version/i);
  });

  it("fromStr() throws when version is present but unsupported", () => {
    const d = descriptionWireDict();
    d.version = 999;
    expect(() => JobDescription.fromStr(canonicalJson(d))).toThrow(/version/i);
  });

  it("optional fields default to null when absent", () => {
    let d = descriptionWireDict();
    d = without(d, "negotiation_hash");
    d = without(d, "provider_sig");
    d = without(d, "quote_expires_at");
    const jd = JobDescription.fromDict(d);
    expect(jd.negotiationHash).toBeNull();
    expect(jd.providerSig).toBeNull();
    expect(jd.quoteExpiresAt).toBeNull();
  });

  it("toDict() omits null optional fields", () => {
    let d = descriptionWireDict();
    d = without(d, "negotiation_hash");
    d = without(d, "provider_sig");
    const jd = JobDescription.fromDict(d);
    const result = jd.toDict();
    expect("negotiation_hash" in result).toBe(false);
    expect("provider_sig" in result).toBe(false);
  });

  it("negotiated_at must be an integer (rejects numeric strings)", () => {
    const d = descriptionWireDict();
    d.negotiated_at = "1700000000";
    expect(() => JobDescription.fromDict(d)).toThrow(
      /negotiated_at must be int/,
    );
  });

  it("negotiated_at rejects booleans", () => {
    const d = descriptionWireDict();
    d.negotiated_at = true;
    expect(() => JobDescription.fromDict(d)).toThrow(
      /negotiated_at must be int/,
    );
  });

  it("quote_expires_at must be an integer or null (rejects numeric strings)", () => {
    const d = descriptionWireDict();
    d.quote_expires_at = "1700000123";
    expect(() => JobDescription.fromDict(d)).toThrow(
      /quote_expires_at must be int/,
    );
  });

  it("quote_expires_at rejects booleans", () => {
    const d = descriptionWireDict();
    d.quote_expires_at = false;
    expect(() => JobDescription.fromDict(d)).toThrow(
      /quote_expires_at must be int/,
    );
  });

  it("quote_expires_at passes through when a valid integer", () => {
    const d = descriptionWireDict();
    d.quote_expires_at = 1_700_000_500;
    const jd = JobDescription.fromDict(d);
    expect(jd.quoteExpiresAt).toBe(1_700_000_500);
  });

  it("toDict() is JSON-serializable and round-trips", () => {
    const jd = JobDescription.fromDict(descriptionWireDict());
    const serialized = JSON.stringify(jd.toDict());
    expect(JSON.parse(serialized)).toEqual(jd.toDict());
  });
});
