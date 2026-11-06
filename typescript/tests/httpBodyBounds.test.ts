import { once } from "node:events";
import { type Server, createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  makeRequestListener,
  route,
  sendJson,
} from "../examples/agent-server/src/http.js";
const servers: Server[] = [];
afterEach(async () => {
  for (const server of servers.splice(0)) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
async function listen(server: Server): Promise<string> {
  servers.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}
import type { IncomingMessage } from "node:http";
import { PassThrough } from "node:stream";
import {
  HttpInputError,
  readJsonBody,
} from "../examples/agent-server/src/http.js";
describe("HTTP body admission", () => {
  it("checks admission before reading an oversized or invalid body", async () => {
    const handler = vi.fn();
    const listener = makeRequestListener(
      [route("POST", "/negotiate", handler)],
      {
        beforeBody: (_req, res) => {
          res.setHeader("Connection", "close");
          sendJson(res, 429, { error: "Rate limited" });
          return false;
        },
      },
    );
    const base = await listen(
      createServer((req, res) => {
        void listener(req, res);
      }),
    );
    expect(
      (
        await fetch(`${base}/negotiate`, {
          method: "POST",
          body: "x".repeat(65 * 1024),
        })
      ).status,
    ).toBe(429);
    expect(handler).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "rejects oversize declared=%s before handler work",
    async (declared) => {
      const handler = vi.fn(({ res }) => sendJson(res, 200, { ok: true }));
      const listener = makeRequestListener([
        route("POST", "/negotiate", handler),
      ]);
      const base = await listen(
        createServer((req, res) => {
          void listener(req, res);
        }),
      );
      const response = await fetch(`${base}/negotiate`, {
        method: "POST",
        body: "x".repeat(65537),
        headers: declared ? { "content-length": "65537" } : {},
      });
      expect(response.status).toBe(413);
      expect(handler).not.toHaveBeenCalled();
    },
  );
  it("ends a stalled stream at the body deadline", async () => {
    const req = new PassThrough() as unknown as IncomingMessage;
    Object.assign(req, { headers: {} });
    const result = readJsonBody(req, 1024, 5);
    await expect(result).rejects.toBeInstanceOf(HttpInputError);
    req.destroy();
  });
});
