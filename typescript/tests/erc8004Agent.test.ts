/**
 * Ports the ERC8004Agent slices of `python/tests/test_sdk.py`.
 *
 * The Python suite patches `ContractInterface` and `Web3` at the module
 * level (`unittest.mock.patch`) so it can drive the SDK class in isolation
 * from any real chain/RPC. This suite does the ESM equivalent: `vi.mock`
 * replaces `../src/erc8004/contract.js`'s `ContractInterface` with a shared
 * fake, and `viem`'s `createPublicClient` is partially mocked so
 * `ERC8004Agent.create()`'s chain-id round trip resolves without a real
 * RPC. The SSRF-guarded `parseAgentUri` HTTP path is exercised separately by
 * mocking `node:http`'s `request`, since it is a pure static method that
 * doesn't require an `ERC8004Agent` instance at all.
 */

import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentEndpoint } from "../src/erc8004/models.js";
import {
  ERC8004PartialRegistrationError,
  TransactionPendingError,
} from "../src/errors.js";
import { WalletProvider } from "../src/wallets/walletProvider.js";

const WALLET_ADDRESS = `0x${"3".repeat(40)}`;
const CONTRACT_ADDRESS = "0x8004A818BFB912233c491871b3d84c89A494BD9e";
const AGENT_URI_B64 =
  "data:application/json;base64,eyJuYW1lIjoiTXkgVGVzdCBBZ2VudCJ9";

const {
  mockContract,
  ContractInterfaceMock,
  fakeClient,
  createPublicClientMock,
  httpRequestMock,
  httpsRequestMock,
  dnsLookupMock,
} = vi.hoisted(() => {
  const mockContract = {
    address: "0x8004A818BFB912233c491871b3d84c89A494BD9e",
    registerAgent: vi.fn(),
    getAgentInfo: vi.fn(),
    getMetadata: vi.fn(),
    setMetadata: vi.fn(),
    setAgentUri: vi.fn(),
  };
  const ContractInterfaceMock = vi.fn().mockImplementation(() => mockContract);
  const fakeClient = { getChainId: vi.fn().mockResolvedValue(97) };
  const createPublicClientMock = vi.fn().mockReturnValue(fakeClient);
  const httpRequestMock = vi.fn();
  const httpsRequestMock = vi.fn();
  // Real `dns.lookup` by default (assigned once the real module is
  // available inside the `vi.mock("node:dns/promises", ...)` factory
  // below); individual tests override with `mockResolvedValueOnce`/
  // `mockImplementationOnce` to control the DNS answer(s) seen by the
  // SSRF guard, then fall back to the real resolver afterwards.
  const dnsLookupMock = vi.fn();
  return {
    mockContract,
    ContractInterfaceMock,
    fakeClient,
    createPublicClientMock,
    httpRequestMock,
    httpsRequestMock,
    dnsLookupMock,
  };
});

vi.mock("../src/erc8004/contract.js", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../src/erc8004/contract.js")>();
  return { ...actual, ContractInterface: ContractInterfaceMock };
});

vi.mock("viem", async (importOriginal) => {
  const actual = await importOriginal<typeof import("viem")>();
  return { ...actual, createPublicClient: createPublicClientMock };
});

// `agent.ts` imports `* as http from "node:http"` / `* as https from
// "node:https"` and calls `.request` for the SSRF-guarded fetch — full
// module replacement (rather than `vi.spyOn`) because Node's built-in ESM
// namespace objects are frozen.
vi.mock("node:http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:http")>();
  return { ...actual, request: httpRequestMock };
});

vi.mock("node:https", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:https")>();
  return { ...actual, request: httpsRequestMock };
});

// Likewise for `dns.lookup`: default to the real resolver (IP literals
// like "203.0.113.5" resolve instantly with no network I/O) so tests that
// don't care about DNS keep working unmodified; SSRF-guard tests below
// override the answer per-call.
vi.mock("node:dns/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:dns/promises")>();
  dnsLookupMock.mockImplementation(actual.lookup);
  return { ...actual, lookup: dnsLookupMock };
});

// Import after the mocks are registered.
const { ERC8004Agent } = await import("../src/erc8004/agent.js");

