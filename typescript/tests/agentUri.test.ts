/**
 * Ports `python/tests/test_agent_uri.py`: registration-file generation,
 * the `registrations[]` gating (agentId + identityRegistry + chainId all
 * present), the base64 data-URI round trip via `canonicalJson`, and the
 * file-hash helper.
 */

import { describe, expect, it } from "vitest";
import { canonicalJson } from "../src/core/canonicalJson.js";
import { AgentURIGenerator } from "../src/erc8004/agentUri.js";
import { AgentEndpoint } from "../src/erc8004/models.js";

const A2A_ENDPOINT = new AgentEndpoint({
  name: "A2A",
  endpoint: "https://agent.example/.well-known/agent-card.json",
  version: "0.3.0",
});

describe("AgentURIGenerator.generateRegistrationFile", () => {
  it("builds the EIP-8004 registration-file shape", () => {
    const file = AgentURIGenerator.generateRegistrationFile({
      name: "Test Agent",
      description: "A test agent",
      image: "https://example.com/image.png",
      endpoints: [A2A_ENDPOINT],
    });

    expect(file.type).toBe(
      "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    );
    expect(file.name).toBe("Test Agent");
    expect(file.description).toBe("A test agent");
    expect(file.image).toBe("https://example.com/image.png");
    expect((file.services as unknown[]).length).toBe(1);
    expect((file.services as Array<{ name: string }>)[0].name).toBe("A2A");
    expect(file.registrations).toEqual([]);
  });

  it("defaults image to an empty string when omitted", () => {
    const file = AgentURIGenerator.generateRegistrationFile({
      name: "Test",
      description: "Test",
      endpoints: [A2A_ENDPOINT],
    });
    expect(file.image).toBe("");
  });

  it("requires name and description", () => {
    expect(() =>
      AgentURIGenerator.generateRegistrationFile({
        name: "",
        description: "Test",
        endpoints: [A2A_ENDPOINT],
      }),
    ).toThrow(/name and description are required/);
    expect(() =>
      AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "",
        endpoints: [A2A_ENDPOINT],
      }),
    ).toThrow(/name and description are required/);
  });

  it("requires at least one endpoint", () => {
    expect(() =>
      AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "Test",
        endpoints: [],
      }),
    ).toThrow(/endpoints is required/);
  });

  it("accepts plain-object endpoints alongside AgentEndpoint instances", () => {
    const file = AgentURIGenerator.generateRegistrationFile({
      name: "Test",
      description: "Test",
      endpoints: [{ name: "web", endpoint: "https://example.com" }],
    });
    expect((file.services as Array<{ name: string }>)[0].name).toBe("web");
  });

  describe("registrations[] gating", () => {
    it("is empty when agentId is missing", () => {
      const file = AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "Test",
        endpoints: [A2A_ENDPOINT],
        identityRegistry: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        chainId: 97,
      });
      expect(file.registrations).toEqual([]);
    });

    it("is empty when identityRegistry is missing", () => {
      const file = AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "Test",
        endpoints: [A2A_ENDPOINT],
        agentId: 1,
        chainId: 97,
      });
      expect(file.registrations).toEqual([]);
    });

    it("is empty when chainId is missing", () => {
      const file = AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "Test",
        endpoints: [A2A_ENDPOINT],
        agentId: 1,
        identityRegistry: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
      });
      expect(file.registrations).toEqual([]);
    });

    it("populates registrations[] with a CAIP-10-style agentRegistry when all three are present", () => {
      const file = AgentURIGenerator.generateRegistrationFile({
        name: "Test",
        description: "Test",
        endpoints: [A2A_ENDPOINT],
        agentId: 1,
        identityRegistry: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
        chainId: 97,
      });
      expect(file.registrations).toEqual([
        {
          agentId: 1,
          agentRegistry: "eip155:97:0x5FbDB2315678afecb367f032d93F642f64180aa3",
        },
      ]);
    });
  });

  it("includes supportedTrust only when provided and non-empty", () => {
    const withTrust = AgentURIGenerator.generateRegistrationFile({
      name: "Test",
      description: "Test",
      endpoints: [A2A_ENDPOINT],
      supportedTrust: ["reputation"],
    });
    expect(withTrust.supportedTrust).toEqual(["reputation"]);

    const withoutTrust = AgentURIGenerator.generateRegistrationFile({
      name: "Test",
      description: "Test",
      endpoints: [A2A_ENDPOINT],
    });
    expect("supportedTrust" in withoutTrust).toBe(false);
  });
});

