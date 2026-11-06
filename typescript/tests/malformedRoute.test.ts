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

it("responds 400 for invalid parameter encoding and continues serving", async () => {
  const handler = vi.fn(({ res }) => sendJson(res, 200, { ok: true }));
  const listener = makeRequestListener([route("GET", "/job/:id", handler)]);
  const base = await listen(
    createServer((req, res) => {
      void listener(req, res);
    }),
  );
  expect((await fetch(`${base}/job/%E0%A4%A`)).status).toBe(400);
  expect(handler).not.toHaveBeenCalled();
  expect((await fetch(`${base}/job/1`)).status).toBe(200);
  expect(handler).toHaveBeenCalledOnce();
});