class StubWallet extends WalletProvider {
  static override readonly kind = "stub";
  get address(): `0x${string}` {
    return WALLET_ADDRESS as `0x${string}`;
  }
}

function receiptOk() {
  return { status: 1, logs: [] as unknown[] };
}

function resetMockContractDefaults() {
  mockContract.registerAgent.mockReset().mockResolvedValue({
    success: true,
    transactionHash: `0x${"0".repeat(64)}`,
    agentId: 1,
    receipt: receiptOk(),
  });
  mockContract.getAgentInfo.mockReset().mockResolvedValue({
    agentId: 1,
    agentAddress: `0x${"1".repeat(40)}`,
    agentWallet: `0x${"1".repeat(40)}`,
    owner: `0x${"2".repeat(40)}`,
    agentURI: AGENT_URI_B64,
  });
  mockContract.getMetadata.mockReset().mockResolvedValue("test value");
  mockContract.setMetadata.mockReset().mockResolvedValue({
    success: true,
    transactionHash: `0x${"0".repeat(64)}`,
    receipt: receiptOk(),
  });
  mockContract.setAgentUri.mockReset().mockResolvedValue({
    success: true,
    transactionHash: `0x${"0".repeat(64)}`,
    receipt: receiptOk(),
  });
}

beforeEach(() => {
  resetMockContractDefaults();
  fakeClient.getChainId.mockReset().mockResolvedValue(97);
  ContractInterfaceMock.mockClear().mockImplementation(() => mockContract);
  createPublicClientMock.mockClear().mockReturnValue(fakeClient);
  httpRequestMock.mockClear();
  httpsRequestMock.mockClear();
  dnsLookupMock.mockClear();
});

afterEach(() => {
  // NOTE: deliberately `clearAllMocks` (calls/instances only), never
  // `restoreAllMocks`/`resetAllMocks` — those strip the `mockReturnValue`/
  // `mockImplementation` set on the plain `vi.fn()` mocks above (they are
  // not `vi.spyOn` spies with a real implementation to "restore" to), which
  // would silently turn every subsequent `createPublicClient()` /
  // `new ContractInterface()` call into `undefined` for the rest of the file.
  vi.clearAllMocks();
});

async function createTestAgent() {
  return ERC8004Agent.create({
    walletProvider: new StubWallet(),
    network: "bsc-testnet",
    debug: true,
  });
}

const A2A_ENDPOINT = [
  new AgentEndpoint({
    name: "A2A",
    endpoint: "https://agent.example/.well-known/agent-card.json",
  }),
];

describe("ERC8004Agent.create", () => {
  it("initializes with wallet_address and contract_address available", async () => {
    const agent = await createTestAgent();
    expect(agent.walletAddress).toBe(WALLET_ADDRESS);
    expect(agent.contractAddress).toBe(mockContract.address);
  });

  it("rejects an unknown network before touching the RPC", async () => {
    await expect(
      ERC8004Agent.create({
        walletProvider: new StubWallet(),
        network: "invalid-network",
      }),
    ).rejects.toThrow(/Unknown network/);
  });

  it("requires a wallet provider", async () => {
    await expect(
      ERC8004Agent.create({
        // biome-ignore lint/suspicious/noExplicitAny: exercising the runtime guard
        walletProvider: null as any,
        network: "bsc-testnet",
      }),
    ).rejects.toThrow(/wallet_provider is required/);
  });

  it("wraps an unreachable RPC in an Error", async () => {
    fakeClient.getChainId.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    await expect(createTestAgent()).rejects.toThrow(/Failed to connect to RPC/);
  });

  it("hard-fails on a chain_id mismatch (defense-in-depth)", async () => {
    fakeClient.getChainId.mockResolvedValueOnce(1); // ethereum mainnet, not 97
    await expect(createTestAgent()).rejects.toThrow(/chain_id mismatch/);
  });
});

