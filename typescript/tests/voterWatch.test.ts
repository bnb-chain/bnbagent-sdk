/**
 * The TypeScript reference voter (`examples/voter/watch.ts`): one failing
 * disputed job never stops the loop (audit M07), and a deliverable that
 * cannot be reviewed (malformed, missing, unsupported or failing to download
 * for a few ticks) is still put to a vote (audit M07 / M09), while RPC errors
 * are retried until the decision margin before the window closes.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MANIFEST_DOWNLOAD_ATTEMPTS,
  MANIFEST_MAX_BYTES,
  ManifestUnavailableError,
  decisionMargin,
  fetchManifest,
  handleDisputedJob,
  pollDisputedJobs,
  printable,
  requireLogRpc,
} from "../examples/voter/watch.js";
import { DeliverableManifest } from "../src/erc8183/index.js";
import {
  type ERC8183Client,
  NetworkError,
  RpcRangeLimitError,
} from "../src/index.js";
import * as publicHttp from "../src/utils/publicHttp.js";

vi.mock("../src/utils/publicHttp.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/utils/publicHttp.js")>()),
  fetchPublicJson: vi.fn(),
}));
const fetchPublic = vi.mocked(publicHttp.fetchPublicJson);
const { fetchPublicJson: realFetchPublicJson } = await vi.importActual<
  typeof import("../src/utils/publicHttp.js")
>("../src/utils/publicHttp.js");

const CID = `Qm${"a".repeat(44)}`;

const MANIFEST = {
  version: 1,
  job_id: 7,
  chain_id: 97,
  contracts: {},
  response: { content: "the work" },
  metadata: {},
};
// JSON.parse turns 1e999 into Infinity, which canonicalJson refuses to hash.
const NON_FINITE_MANIFEST =
  '{"version":1,"job_id":7,"chain_id":97,"contracts":{},"response":{"content":"x"},"metadata":{"n":1e999}}';

/** Serve the voter's guarded download: a JSON object, or `null` (refused or failed). */
function servePublic(impl: () => Promise<Record<string, unknown> | null>) {
  fetchPublic.mockImplementation(impl);
}

afterEach(() => {
  fetchPublic.mockReset();
  vi.unstubAllEnvs();
});

function fakeClient(
  opts: {
    deliverableUrl?: unknown;
    submittedAgo?: bigint;
    window?: bigint;
  } = {},
) {
  const now = BigInt(Math.floor(Date.now() / 1000));
  return {
    policy: {
      address: `0x${"cc".repeat(20)}`,
      hasVoted: vi.fn(async () => false),
      disputeWindow: vi.fn(async () => opts.window ?? 3600n),
      disputed: vi.fn(async () => true),
    },
    getJob: vi.fn(async () => ({
      client: `0x${"01".repeat(20)}`,
      provider: `0x${"02".repeat(20)}`,
      budget: 100n,
      status: 3,
      submittedAt: now - (opts.submittedAgo ?? 60n),
      deliverable: DeliverableManifest.fromDict(MANIFEST).manifestHash(),
    })),
    getDeliverableUrl: vi.fn(
      async (): Promise<string | null> =>
        (opts.deliverableUrl === undefined
          ? `ipfs://${CID}`
          : opts.deliverableUrl) as string | null,
    ),
    voteReject: vi.fn(async () => ({})),
    publicClient: {
      getBlockNumber: vi.fn(async () => 100n),
      // two seconds a block
      getBlock: vi.fn(async ({ blockNumber }: { blockNumber: bigint }) => ({
        timestamp: blockNumber * 2n,
      })),
      getLogs: vi.fn(async () => []),
    },
    network: { rpcUrl: "https://rpc.example" },
  };
}

const asClient = (client: ReturnType<typeof fakeClient>) =>
  client as unknown as ERC8183Client;

function prompt(answer = "s") {
  return { question: vi.fn(async () => answer) };
}

