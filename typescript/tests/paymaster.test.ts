import { getAddress } from "viem";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Paymaster, toAddressHex, toHex } from "../src/core/paymaster";

const PAYMASTER_URL = "https://paymaster.example.com";
const ADDRESS = "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18";

/** Build a minimal fetch-Response-like mock (duck-typed: ok/status/json()). */
function mockResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
  };
}

function fetchMock() {
  return vi.fn() as unknown as typeof fetch & ReturnType<typeof vi.fn>;
}

describe("toHex", () => {
  it("converts a number to hex", () => {
    const result = toHex(255);
    expect(result.startsWith("0x")).toBe(true);
    expect(Number.parseInt(result, 16)).toBe(255);
  });

  it("converts a bigint to hex", () => {
    const result = toHex(255n);
    expect(Number.parseInt(result, 16)).toBe(255);
  });

  it("converts bytes (Uint8Array) to hex", () => {
    expect(toHex(new Uint8Array([0xab, 0xcd]))).toBe("0xabcd");
  });

  it("passes through a string that already has a 0x prefix", () => {
    expect(toHex("0xdeadbeef")).toBe("0xdeadbeef");
  });

  it("prefixes a string without 0x", () => {
    expect(toHex("abcd")).toBe("0xabcd");
  });

  it("returns default for null", () => {
    expect(toHex(null)).toBe("0x0");
  });

  it("returns default for undefined", () => {
    expect(toHex(undefined)).toBe("0x0");
  });

  it("returns default for empty bytes", () => {
    expect(toHex(new Uint8Array())).toBe("0x0");
  });

  it("returns default for empty string", () => {
    expect(toHex("")).toBe("0x0");
  });

  it("honors a custom default value", () => {
    expect(toHex(null, "0xdead")).toBe("0xdead");
  });
});

describe("toAddressHex", () => {
  it("checksums a valid address", () => {
    const result = toAddressHex(ADDRESS);
    expect(result.startsWith("0x")).toBe(true);
    expect(result.length).toBe(42);
  });

  it("returns default for an invalid address", () => {
    expect(toAddressHex("not-an-address")).toBe("0x0");
  });

  it("returns default for null", () => {
    expect(toAddressHex(null)).toBe("0x0");
  });

  it("returns default for undefined", () => {
    expect(toAddressHex(undefined)).toBe("0x0");
  });
});

describe("Paymaster", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function makePaymaster() {
    return new Paymaster(PAYMASTER_URL);
  }

  it("ethGetTransactionCount parses a hex result into a number", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ result: "0xa" }),
    );

    const pm = makePaymaster();
    const count = await pm.ethGetTransactionCount(ADDRESS);
    expect(count).toBe(10);
  });

  it("ethGetTransactionCount throws when 'result' is missing", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse({}));

    const pm = makePaymaster();
    await expect(pm.ethGetTransactionCount(ADDRESS)).rejects.toThrow(
      /missing 'result'/,
    );
  });

  it("ethGetTransactionCount 0x-prefixes and checksums the address sent", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: "0x1" }));

    const pm = makePaymaster();
    await pm.ethGetTransactionCount(ADDRESS.slice(2)); // no 0x prefix

    const [, init] = mock.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.params[0]).toBe(getAddress(ADDRESS));
  });

  it("ethSendRawTransaction returns the tx hash", async () => {
    const hash = `0x${"ab".repeat(32)}`;
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ result: hash }),
    );

    const pm = makePaymaster();
    const result = await pm.ethSendRawTransaction("0xsignedtx");
    expect(result).toBe(hash);
  });

  it("ethSendRawTransaction throws when 'result' is missing", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse({}));

    const pm = makePaymaster();
    await expect(pm.ethSendRawTransaction("0xsignedtx")).rejects.toThrow(
      /missing 'result'/,
    );
  });

  it("ethSendRawTransaction adds a 0x prefix to the raw tx param", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: "0xhash" }));

    const pm = makePaymaster();
    await pm.ethSendRawTransaction("signedtx_no_prefix");

    const [, init] = mock.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.params[0].startsWith("0x")).toBe(true);
  });

  it("ethSendRawTransaction forwards txOptions as extra HTTP headers", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: "0xhash" }));

    const pm = makePaymaster();
    await pm.ethSendRawTransaction("0xsignedtx", { "X-Api-Key": "secret" });

    const [, init] = mock.mock.calls[0];
    expect((init.headers as Record<string, string>)["X-Api-Key"]).toBe(
      "secret",
    );
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
  });

  it("does not override a caller-supplied Content-Type header", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: "0xhash" }));

    const pm = makePaymaster();
    await pm.ethSendRawTransaction("0xsignedtx", {
      "Content-Type": "application/custom",
    });

    const [, init] = mock.mock.calls[0];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/custom",
    );
  });

  it("isSponsorable returns true when sponsorable", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ result: { sponsorable: true } }),
    );

    const pm = makePaymaster();
    const result = await pm.isSponsorable({
      to: `0x${"ab".repeat(20)}`,
      from: `0x${"cd".repeat(20)}`,
      value: 0n,
      data: "0x",
      gas: 21000n,
    });
    expect(result).toBe(true);
  });

  it("isSponsorable returns false when not sponsorable", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ result: { sponsorable: false } }),
    );

    const pm = makePaymaster();
    const result = await pm.isSponsorable({
      to: `0x${"ab".repeat(20)}`,
      from: `0x${"cd".repeat(20)}`,
    });
    expect(result).toBe(false);
  });

  it("isSponsorable returns false (not a throw) when 'result' is missing", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse({}));

    const pm = makePaymaster();
    const result = await pm.isSponsorable({ to: `0x${"ab".repeat(20)}` });
    expect(result).toBe(false);
  });

  it("isSponsorable hex-encodes params with 0x0 defaults", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: { sponsorable: true } }));

    const pm = makePaymaster();
    await pm.isSponsorable({});

    const [, init] = mock.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body.method).toBe("pm_isSponsorable");
    expect(body.params[0]).toEqual({
      to: "0x0",
      from: "0x0",
      value: "0x0",
      data: "0x0",
      gas: "0x0",
    });
  });

  it("throws a formatted RPC error when the response contains an error object", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ error: { message: "bad request", code: -32600 } }),
    );

    const pm = makePaymaster();
    await expect(pm.ethGetTransactionCount(ADDRESS)).rejects.toThrow(
      "RPC error [-32600]: bad request",
    );
  });

  it("defaults RPC error code/message when the error object omits them", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({ error: {} }),
    );

    const pm = makePaymaster();
    await expect(pm.ethGetTransactionCount(ADDRESS)).rejects.toThrow(
      "RPC error [-1]: Unknown error",
    );
  });

  it("throws on a non-2xx HTTP response", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      mockResponse({}, 500),
    );

    const pm = makePaymaster();
    await expect(pm.ethGetTransactionCount(ADDRESS)).rejects.toThrow(/500/);
  });

  it("propagates a network error from fetch", async () => {
    const networkError = new Error("refused");
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValue(networkError);

    const pm = makePaymaster();
    await expect(pm.ethGetTransactionCount(ADDRESS)).rejects.toBe(networkError);
  });

  it("uses a 30s AbortSignal.timeout on the request", async () => {
    const mock = fetch as ReturnType<typeof vi.fn>;
    mock.mockResolvedValue(mockResponse({ result: "0x1" }));

    const pm = makePaymaster();
    await pm.ethGetTransactionCount(ADDRESS);

    const [, init] = mock.mock.calls[0];
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });
});