describe("generateAgentUri", () => {
  it("returns a base64 data URI", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "My Test Agent",
      description: "A test agent for demonstration",
      image: "https://example.com/image.png",
      endpoints: A2A_ENDPOINT,
    });
    expect(uri.startsWith("data:application/json;base64,")).toBe(true);
  });

  it("requires at least one endpoint", async () => {
    const agent = await createTestAgent();
    expect(() =>
      agent.generateAgentUri({
        name: "Test",
        description: "Test",
        endpoints: [],
      }),
    ).toThrow(/endpoints is required/);
  });

  it("carries supportedTrust through to the parsed file", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "Trust Agent",
      description: "d",
      endpoints: A2A_ENDPOINT,
      supportedTrust: ["reputation", "crypto-economic"],
    });
    const data = await ERC8004Agent.parseAgentUri(uri);
    expect(data?.supportedTrust).toContain("reputation");
  });

  it("carries image through to the parsed file", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "Image Agent",
      description: "d",
      endpoints: A2A_ENDPOINT,
      image: "https://example.com/agent-image.png",
    });
    const data = await ERC8004Agent.parseAgentUri(uri);
    expect(data?.image).toBe("https://example.com/agent-image.png");
  });
});

describe("registerAgent", () => {
  it("registers with a generated agent URI and returns the assigned agentId", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "My Test Agent",
      description: "A test agent",
      endpoints: A2A_ENDPOINT,
    });
    const result = await agent.registerAgent(uri);
    expect(result.agentId).toBe(1);
    expect(result.transactionHash).toBeDefined();
    expect(result.agentURI).toBeDefined();
  });

  it("forwards metadata to the contract layer", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "My Test Agent",
      description: "A test agent",
      endpoints: A2A_ENDPOINT,
    });
    const metadata = [{ key: "name", value: "My Test Agent" }];
    await agent.registerAgent(uri, metadata);
    expect(mockContract.registerAgent).toHaveBeenCalledWith(uri, metadata);
  });

  it("requires a non-empty agent_uri", async () => {
    const agent = await createTestAgent();
    await expect(agent.registerAgent("")).rejects.toThrow(
      /agent_uri is required/,
    );
  });

  it("raises a partial-registration error (no tx hash) when setAgentUri fails outright", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "Partial Agent",
      description: "d",
      endpoints: A2A_ENDPOINT,
    });
    mockContract.setAgentUri.mockRejectedValueOnce(
      new Error("setAgentURI reverted"),
    );

    let caught: unknown;
    try {
      await agent.registerAgent(uri);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ERC8004PartialRegistrationError);
    const err = caught as ERC8004PartialRegistrationError;
    expect(err.agentId).toBe(1);
    expect(err.txHash).toBeNull();
    expect(err.retryable).toBe(true);
  });

  it("carries the pending tx hash when setAgentUri broadcasts but doesn't confirm", async () => {
    const agent = await createTestAgent();
    const uri = agent.generateAgentUri({
      name: "Partial Agent",
      description: "d",
      endpoints: A2A_ENDPOINT,
    });
    const pendingHash = `0x${"ab".repeat(32)}`;
    mockContract.setAgentUri.mockRejectedValueOnce(
      new TransactionPendingError(pendingHash, 300),
    );

    let caught: unknown;
    try {
      await agent.registerAgent(uri);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ERC8004PartialRegistrationError);
    const err = caught as ERC8004PartialRegistrationError;
    expect(err.agentId).toBe(1);
    expect(err.txHash).toBe(pendingHash);
  });
});

describe("getAgentInfo / getMetadata / setMetadata / setAgentUri passthrough", () => {
  it("getAgentInfo returns the contract-layer result", async () => {
    const agent = await createTestAgent();
    const info = await agent.getAgentInfo(1);
    expect(info.agentId).toBe(1);
    expect(info.owner).toBeDefined();
  });

  it("getMetadata returns the contract-layer value", async () => {
    const agent = await createTestAgent();
    await expect(agent.getMetadata(1, "name")).resolves.toBe("test value");
  });

  it("setMetadata returns the contract-layer result", async () => {
    const agent = await createTestAgent();
    const result = await agent.setMetadata(1, "updated_info", "new value");
    expect(result.success).toBe(true);
  });

  it("setAgentUri returns the contract-layer result plus the agentURI used", async () => {
    const agent = await createTestAgent();
    const result = await agent.setAgentUri(1, AGENT_URI_B64);
    expect(result.success).toBe(true);
    expect(result.agentURI).toBe(AGENT_URI_B64);
  });

  it("setAgentUri requires a non-empty agent_uri", async () => {
    const agent = await createTestAgent();
    await expect(agent.setAgentUri(1, "")).rejects.toThrow(
      /agent_uri is required/,
    );
  });
});