describe("fetchManifest", () => {
  it("returns a well-formed manifest", async () => {
    servePublic(async () => MANIFEST);
    const manifest = await fetchManifest(`ipfs://${CID}`, "https://gateway/");
    expect(manifest?.response.content).toBe("the work");
    expect(fetchPublic).toHaveBeenCalledWith(`https://gateway/${CID}`, {
      timeoutMs: 15_000,
      maxBytes: MANIFEST_MAX_BYTES,
    });
    expect(MANIFEST_MAX_BYTES).toBe(8 * 1024 * 1024);
  });

  it("reports a manifest whose hash cannot be computed as malformed instead of throwing (audit M07)", async () => {
    servePublic(async () => JSON.parse(NON_FINITE_MANIFEST));
    await expect(
      fetchManifest(`ipfs://${CID}`, "https://gateway/"),
    ).resolves.toBeNull();
  });

  it.each([
    ["a failed request", async () => null],
    ["an HTTP error", async () => null],
  ])("throws ManifestUnavailableError on %s", async (_label, impl) => {
    servePublic(impl);
    await expect(
      fetchManifest(`ipfs://${CID}`, "https://gateway/"),
    ).rejects.toBeInstanceOf(ManifestUnavailableError);
  });

  it.each(["https://[::1", "http://exa mple.com/m"])(
    "throws ManifestUnavailableError on an invalid URL: %j (audit M09)",
    async (url) => {
      fetchPublic.mockImplementation(realFetchPublicJson);
      await expect(
        fetchManifest(url, "https://gateway/"),
      ).rejects.toBeInstanceOf(ManifestUnavailableError);
    },
  );
});

describe("printable", () => {
  it("escapes what could rewrite the terminal and keeps the rest", () => {
    expect(printable("line 1\n\tline 2 — 中文 ✓")).toBe(
      "line 1\n\tline 2 — 中文 ✓",
    );
    expect(printable("\x1b[3A\r")).toBe("\\x1b[3A\\x0d");
    expect(printable("a\u202eb\ud800")).toBe("a\\u202eb\\ud800");
    expect(printable({ k: "\x1b" })).toBe('{"k":"\\u001b"}');
  });
});

describe("decisionMargin", () => {
  it("is 30 minutes, at most half the window", () => {
    expect(decisionMargin(7 * 86_400)).toBe(1800);
    expect(decisionMargin(3600)).toBe(1800);
    expect(decisionMargin(600)).toBe(300);
    vi.stubEnv("VOTER_DECISION_MARGIN_SECONDS", "120");
    expect(decisionMargin(7 * 86_400)).toBe(120);
  });

  it.each(["-1", "NaN", "Infinity", "30m"])(
    "rejects VOTER_DECISION_MARGIN_SECONDS=%j",
    (value) => {
      vi.stubEnv("VOTER_DECISION_MARGIN_SECONDS", value);
      expect(() => decisionMargin(3600)).toThrow(
        /VOTER_DECISION_MARGIN_SECONDS must be a non-negative number/,
      );
    },
  );
});

