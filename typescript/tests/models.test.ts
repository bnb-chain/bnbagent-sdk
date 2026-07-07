/**
 * Ports `python/tests/test_models.py`: `AgentEndpoint` construction,
 * validation, `toDict`/`fromDict`, and the `a2a()`/`mcp()` protocol-aware
 * constructors.
 */

import { describe, expect, it } from "vitest";
import { AgentEndpoint } from "../src/erc8004/models.js";

describe("AgentEndpoint", () => {
  it("creates an endpoint with required fields only", () => {
    const endpoint = new AgentEndpoint({
      name: "A2A",
      endpoint: "https://agent.example/.well-known/agent-card.json",
    });

    expect(endpoint.name).toBe("A2A");
    expect(endpoint.endpoint).toBe(
      "https://agent.example/.well-known/agent-card.json",
    );
    expect(endpoint.version).toBeNull();
    expect(endpoint.capabilities).toEqual([]);
  });

  it("creates an endpoint with optional fields", () => {
    const endpoint = new AgentEndpoint({
      name: "MCP",
      endpoint: "https://mcp.agent.example/",
      version: "2025-06-18",
      capabilities: ["tools", "prompts"],
    });

    expect(endpoint.name).toBe("MCP");
    expect(endpoint.endpoint).toBe("https://mcp.agent.example/");
    expect(endpoint.version).toBe("2025-06-18");
    expect(endpoint.capabilities).toEqual(["tools", "prompts"]);
  });

  it("requires name", () => {
    expect(
      () => new AgentEndpoint({ name: "", endpoint: "https://example.com" }),
    ).toThrow(/name is required/);
  });

  it("requires endpoint", () => {
    expect(() => new AgentEndpoint({ name: "A2A", endpoint: "" })).toThrow(
      /endpoint is required/,
    );
  });

  it("rejects a non-http(s) endpoint", () => {
    expect(
      () => new AgentEndpoint({ name: "A2A", endpoint: "invalid-url" }),
    ).toThrow(/endpoint must start with http:\/\/ or https:\/\//);
    expect(
      () => new AgentEndpoint({ name: "A2A", endpoint: "ftp://example.com" }),
    ).toThrow(/endpoint must start with http:\/\/ or https:\/\//);
  });

  it("toDict includes optional fields when present", () => {
    const endpoint = new AgentEndpoint({
      name: "A2A",
      endpoint: "https://agent.example/.well-known/agent-card.json",
      version: "0.3.0",
      capabilities: ["tools"],
    });
    const dict = endpoint.toDict();
    expect(dict.name).toBe("A2A");
    expect(dict.endpoint).toBe(
      "https://agent.example/.well-known/agent-card.json",
    );
    expect(dict.version).toBe("0.3.0");
    expect(dict.capabilities).toEqual(["tools"]);
  });

  it("toDict omits null/empty optional fields", () => {
    const endpoint = new AgentEndpoint({
      name: "A2A",
      endpoint: "https://agent.example/.well-known/agent-card.json",
    });
    const dict = endpoint.toDict();
    expect("version" in dict).toBe(false);
    expect("capabilities" in dict).toBe(false);
  });

  it("fromDict builds an endpoint from a plain object", () => {
    const endpoint = AgentEndpoint.fromDict({
      name: "A2A",
      endpoint: "https://agent.example/.well-known/agent-card.json",
      version: "0.3.0",
    });
    expect(endpoint.name).toBe("A2A");
    expect(endpoint.endpoint).toBe(
      "https://agent.example/.well-known/agent-card.json",
    );
    expect(endpoint.version).toBe("0.3.0");
  });

  it("fromDict requires name and endpoint", () => {
    expect(() => AgentEndpoint.fromDict({ name: "A2A" })).toThrow(
      /must contain 'name' and 'endpoint' fields/,
    );
    expect(() =>
      AgentEndpoint.fromDict({ endpoint: "https://example.com" }),
    ).toThrow(/must contain 'name' and 'endpoint' fields/);
  });
});

describe("AgentEndpoint.a2a", () => {
  it("appends the well-known path to a bare base URL", () => {
    const ep = AgentEndpoint.a2a("https://agent.example");
    expect(ep.name).toBe("A2A");
    expect(ep.endpoint).toBe(
      "https://agent.example/.well-known/agent-card.json",
    );
  });

  it("normalizes a trailing slash before appending", () => {
    const ep = AgentEndpoint.a2a("https://agent.example/");
    expect(ep.endpoint).toBe(
      "https://agent.example/.well-known/agent-card.json",
    );
  });

  it("does not double the path when already present", () => {
    const url = "https://agent.example/.well-known/agent-card.json";
    expect(AgentEndpoint.a2a(url).endpoint).toBe(url);
  });

  it("appends after an existing path segment", () => {
    const ep = AgentEndpoint.a2a("https://host.example/agents/foo");
    expect(ep.endpoint).toBe(
      "https://host.example/agents/foo/.well-known/agent-card.json",
    );
  });

  it("passes version and capabilities through", () => {
    const ep = AgentEndpoint.a2a("https://agent.example", {
      version: "0.3.0",
      capabilities: ["chat"],
    });
    expect(ep.version).toBe("0.3.0");
    expect(ep.capabilities).toEqual(["chat"]);
  });

  it("rejects a non-http(s) scheme", () => {
    expect(() => AgentEndpoint.a2a("ftp://agent.example")).toThrow(/http/);
  });
});

describe("AgentEndpoint.mcp", () => {
  it("uses the bare URL and version, per the EIP-8004 example", () => {
    const ep = AgentEndpoint.mcp("https://agent.example/mcp", {
      version: "2025-06-18",
    });
    expect(ep.name).toBe("MCP");
    expect(ep.endpoint).toBe("https://agent.example/mcp");
    expect(ep.version).toBe("2025-06-18");
    expect(ep.capabilities).toEqual([]);
  });

  it("passes capabilities through verbatim", () => {
    const ep = AgentEndpoint.mcp("https://agent.example/mcp", {
      capabilities: ["tools"],
    });
    expect(ep.capabilities).toEqual(["tools"]);
  });

  it("rejects a stdio URL (no registrable http(s) address)", () => {
    expect(() => AgentEndpoint.mcp("stdio://local")).toThrow(/http/);
  });

  it("round-trips through toDict/fromDict", () => {
    const ep = AgentEndpoint.mcp("https://agent.example/mcp", {
      version: "2025-06-18",
    });
    const again = AgentEndpoint.fromDict(ep.toDict());
    expect(again.name).toBe(ep.name);
    expect(again.endpoint).toBe(ep.endpoint);
    expect(again.version).toBe(ep.version);
    expect(again.capabilities).toEqual(ep.capabilities);
  });
});
