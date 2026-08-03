import { afterEach, describe, expect, it, vi } from "vitest";
import {
  RELAY_VERIFIER_ENDPOINT_TIMEOUT_MS,
  type TxProbeResult,
  confirmTxUnseen,
  resolveFallbackRpcUrls,
} from "../src/core/relayVerifier.js";
import {
  BSC_MAINNET_CHAIN_ID,
  BSC_TESTNET_CHAIN_ID,
} from "../src/networks/addresses.js";

const HASH = `0x${"ab".repeat(32)}` as `0x${string}`;

afterEach(() => {
  vi.unstubAllEnvs();
});

/** Build a probe that answers per-URL from a lookup table. */
function tableProbe(
  table: Record<string, TxProbeResult>,
): (url: string) => Promise<TxProbeResult> {
  return async (url: string) => table[url] ?? "error";
}

describe("resolveFallbackRpcUrls", () => {
  it("explicit opts beat env and the built-in table", () => {
    vi.stubEnv("BNBAGENT_FALLBACK_RPC_URLS", "https://env.example");
    expect(
      resolveFallbackRpcUrls({
        chainId: BSC_TESTNET_CHAIN_ID,
        fallbackRpcUrls: ["https://a.example", "https://b.example"],
      }),
    ).toEqual(["https://a.example", "https://b.example"]);
  });

  it("env (comma-separated) beats the built-in table", () => {
    vi.stubEnv(
      "BNBAGENT_FALLBACK_RPC_URLS",
      " https://x.example , https://y.example ,",
    );
    expect(resolveFallbackRpcUrls({ chainId: BSC_TESTNET_CHAIN_ID })).toEqual([
      "https://x.example",
      "https://y.example",
    ]);
  });

  it("falls back to the built-in per-chain table", () => {
    const testnet = resolveFallbackRpcUrls({ chainId: BSC_TESTNET_CHAIN_ID });
    const mainnet = resolveFallbackRpcUrls({ chainId: BSC_MAINNET_CHAIN_ID });
    expect(testnet.length).toBeGreaterThan(0);
    expect(mainnet.length).toBeGreaterThan(0);
    expect(testnet.every((u) => u.startsWith("https://"))).toBe(true);
  });

  it("unknown chain yields no endpoints", () => {
    expect(resolveFallbackRpcUrls({ chainId: 1337 })).toEqual([]);
  });

  it("dedupes the primary RPC (case/trailing-slash-insensitive) and repeats", () => {
    expect(
      resolveFallbackRpcUrls({
        chainId: BSC_TESTNET_CHAIN_ID,
        primaryRpcUrl: "https://A.example/",
        fallbackRpcUrls: [
          "https://a.example",
          "https://b.example",
          "https://b.example/",
        ],
      }),
    ).toEqual(["https://b.example"]);
  });
});

describe("confirmTxUnseen", () => {
  it("all unseen -> confirmed-unseen", async () => {
    const verdict = await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1", "u2"],
      probe: tableProbe({ u1: "unseen", u2: "unseen" }),
    });
    expect(verdict).toBe("confirmed-unseen");
  });

  it("a single seen endpoint wins -> seen", async () => {
    const verdict = await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1", "u2", "u3"],
      probe: tableProbe({ u1: "unseen", u2: "seen", u3: "error" }),
    });
    expect(verdict).toBe("seen");
  });

  it("mixed error + unseen -> confirmed-unseen (one authoritative answer suffices)", async () => {
    const verdict = await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1", "u2"],
      probe: tableProbe({ u1: "error", u2: "unseen" }),
    });
    expect(verdict).toBe("confirmed-unseen");
  });

  it("all error -> inconclusive", async () => {
    const verdict = await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1", "u2"],
      probe: tableProbe({}),
    });
    expect(verdict).toBe("inconclusive");
  });

  it("a throwing probe counts as error, never rejects", async () => {
    const verdict = await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1"],
      probe: async () => {
        throw new Error("boom");
      },
    });
    expect(verdict).toBe("inconclusive");
  });

  it("no usable endpoints -> inconclusive without probing", async () => {
    const probe = vi.fn(async (): Promise<TxProbeResult> => "unseen");
    const verdict = await confirmTxUnseen(HASH, {
      chainId: 1337,
      probe,
    });
    expect(verdict).toBe("inconclusive");
    expect(probe).not.toHaveBeenCalled();
  });

  it("passes the per-endpoint timeout to the probe", async () => {
    const probe = vi.fn(
      async (
        _url: string,
        _hash: `0x${string}`,
        timeoutMs: number,
      ): Promise<TxProbeResult> => {
        expect(timeoutMs).toBe(RELAY_VERIFIER_ENDPOINT_TIMEOUT_MS);
        return "unseen";
      },
    );
    await confirmTxUnseen(HASH, {
      chainId: BSC_TESTNET_CHAIN_ID,
      fallbackRpcUrls: ["u1"],
      probe,
    });
    expect(probe).toHaveBeenCalledOnce();
  });
});