describe("handleDisputedJob", () => {
  it("verifies a well-formed manifest against job.deliverable and prompts", async () => {
    servePublic(async () => MANIFEST);
    const client = fakeClient();
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt("s");

    await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

    expect(rl.question).toHaveBeenCalledTimes(1);
    expect(log.mock.calls.flat()).toContain("  hash ok  : yes");
    expect(client.getDeliverableUrl).toHaveBeenCalledWith(7n);
    log.mockRestore();
  });

  it.each([
    ["x\u202e", "ignored"],
    [`ipfs://${CID}`, "\x1b[3A\r  hash ok  : yes\x1b[K"],
  ])(
    "prints a provider's control characters escaped: url %j, content %j",
    async (deliverableUrl, content) => {
      // Control characters in the deliverable could rewrite the hash check
      // printed above the content.
      servePublic(async () => ({ ...MANIFEST, response: { content } }));
      const client = fakeClient({ deliverableUrl });
      const log = vi.spyOn(console, "log").mockImplementation(() => {});
      const rl = prompt("r");

      await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

      expect(rl.question).toHaveBeenCalledTimes(1);
      const out = log.mock.calls.flat().join("\n");
      for (const raw of ["\x1b", "\r", "\u202e"]) {
        expect(out).not.toContain(raw);
      }
      expect(out).toMatch(/\\u202e|\\x1b\[3A\\x0d/);
      vi.restoreAllMocks();
    },
  );

  it("still prompts for the non-finite manifest so the voter can reject (audit M07)", async () => {
    servePublic(async () => JSON.parse(NON_FINITE_MANIFEST));
    const client = fakeClient();
    vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt("r");

    await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

    expect(rl.question).toHaveBeenCalledTimes(1);
    expect(client.voteReject).toHaveBeenCalledWith(7n);
    vi.restoreAllMocks();
  });

  it.each([
    null,
    "",
    123,
    { a: 1 },
    [`ipfs://${CID}`],
    "file:///etc/passwd",
    "ftp://host/m.json",
  ])(
    "prompts at once, with a warning, when no deliverable can be fetched: %j (audit M09)",
    async (deliverableUrl) => {
      servePublic(async () => {
        throw new Error("nothing should be fetched");
      });
      const client = fakeClient({ deliverableUrl });
      const log = vi.spyOn(console, "log").mockImplementation(() => {});
      const rl = prompt("r");

      await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

      expect(rl.question).toHaveBeenCalledTimes(1);
      expect(fetchPublic).not.toHaveBeenCalled();
      const out = log.mock.calls.flat().join("\n");
      expect(out).toContain("deliverable unavailable");
      if (typeof deliverableUrl !== "string" || !deliverableUrl) {
        expect(out).toContain(
          "the provider published no usable deliverable_url",
        );
      }
      expect(client.voteReject).toHaveBeenCalledWith(7n);
      vi.restoreAllMocks();
    },
  );

  it.each([
    ["a failed request", async () => null],
    ["HTTP 404", async () => null],
    ["HTTP 500", async () => null],
  ])(
    "retries a failed download (%s) briefly, then prompts (audit M09)",
    async (_label, impl) => {
      servePublic(impl);
      const client = fakeClient();
      const log = vi.spyOn(console, "log").mockImplementation(() => {});
      const rl = prompt("r");

      for (
        let failures = 0;
        failures < MANIFEST_DOWNLOAD_ATTEMPTS - 1;
        failures++
      ) {
        await expect(
          handleDisputedJob(
            asClient(client),
            7,
            "0xvoter",
            "https://g/",
            rl,
            failures,
          ),
        ).rejects.toBeInstanceOf(ManifestUnavailableError);
      }
      expect(rl.question).not.toHaveBeenCalled();

      await handleDisputedJob(
        asClient(client),
        7,
        "0xvoter",
        "https://g/",
        rl,
        MANIFEST_DOWNLOAD_ATTEMPTS - 1,
      );
      expect(rl.question).toHaveBeenCalledTimes(1);
      expect(log.mock.calls.flat().join("\n")).toContain(
        "deliverable unavailable: could not fetch manifest",
      );
      expect(client.voteReject).toHaveBeenCalledWith(7n);
      vi.restoreAllMocks();
    },
  );

  it("prompts on the first failed download when the window is closing", async () => {
    servePublic(async () => null);
    const client = fakeClient({ submittedAgo: 3600n - 12n, window: 3600n });
    vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt("s");

    await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

    expect(rl.question).toHaveBeenCalledTimes(1);
    vi.restoreAllMocks();
  });

  it("retries a failed lookup, and prompts on it once the window is within the margin", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const far = fakeClient({ submittedAgo: 60n, window: 3600n });
    far.getDeliverableUrl.mockRejectedValue(
      new NetworkError("JobInitialised scan failed"),
    );
    const rl = prompt("s");
    await expect(
      handleDisputedJob(asClient(far), 7, "0xvoter", "https://g/", rl),
    ).rejects.toBeInstanceOf(NetworkError);
    expect(rl.question).not.toHaveBeenCalled();

    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const near = fakeClient({ submittedAgo: 3600n - 1700n, window: 3600n });
    near.getDeliverableUrl.mockRejectedValue(
      new NetworkError("JobInitialised scan failed"),
    );
    await handleDisputedJob(asClient(near), 7, "0xvoter", "https://g/", rl);
    expect(rl.question).toHaveBeenCalledTimes(1);
    const out = log.mock.calls.flat().join("\n");
    expect(out).toContain(
      "deliverable not verified: could not look the deliverable up on-chain",
    );
    expect(out).toContain("closes in 28 min");
    vi.restoreAllMocks();
  });

  it("stops once the voting window has closed", async () => {
    const client = fakeClient({
      deliverableUrl: null,
      submittedAgo: 3600n,
      window: 3600n,
    });
    vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt();

    await handleDisputedJob(asClient(client), 7, "0xvoter", "https://g/", rl);

    expect(client.getDeliverableUrl).not.toHaveBeenCalled();
    expect(rl.question).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});

describe("pollDisputedJobs", () => {
  it("keeps a failed job for the next tick without blocking the others", async () => {
    const client = fakeClient();
    vi.spyOn(console, "log").mockImplementation(() => {});
    const watching = new Set([7, 8]);
    const seen = new Set<number>();
    const handle = vi
      .fn<(jobId: number) => Promise<void>>()
      .mockImplementation(async () => {});
    handle.mockRejectedValueOnce(new ManifestUnavailableError("not yet"));

    await pollDisputedJobs(asClient(client), watching, seen, handle);
    expect([...seen]).toEqual([8]);
    expect([...watching]).toEqual([7]);

    await pollDisputedJobs(asClient(client), watching, seen, handle);
    expect([...seen].sort()).toEqual([7, 8]);
    expect(watching.size).toBe(0);
    vi.restoreAllMocks();
  });

  it("survives the non-finite manifest end to end and prompts for it (audit M07)", async () => {
    servePublic(async () => JSON.parse(NON_FINITE_MANIFEST));
    const client = fakeClient();
    vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt("r");
    const seen = new Set<number>();

    await pollDisputedJobs(asClient(client), new Set([7]), seen, (jobId) =>
      handleDisputedJob(asClient(client), jobId, "0xvoter", "https://g/", rl),
    );

    expect(rl.question).toHaveBeenCalledTimes(1);
    expect(client.voteReject).toHaveBeenCalledWith(7n);
    expect([...seen]).toEqual([7]);
    vi.restoreAllMocks();
  });

  it.each([
    ["a missing URL", null],
    ["HTTP 500", `ipfs://${CID}`],
    ["a file:// URL", "file:///x"],
  ])(
    "votes within a few ticks of an unreviewable dispute: %s (audit M09)",
    async (_label, deliverableUrl) => {
      // The audit's simulation: a 3 600 s window polled every 12 s. Before the
      // fix, all three cases were retried for 300 ticks without a prompt.
      servePublic(async () => null);
      const client = fakeClient({ deliverableUrl });
      vi.spyOn(console, "log").mockImplementation(() => {});
      const rl = prompt("r");
      const watching = new Set([7]);
      const seen = new Set<number>();
      const failures = new Map<number, number>();

      let ticks = 0;
      while (watching.size > 0 && ticks < 300) {
        ticks++;
        await pollDisputedJobs(
          asClient(client),
          watching,
          seen,
          (jobId, downloadFailures) =>
            handleDisputedJob(
              asClient(client),
              jobId,
              "0xvoter",
              "https://g/",
              rl,
              downloadFailures,
            ),
          failures,
        );
      }

      expect(rl.question).toHaveBeenCalledTimes(1);
      expect(ticks).toBeLessThanOrEqual(MANIFEST_DOWNLOAD_ATTEMPTS);
      expect([...seen]).toEqual([7]);
      expect(client.voteReject).toHaveBeenCalledWith(7n);
      vi.restoreAllMocks();
    },
  );

  it("keeps retrying RPC errors without prompting or counting a download failure", async () => {
    const client = fakeClient();
    client.getDeliverableUrl.mockRejectedValue(
      new RpcRangeLimitError("-32005 limit exceeded"),
    );
    vi.spyOn(console, "log").mockImplementation(() => {});
    const rl = prompt();
    const watching = new Set([7]);
    const failures = new Map<number, number>();

    for (let tick = 0; tick < 10; tick++) {
      await pollDisputedJobs(
        asClient(client),
        watching,
        new Set(),
        (jobId, downloadFailures) =>
          handleDisputedJob(
            asClient(client),
            jobId,
            "0xvoter",
            "https://g/",
            rl,
            downloadFailures,
          ),
        failures,
      );
    }

    expect(rl.question).not.toHaveBeenCalled();
    expect([...watching]).toEqual([7]);
    expect(failures.size).toBe(0);
    vi.restoreAllMocks();
  });

  it.each(["lookup", "download"])(
    "is prompted before the window closes whatever keeps failing: %s",
    async (failure) => {
      // The audit's 3 600 s window polled every 12 s, with time running: a
      // lookup that keeps failing (an RPC that cannot serve the submission's
      // logs) is retried until the decision margin and then put to a vote.
      let nowMs = 1_000_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => nowMs);
      const client = fakeClient({ submittedAgo: 60n, window: 3600n });
      if (failure === "lookup") {
        client.getDeliverableUrl.mockRejectedValue(
          new NetworkError("scan failed"),
        );
      } else {
        servePublic(async () => null);
      }
      vi.spyOn(console, "log").mockImplementation(() => {});
      const windowEnd = nowMs / 1000 - 60 + 3600;
      const promptedAt: number[] = [];
      const rl = {
        question: vi.fn(async () => {
          promptedAt.push(windowEnd - nowMs / 1000);
          return "r";
        }),
      };
      const watching = new Set([7]);
      const failures = new Map<number, number>();

      while (watching.size > 0 && nowMs / 1000 < windowEnd) {
        await pollDisputedJobs(
          asClient(client),
          watching,
          new Set(),
          (jobId, downloadFailures) =>
            handleDisputedJob(
              asClient(client),
              jobId,
              "0xvoter",
              "https://g/",
              rl,
              downloadFailures,
            ),
          failures,
        );
        nowMs += 12_000;
      }

      expect(promptedAt).toHaveLength(1);
      if (failure === "lookup") {
        expect(promptedAt[0]).toBeGreaterThan(1800 - 12);
        expect(promptedAt[0]).toBeLessThanOrEqual(1800);
      } else {
        expect(promptedAt[0]).toBeGreaterThan(3000);
      }
      expect(client.voteReject).toHaveBeenCalledWith(7n);
      vi.restoreAllMocks();
    },
  );

  it("retries a job whose dispute check fails on the RPC", async () => {
    const client = fakeClient();
    client.policy.disputed.mockRejectedValueOnce(new Error("rpc down"));
    vi.spyOn(console, "log").mockImplementation(() => {});
    const watching = new Set([7]);
    const handle = vi.fn(async () => {});

    await pollDisputedJobs(asClient(client), watching, new Set(), handle);

    expect(handle).not.toHaveBeenCalled();
    expect([...watching]).toEqual([7]);
    vi.restoreAllMocks();
  });
});

describe("requireLogRpc", () => {
  it("passes on an RPC that serves eth_getLogs", async () => {
    const client = fakeClient();
    await expect(requireLogRpc(asClient(client))).resolves.toBeUndefined();
    expect(client.publicClient.getLogs).toHaveBeenCalledWith({
      address: client.policy.address,
      fromBlock: 90n,
      toBlock: 100n,
    });
  });

  it("fails at startup on an RPC that does not", async () => {
    const client = fakeClient();
    client.publicClient.getLogs.mockRejectedValueOnce(
      new Error("limit exceeded"),
    );
    await expect(requireLogRpc(asClient(client))).rejects.toThrow(
      /does not serve eth_getLogs \(limit exceeded\).*RPC_URL/,
    );
  });

  it("also reads logs from about one voting window ago", async () => {
    const client = fakeClient();
    client.publicClient.getBlockNumber.mockResolvedValue(1_000_000n);
    await requireLogRpc(asClient(client));
    // 3 600 s at two seconds a block
    expect(client.publicClient.getLogs).toHaveBeenLastCalledWith({
      address: client.policy.address,
      fromBlock: 998_200n,
      toBlock: 998_210n,
    });
  });

  it("fails at startup on an RPC that drops logs as old as the voting window", async () => {
    const client = fakeClient();
    client.publicClient.getBlockNumber.mockResolvedValue(1_000_000n);
    client.publicClient.getLogs
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error("403 archive requests need a token"));
    await expect(requireLogRpc(asClient(client))).rejects.toThrow(
      /as old as the voting window \(1\.0 h, around block 998200\).*archive endpoint/,
    );
  });
});