describe("getAllAgents", () => {
  it("queries the 8004scan API with chain_id/limit/offset and returns the parsed body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [{ token_id: 1, name: "Agent 1" }],
          total: 1,
          limit: 10,
          offset: 0,
        }),
        { status: 200 },
      ),
    );
    const agent = await createTestAgent();
    const result = await agent.getAllAgents(10, 0);

    expect(result.items).toHaveLength(1);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const requestedUrl = new URL(String(fetchSpy.mock.calls[0]?.[0]));
    expect(requestedUrl.searchParams.get("chain_id")).toBe("97");
    expect(requestedUrl.searchParams.get("limit")).toBe("10");
    expect(requestedUrl.searchParams.get("offset")).toBe("0");
  });

  it("caps limit at 100", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ items: [] }), { status: 200 }),
      );
    const agent = await createTestAgent();
    await agent.getAllAgents(500, 0);
    const requestedUrl = new URL(String(fetchSpy.mock.calls[0]?.[0]));
    expect(requestedUrl.searchParams.get("limit")).toBe("100");
  });

  it("wraps a network failure as '8004scan API request failed'", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    const agent = await createTestAgent();
    await expect(agent.getAllAgents()).rejects.toThrow(
      /8004scan API request failed/,
    );
  });

  it("wraps a non-2xx response as '8004scan API request failed'", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("server error", { status: 500 }),
    );
    const agent = await createTestAgent();
    await expect(agent.getAllAgents()).rejects.toThrow(
      /8004scan API request failed/,
    );
  });
});

