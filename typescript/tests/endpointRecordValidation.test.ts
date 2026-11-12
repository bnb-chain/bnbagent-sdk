import { describe, expect, it } from "vitest";
import { AgentURIGenerator } from "../src/erc8004/agentUri.js";

describe("plain endpoint record validation", () => {
  it("rejects a file endpoint just like an AgentEndpoint instance", () => {
    expect(() =>
      AgentURIGenerator.generateAgentUri({
        name: "agent",
        description: "test",
        endpoints: [{ name: "A2A", endpoint: "file:///private" }],
      }),
    ).toThrow(/http/);
  });
  it("normalizes supported plain endpoints", () => {
    const uri = AgentURIGenerator.generateAgentUri({
      name: "agent",
      description: "test",
      endpoints: [{ name: "A2A", endpoint: "https://agent.example/a2a" }],
    });
    expect(
      AgentURIGenerator.decodeRegistrationFileFromBase64(uri).services,
    ).toEqual([{ name: "A2A", endpoint: "https://agent.example/a2a" }]);
  });
});
