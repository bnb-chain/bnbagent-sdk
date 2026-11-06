import { describe, expect, it } from "vitest";
import { ERC8004Agent } from "../src/erc8004/agent.js";
import { AgentURIGenerator } from "../src/erc8004/agentUri.js";

describe("data URI registration bounds", () => {
  it("rejects oversized decoded registration before JSON parsing", async () => {
    const payload = Buffer.from(
      JSON.stringify({ name: "x".repeat(1024 * 1024) }),
    ).toString("base64");
    await expect(
      ERC8004Agent.parseAgentUri(`data:application/json;base64,${payload}`),
    ).resolves.toBeNull();
  });
  it.each(["[]", "null", "1"])("rejects non-object registration %s", (json) => {
    expect(() =>
      AgentURIGenerator.decodeRegistrationFileFromBase64(
        Buffer.from(json).toString("base64"),
      ),
    ).toThrow(/object/);
  });
  it("continues decoding ordinary object registrations", () => {
    expect(
      AgentURIGenerator.decodeRegistrationFileFromBase64(
        Buffer.from('{"name":"ok"}').toString("base64"),
      ),
    ).toEqual({ name: "ok" });
  });
});