describe("getLocalAgentInfo", () => {
  it("returns null when no agent matches", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200 }),
    );
    const agent = await createTestAgent();
    await expect(agent.getLocalAgentInfo("NonExistent")).resolves.toBeNull();
  });

  it("returns the matching agent owned by this wallet", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              owner_address: WALLET_ADDRESS,
              name: "Test Agent",
              token_id: 1,
              agent_uri: "data:application/json;base64,xxx",
            },
          ],
        }),
        { status: 200 },
      ),
    );
    const agent = await createTestAgent();
    const result = await agent.getLocalAgentInfo("Test Agent");
    expect(result?.name).toBe("Test Agent");
    expect(result?.agentId).toBe(1);
  });

  it("returns null for an empty name without calling the API", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const agent = await createTestAgent();
    await expect(agent.getLocalAgentInfo("")).resolves.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("network / accessors", () => {
  it("network getter exposes name/chainId", async () => {
    const agent = await createTestAgent();
    expect(agent.network.name).toBe("bsc-testnet");
    expect(agent.network.chainId).toBe(97);
  });
});

describe("ERC8004Agent.parseAgentUri", () => {
  it("returns null for an unsupported format", async () => {
    await expect(ERC8004Agent.parseAgentUri("invalid-uri")).resolves.toBeNull();
    await expect(ERC8004Agent.parseAgentUri("")).resolves.toBeNull();
  });

  it("decodes a base64 data URI", async () => {
    const data = await ERC8004Agent.parseAgentUri(AGENT_URI_B64);
    expect(data?.name).toBe("My Test Agent");
  });
});

describe("ERC8004Agent.parseAgentUri: SSRF guard (http/https path)", () => {
  it("blocks known cloud-metadata hostnames outright", async () => {
    await expect(
      ERC8004Agent.parseAgentUri("http://169.254.169.254/latest/meta-data/"),
    ).resolves.toBeNull();
    await expect(
      ERC8004Agent.parseAgentUri(
        "http://metadata.google.internal/computeMetadata/v1/",
      ),
    ).resolves.toBeNull();
  });

  it("blocks RFC 6598 CGNAT (Alibaba Cloud ECS metadata at 100.100.100.200)", async () => {
    await expect(
      ERC8004Agent.parseAgentUri(
        "http://100.100.100.200/latest/meta-data/ram/security-credentials/role",
      ),
    ).resolves.toBeNull();
  });

  it("blocks loopback and RFC1918 private ranges", async () => {
    await expect(
      ERC8004Agent.parseAgentUri("http://127.0.0.1/agent.json"),
    ).resolves.toBeNull();
    await expect(
      ERC8004Agent.parseAgentUri("http://10.0.0.5/agent.json"),
    ).resolves.toBeNull();
    await expect(
      ERC8004Agent.parseAgentUri("http://192.168.1.5/agent.json"),
    ).resolves.toBeNull();
  });

  /**
   * Wires `mock` (either `httpRequestMock` or `httpsRequestMock`) to emit a
   * canned response, and returns the request `options` object the SUT
   * passed to `.request(...)` so callers can assert on it (host/hostname,
   * servername, headers, etc).
   */
  function mockTransportResponse(
    mock: typeof httpRequestMock,
    opts: {
      status?: number;
      headers?: Record<string, string>;
      chunks: string[];
    },
  ): { capturedOptions: Record<string, unknown> | undefined } {
    const captured: { capturedOptions: Record<string, unknown> | undefined } = {
      capturedOptions: undefined,
    };
    mock.mockImplementation(
      (options: unknown, callback?: (res: unknown) => void) => {
        captured.capturedOptions = options as Record<string, unknown>;
        const req = new EventEmitter() as EventEmitter & {
          destroy: () => void;
          end: () => void;
        };
        req.destroy = vi.fn();
        req.end = () => {
          queueMicrotask(() => {
            const res = new EventEmitter() as EventEmitter & {
              statusCode: number;
              headers: Record<string, string>;
              destroy: () => void;
            };
            res.statusCode = opts.status ?? 200;
            res.headers = opts.headers ?? {};
            res.destroy = vi.fn();
            callback?.(res);
            queueMicrotask(() => {
              for (const chunk of opts.chunks) {
                res.emit("data", Buffer.from(chunk));
              }
              res.emit("end");
            });
          });
        };
        return req;
      },
    );
    return captured;
  }

  describe("with a mocked node:http transport", () => {
    beforeEach(() => {
      httpRequestMock.mockReset();
    });

    function mockHttpResponse(opts: {
      status?: number;
      headers?: Record<string, string>;
      chunks: string[];
    }) {
      return mockTransportResponse(httpRequestMock, opts);
    }

    it("parses a valid http response from a public (non-blocked) IP", async () => {
      mockHttpResponse({
        chunks: [
          JSON.stringify({
            name: "Test Agent",
            description: "Test Description",
          }),
        ],
      });
      const data = await ERC8004Agent.parseAgentUri(
        "http://203.0.113.5/agent.json",
      );
      expect(data?.name).toBe("Test Agent");
      expect(data?.description).toBe("Test Description");
    });

    it("rejects a response advertising Content-Length over the 1 MB cap", async () => {
      mockHttpResponse({
        headers: { "content-length": String(2 * 1024 * 1024) },
        chunks: ["x".repeat(10)],
      });
      const data = await ERC8004Agent.parseAgentUri(
        "http://203.0.113.5/agent.json",
      );
      expect(data).toBeNull();
    });

    it("rejects a streamed body over the 1 MB cap even with no Content-Length header", async () => {
      const oneMb = 1024 * 1024;
      mockHttpResponse({
        chunks: ["x".repeat(oneMb + 1)],
      });
      const data = await ERC8004Agent.parseAgentUri(
        "http://203.0.113.5/agent.json",
      );
      expect(data).toBeNull();
    });

    it("returns null on a non-2xx status", async () => {
      mockHttpResponse({ status: 404, chunks: ["not found"] });
      const data = await ERC8004Agent.parseAgentUri(
        "http://203.0.113.5/agent.json",
      );
      expect(data).toBeNull();
    });

    it("connects to the DNS-resolved IP with the original Host header, and issues no TLS servername", async () => {
      const captured = mockHttpResponse({
        chunks: [JSON.stringify({ name: "Test Agent" })],
      });
      const data = await ERC8004Agent.parseAgentUri(
        "http://203.0.113.5/agent.json",
      );
      expect(data?.name).toBe("Test Agent");
      const options = captured.capturedOptions;
      expect(options).toBeDefined();
      // For this URI the hostname literally *is* the resolved IP, so this
      // mainly pins down the shape of the options object (host + Host
      // header present, no servername) that the HTTPS assertions below
      // then contrast against.
      expect(options?.host).toBe("203.0.113.5");
      expect((options?.headers as Record<string, string>)?.Host).toBe(
        "203.0.113.5",
      );
      expect(options?.servername).toBeUndefined();
      expect(httpsRequestMock).not.toHaveBeenCalled();
    });
  });

  describe("with a mocked node:https transport", () => {
    beforeEach(() => {
      httpsRequestMock.mockReset();
    });

    function mockHttpsResponse(opts: {
      status?: number;
      headers?: Record<string, string>;
      chunks: string[];
    }) {
      return mockTransportResponse(httpsRequestMock, opts);
    }

    it("connects to the DNS-resolved IP (not the hostname) while sending the original hostname as SNI servername and Host header", async () => {
      // "agent.example" is a real, non-IP hostname, so `dns.lookup` is
      // actually consulted (unlike the IP-literal URIs used elsewhere in
      // this file, where lookup is a no-op identity resolution). Stub it to
      // return a known-public address so we can assert exactly what socket
      // target the SSRF guard connects to.
      dnsLookupMock.mockResolvedValueOnce([
        { address: "203.0.113.9", family: 4 },
      ]);
      const captured = mockHttpsResponse({
        chunks: [JSON.stringify({ name: "HTTPS Agent" })],
      });

      const data = await ERC8004Agent.parseAgentUri(
        "https://agent.example/agent.json",
      );

      expect(data?.name).toBe("HTTPS Agent");
      expect(dnsLookupMock).toHaveBeenCalledWith(
        "agent.example",
        expect.objectContaining({ all: true }),
      );
      const options = captured.capturedOptions;
      expect(options).toBeDefined();
      // The socket must dial the resolved IP, never the original hostname
      // (that's what closes the DNS-rebind window between check and use).
      expect(options?.host).toBe("203.0.113.9");
      expect(options?.host).not.toBe("agent.example");
      // ... but TLS server-name/cert-hostname validation and the HTTP
      // Host header must still carry the *original* hostname, or the
      // remote server/cert can't route/validate the request.
      expect(options?.servername).toBe("agent.example");
      expect((options?.headers as Record<string, string>)?.Host).toBe(
        "agent.example",
      );
      expect(httpRequestMock).not.toHaveBeenCalled();
    });
  });

  describe("DNS-rebind guard", () => {
    beforeEach(() => {
      httpRequestMock.mockReset();
    });

    it("proceeds to the exact resolved IP when dns.lookup answers with a public address", async () => {
      dnsLookupMock.mockResolvedValueOnce([
        { address: "203.0.113.20", family: 4 },
      ]);
      const captured = mockTransportResponse(httpRequestMock, {
        chunks: [JSON.stringify({ name: "Public Agent" })],
      });

      const data = await ERC8004Agent.parseAgentUri(
        "http://safe-host.example/agent.json",
      );

      expect(data?.name).toBe("Public Agent");
      expect(captured.capturedOptions?.host).toBe("203.0.113.20");
    });

    it("rejects and makes no request when dns.lookup answers with a private address (rebind attempt)", async () => {
      dnsLookupMock.mockResolvedValueOnce([{ address: "10.0.0.5", family: 4 }]);

      const data = await ERC8004Agent.parseAgentUri(
        "http://evil-host.example/agent.json",
      );

      expect(data).toBeNull();
      expect(httpRequestMock).not.toHaveBeenCalled();
      expect(httpsRequestMock).not.toHaveBeenCalled();
    });

    it("rejects and makes no request when ANY of multiple dns.lookup answers is private (multi-answer defense-in-depth)", async () => {
      dnsLookupMock.mockResolvedValueOnce([
        { address: "203.0.113.30", family: 4 },
        { address: "10.0.0.5", family: 4 },
      ]);

      const data = await ERC8004Agent.parseAgentUri(
        "http://multi-answer-host.example/agent.json",
      );

      expect(data).toBeNull();
      expect(httpRequestMock).not.toHaveBeenCalled();
      expect(httpsRequestMock).not.toHaveBeenCalled();
    });
  });
});
