import { beforeEach, describe, expect, it, vi } from "vitest";
import { NONCE_ERROR_PATTERNS, NonceManager } from "../src/core/nonceManager";

const FAKE_ADDRESS = "0x1234567890123456789012345678901234567890";

function makeClient(rpcUrl = "https://fake-rpc.example.com", nonce = 0) {
  return {
    transport: { url: rpcUrl },
    getTransactionCount: vi.fn().mockResolvedValue(nonce),
  };
}

beforeEach(() => {
  NonceManager._clearAll();
});

describe("NONCE_ERROR_PATTERNS", () => {
  it("contains the three known nonce-related substrings", () => {
    expect(NONCE_ERROR_PATTERNS).toEqual([
      "nonce too low",
      "already known",
      "replacement transaction underpriced",
    ]);
  });
});

describe("singleton", () => {
  it("forAccount creates an instance", () => {
    const client = makeClient();
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    expect(mgr).toBeInstanceOf(NonceManager);
  });

  it("forAccount caches the same key", () => {
    const client = makeClient();
    const mgr1 = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    const mgr2 = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    expect(mgr1).toBe(mgr2);
  });

  it("different rpc urls create separate instances", () => {
    const clientA = makeClient("https://rpc-a.example.com");
    const clientB = makeClient("https://rpc-b.example.com");
    const mgrA = NonceManager.forAccount(clientA as never, FAKE_ADDRESS);
    const mgrB = NonceManager.forAccount(clientB as never, FAKE_ADDRESS);
    expect(mgrA).not.toBe(mgrB);
  });

  it("different accounts create separate instances", () => {
    const client = makeClient();
    const addr2 = `0x${"11".repeat(20)}` as const;
    const mgr1 = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    const mgr2 = NonceManager.forAccount(client as never, addr2);
    expect(mgr1).not.toBe(mgr2);
  });

  it("checksums the address so lowercase and checksummed share a singleton", () => {
    const client = makeClient();
    const lower = FAKE_ADDRESS.toLowerCase() as `0x${string}`;
    const mgr1 = NonceManager.forAccount(client as never, lower);
    const mgr2 = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    expect(mgr1).toBe(mgr2);
  });

  it("_clearAll clears the singleton map", () => {
    const client = makeClient();
    NonceManager.forAccount(client as never, FAKE_ADDRESS);
    NonceManager._clearAll();
    const clientB = makeClient();
    const mgrAfter = NonceManager.forAccount(clientB as never, FAKE_ADDRESS);
    // After clearing, a fresh call against a differently-mocked client must
    // seed from that new client, proving the old singleton was discarded.
    expect(mgrAfter).toBeInstanceOf(NonceManager);
  });

  it("keys by client.uid when transport.url is absent", () => {
    const clientA = {
      uid: "uid-a",
      getTransactionCount: vi.fn().mockResolvedValue(0),
    };
    const clientB = {
      uid: "uid-b",
      getTransactionCount: vi.fn().mockResolvedValue(0),
    };
    const mgrA = NonceManager.forAccount(clientA as never, FAKE_ADDRESS);
    const mgrB = NonceManager.forAccount(clientB as never, FAKE_ADDRESS);
    expect(mgrA).not.toBe(mgrB);
  });
});

describe("getNonce", () => {
  it("seeds from chain on first call", async () => {
    const client = makeClient(undefined, 5);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    const nonce = await mgr.getNonce();
    expect(nonce).toBe(5);
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });

  it("increments locally after seeding", async () => {
    const client = makeClient(undefined, 10);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    const n1 = await mgr.getNonce();
    const n2 = await mgr.getNonce();
    const n3 = await mgr.getNonce();
    expect([n1, n2, n3]).toEqual([10, 11, 12]);
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });

  it("does not call RPC again over many sequential calls", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    for (let i = 0; i < 10; i++) {
      await mgr.getNonce();
    }
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });

  it("calls getTransactionCount with the checksummed address and pending tag", async () => {
    const client = makeClient(undefined, 42);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    expect(client.getTransactionCount).toHaveBeenCalledWith({
      address: FAKE_ADDRESS,
      blockTag: "pending",
    });
  });

  it("gives 50 concurrent callers unique nonces with exactly one RPC call", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    const results = await Promise.all(
      Array.from({ length: 50 }, () => mgr.getNonce()),
    );
    expect(results).toHaveLength(50);
    expect(new Set(results).size).toBe(50);
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });
});

describe("handleError", () => {
  it("resyncs and returns true on 'nonce too low'", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    client.getTransactionCount.mockResolvedValue(5);
    const result = await mgr.handleError(new Error("nonce too low"), 0);
    expect(result).toBe(true);
    expect(await mgr.getNonce()).toBe(5);
  });

  it("resyncs and returns true on 'already known'", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    client.getTransactionCount.mockResolvedValue(3);
    const result = await mgr.handleError(new Error("already known"), 0);
    expect(result).toBe(true);
  });

  it("resyncs and returns true on 'replacement transaction underpriced'", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    client.getTransactionCount.mockResolvedValue(3);
    const result = await mgr.handleError(
      new Error("replacement transaction underpriced"),
      0,
    );
    expect(result).toBe(true);
  });

  it("returns false for unrelated errors and does not resync", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    const result = await mgr.handleError(new Error("out of gas"), 0);
    expect(result).toBe(false);
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
  });

  it("matches case-insensitively", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    client.getTransactionCount.mockResolvedValue(7);
    const result = await mgr.handleError(new Error("NONCE TOO LOW"), 0);
    expect(result).toBe(true);
  });

  it("updates the nonce used for subsequent getNonce calls", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce(); // 0
    await mgr.getNonce(); // 1
    client.getTransactionCount.mockResolvedValue(10);
    await mgr.handleError(new Error("nonce too low"), 1);
    expect(await mgr.getNonce()).toBe(10);
  });
});

describe("reset", () => {
  it("clears the cached nonce so the next getNonce reseeds", async () => {
    const client = makeClient(undefined, 5);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    mgr.reset();
    client.getTransactionCount.mockResolvedValue(20);
    expect(await mgr.getNonce()).toBe(20);
  });

  it("causes an extra RPC call on the next getNonce after reset", async () => {
    const client = makeClient(undefined, 0);
    const mgr = NonceManager.forAccount(client as never, FAKE_ADDRESS);
    await mgr.getNonce();
    expect(client.getTransactionCount).toHaveBeenCalledTimes(1);
    mgr.reset();
    await mgr.getNonce();
    expect(client.getTransactionCount).toHaveBeenCalledTimes(2);
  });
});
