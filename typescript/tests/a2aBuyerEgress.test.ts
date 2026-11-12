import { once } from "node:events";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, it, vi } from "vitest";
import { sendSkill } from "../examples/a2a-agent/scripts/buyer.js";
it("refuses registry-discovered private POST targets without opening a connection", async () => {
  const hit = vi.fn();
  const server = createServer((_, res) => {
    hit();
    res.end("{}");
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/`;
    await expect(sendSkill(url, { skill: "status" }, true)).rejects.toThrow(
      /refused or failed/,
    );
    expect(hit).not.toHaveBeenCalled();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