describe("AgentURIGenerator.calculateFileHash", () => {
  it("returns a 0x-prefixed keccak hash of the canonical JSON", () => {
    const file = { b: 1, a: 2 };
    const hash = AgentURIGenerator.calculateFileHash(file);
    expect(hash).toMatch(/^0x[0-9a-f]{64}$/);
  });

  it("is stable regardless of key order (matches canonicalJson's sorting)", () => {
    const h1 = AgentURIGenerator.calculateFileHash({ a: 1, b: 2 });
    const h2 = AgentURIGenerator.calculateFileHash({ b: 2, a: 1 });
    expect(h1).toBe(h2);
  });
});

describe("AgentURIGenerator.generateAgentUri", () => {
  it("returns a base64 data URI over canonicalJson (not plain JSON.stringify)", () => {
    const agentUri = AgentURIGenerator.generateAgentUri({
      name: "Test Agent",
      description: "A test agent",
      endpoints: [A2A_ENDPOINT],
    });

    expect(agentUri.startsWith("data:application/json;base64,")).toBe(true);

    const base64Str = agentUri.split(",", 2)[1];
    const decoded = JSON.parse(
      Buffer.from(base64Str, "base64").toString("utf-8"),
    );
    expect(decoded.name).toBe("Test Agent");
    expect(decoded.description).toBe("A test agent");
    expect(decoded.services).toHaveLength(1);

    // The encoded payload must be exactly canonicalJson's output — the
    // cross-SDK interop primitive — not an arbitrary JSON.stringify.
    const file = AgentURIGenerator.generateRegistrationFile({
      name: "Test Agent",
      description: "A test agent",
      endpoints: [A2A_ENDPOINT],
    });
    expect(Buffer.from(base64Str, "base64").toString("utf-8")).toBe(
      canonicalJson(file),
    );
  });

  it("includes a populated registrations[] when agentId/registry/chainId are given", () => {
    const agentUri = AgentURIGenerator.generateAgentUri({
      name: "Test Agent",
      description: "A test agent",
      endpoints: [A2A_ENDPOINT],
      agentId: 1,
      identityRegistry: "0x5FbDB2315678afecb367f032d93F642f64180aa3",
      chainId: 97,
    });

    const decoded = AgentURIGenerator.decodeRegistrationFileFromBase64(
      agentUri,
    ) as { registrations: Array<{ agentId: number; agentRegistry: string }> };
    expect(decoded.registrations).toHaveLength(1);
    expect(decoded.registrations[0].agentId).toBe(1);
    expect(decoded.registrations[0].agentRegistry).toBe(
      "eip155:97:0x5FbDB2315678afecb367f032d93F642f64180aa3",
    );
  });
});

describe("AgentURIGenerator base64 encode/decode round trip", () => {
  it("round-trips a registration file", () => {
    const registrationFile = {
      type: "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
      name: "Test Agent",
      description: "A test agent",
      image: "",
      services: [],
      registrations: [],
    };

    const base64Str =
      AgentURIGenerator.encodeRegistrationFileToBase64(registrationFile);
    expect(typeof base64Str).toBe("string");

    const decoded =
      AgentURIGenerator.decodeRegistrationFileFromBase64(base64Str);
    expect(decoded.name).toBe(registrationFile.name);
    expect(decoded.description).toBe(registrationFile.description);
  });

  it("decodes with the data: URI prefix present", () => {
    const registrationFile = {
      name: "Test Agent",
      description: "A test agent",
    };
    const base64Str =
      AgentURIGenerator.encodeRegistrationFileToBase64(registrationFile);
    const dataUri = `data:application/json;base64,${base64Str}`;

    const decoded = AgentURIGenerator.decodeRegistrationFileFromBase64(dataUri);
    expect(decoded.name).toBe(registrationFile.name);
  });

  it("encodes non-ASCII text as \\uXXXX escapes, matching canonicalJson/Python ensure_ascii", () => {
    const registrationFile = { name: "é日本" };
    const base64Str =
      AgentURIGenerator.encodeRegistrationFileToBase64(registrationFile);
    const raw = Buffer.from(base64Str, "base64").toString("utf-8");
    expect(raw).toBe(canonicalJson(registrationFile));
    expect([...raw].every((ch) => ch.charCodeAt(0) <= 0x7f)).toBe(true);
  });
});
