import type { IncomingMessage } from "node:http";
import { describe, expect, it } from "vitest";
import { clientIpOf } from "../examples/agent-server/src/http.js";

function request(
  remoteAddress: string,
  forwardedFor?: string,
): IncomingMessage {
  return {
    headers: forwardedFor ? { "x-forwarded-for": forwardedFor } : {},
    socket: { remoteAddress },
  } as IncomingMessage;
}

describe("agent-server client IP trust", () => {
  it("ignores X-Forwarded-For from an untrusted peer", () => {
    expect(clientIpOf(request("203.0.113.10", "198.51.100.1"))).toBe(
      "203.0.113.10",
    );
  });

  it("uses the rightmost untrusted hop behind trusted proxies", () => {
    const trusted = new Set(["127.0.0.1", "10.0.0.2"]);
    expect(
      clientIpOf(
        request("::ffff:127.0.0.1", "192.0.2.9, 198.51.100.7, 10.0.0.2"),
        trusted,
      ),
    ).toBe("198.51.100.7");
  });

  it("does not select a spoofed leftmost value", () => {
    const trusted = new Set(["127.0.0.1"]);
    expect(
      clientIpOf(request("127.0.0.1", "192.0.2.200, 198.51.100.8"), trusted),
    ).toBe("198.51.100.8");
  });
});
