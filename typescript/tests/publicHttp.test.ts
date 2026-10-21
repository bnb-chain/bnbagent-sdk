import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { lookup, request } = vi.hoisted(() => ({
  lookup: vi.fn(),
  request: vi.fn(),
}));
vi.mock("node:dns/promises", () => ({ lookup }));
vi.mock("node:http", () => ({ request }));
vi.mock("node:https", () => ({ request }));

import { fetchManifest } from "../examples/voter/watch.js";
import {
  fetchPublicJson,
  isBlockedIp,
  publicGatewayUrl,
} from "../src/utils/publicHttp.js";

const manifest = {
  version: 1,
  job_id: 1,
  chain_id: 97,
  contracts: {},
  response: { content: "ok" },
};

function respond(body = JSON.stringify(manifest), status = 200, headers = {}) {
  request.mockImplementation((_options, callback) => {
    const req = Object.assign(new EventEmitter(), {
      destroy: vi.fn(),
      end: () => {
        queueMicrotask(() => {
          const res = Object.assign(new EventEmitter(), {
            statusCode: status,
            headers,
            destroy: vi.fn(),
          });
          callback(res);
          res.emit("data", Buffer.from(body));
          res.emit("end");
        });
      },
    });
    return req;
  });
}

beforeEach(() => {
  lookup.mockReset().mockResolvedValue([{ address: "8.8.8.8", family: 4 }]);
  request.mockReset();
  respond();
});
afterEach(() => vi.useRealTimers());

describe("public downloads", () => {
  it.each([
    "::",
    "::1",
    "ff02::1",
    "fe80::1%lo0",
    "64:ff9b::7f00:1",
    "64:ff9b:1::1",
    "2002:7f00:1::",
    "2001::1",
    "3fff::1",
    "::ffff:7f00:1",
    "224.0.0.1",
    "192.88.99.1",
  ])("blocks %s", (ip) => {
    expect(isBlockedIp(ip)).toBe(true);
  });
  it.each(["8.8.8.8", "2606:4700:4700::1111", "::ffff:808:808"])(
    "accepts native public or mapped address %s",
    (ip) => {
      expect(isBlockedIp(ip)).toBe(false);
    },
  );
  it.each([
    "file:///etc/passwd",
    "http://127.0.0.1/",
    "http://[::]/",
    "http://user:secret@8.8.8.8/",
    "http://8.8.8.8:0/",
    "http://metadata.google.internal./",
  ])("refuses %s before HTTP", async (url) => {
    expect(await fetchPublicJson(url)).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
  it("refuses mixed DNS answers", async () => {
    lookup.mockResolvedValue([
      { address: "8.8.8.8", family: 4 },
      { address: "::", family: 6 },
    ]);
    expect(await fetchPublicJson("http://agent.example/")).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
  it("pins IPv6 while keeping HTTPS hostname validation and Host", async () => {
    lookup.mockResolvedValue([{ address: "2606:4700:4700::1111", family: 6 }]);
    expect(
      await fetchPublicJson("https://agent.example:8443/manifest"),
    ).toEqual(manifest);
    expect(request.mock.calls[0][0]).toMatchObject({
      host: "2606:4700:4700::1111",
      servername: "agent.example",
      headers: { Host: "agent.example:8443" },
      agent: false,
    });
    expect(request.mock.calls[0][0].rejectUnauthorized).not.toBe(false);
    expect(lookup).toHaveBeenCalledTimes(1);
  });
  it("supports public IPv6 literals without re-resolving", async () => {
    expect(
      await fetchPublicJson("http://[2606:4700:4700::1111]/manifest"),
    ).toEqual(manifest);
    expect(lookup).not.toHaveBeenCalled();
    expect(request.mock.calls[0][0].headers.Host).toBe(
      "[2606:4700:4700::1111]",
    );
  });
  it.each([301, 302, 307, 308])(
    "does not follow redirect %s",
    async (status) => {
      respond("", status, { location: "http://127.0.0.1/" });
      expect(await fetchPublicJson("http://agent.example/")).toBeNull();
      expect(request).toHaveBeenCalledTimes(1);
    },
  );
  it("bounds streamed bytes even without Content-Length", async () => {
    respond("x".repeat(33));
    expect(
      await fetchPublicJson("http://agent.example/", { maxBytes: 32 }),
    ).toBeNull();
  });
  it("refuses encoded and non-object JSON", async () => {
    respond("{}", 200, { "content-encoding": "gzip" });
    expect(await fetchPublicJson("http://agent.example/")).toBeNull();
    respond("[]");
    expect(await fetchPublicJson("http://agent.example/")).toBeNull();
  });
  it("bounds the entire response even if no socket timeout fires", async () => {
    vi.useFakeTimers();
    const destroy = vi.fn();
    request.mockReturnValue(
      Object.assign(new EventEmitter(), { destroy, end: vi.fn() }),
    );
    const pending = fetchPublicJson("http://8.8.8.8/", { timeoutMs: 20 });
    await vi.advanceTimersByTimeAsync(21);
    expect(await pending).toBeNull();
    expect(destroy).toHaveBeenCalledOnce();
  });
  it("voter refuses private URL and still accepts the next public manifest", async () => {
    expect(
      await fetchManifest("http://127.0.0.1/", "https://gateway.example/ipfs"),
    ).toBeNull();
    expect(request).not.toHaveBeenCalled();
    expect(
      await fetchManifest(
        "https://agent.example/manifest",
        "https://gateway.example/ipfs",
      ),
    ).not.toBeNull();
  });
  it.each(["../secret", "//localhost", "bad?redirect=x", "%2f.."])(
    "rejects IPFS path %s",
    (cid) => {
      expect(() =>
        publicGatewayUrl(`ipfs://${cid}`, "https://gateway.example/ipfs/"),
      ).toThrow();
    },
  );
  it("expands a valid CID", () => {
    const cid = `Qm${"a".repeat(44)}`;
    expect(
      publicGatewayUrl(`ipfs://${cid}`, "https://gateway.example/ipfs/"),
    ).toBe(`https://gateway.example/ipfs/${cid}`);
  });
});
