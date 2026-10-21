import { once } from "node:events";
import { type Server, createServer } from "node:http";
import type { AddressInfo } from "node:net";
import type { createInterface } from "node:readline/promises";
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  MANIFEST_DOWNLOAD_ATTEMPTS,
  ManifestUnavailableError,
  handleDisputedJob,
} from "../examples/voter/watch.js";
import {
  DeliverableManifest,
  type ERC8183Client,
} from "../src/erc8183/index.js";

const payload = {
  version: 1,
  job_id: 777,
  chain_id: 97,
  contracts: {},
  response: { content: "test-only-loopback-content" },
  metadata: {},
};

describe("disputed-job download boundary", () => {
  let server: Server;
  let port: number;
  const hits: string[] = [];

  beforeAll(async () => {
    server = createServer((req, res) => {
      hits.push(req.url ?? "");
      if (req.url === "/redirect") {
        res.writeHead(302, { Location: "/manifest", Connection: "close" });
        res.end();
        return;
      }
      res.writeHead(200, {
        "Content-Type": "application/json",
        Connection: "close",
      });
      res.end(req.url === "/invalid" ? "not json" : JSON.stringify(payload));
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    port = (server.address() as AddressInfo).port;
  });

  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  afterEach(() => {
    hits.length = 0;
    vi.restoreAllMocks();
  });

  describe.each(["s", "r"])("operator choice %s", (choice) => {
    it.each(["/manifest", "/invalid", "/redirect"])(
      "never fetches loopback %s before prompting",
      async (path) => {
        const output = vi.spyOn(console, "log").mockImplementation(() => {});
        const voteReject = vi.fn().mockResolvedValue({});
        const client = {
          policy: {
            hasVoted: vi.fn().mockResolvedValue(false),
            disputeWindow: vi.fn().mockResolvedValue(3600n),
          },
          getJob: vi.fn().mockResolvedValue({
            client: "client",
            provider: "provider",
            budget: 1n,
            status: "Submitted",
            submittedAt: BigInt(Math.floor(Date.now() / 1000) - 60),
            deliverable: DeliverableManifest.fromDict(payload).manifestHash(),
          }),
          getDeliverableUrl: vi
            .fn()
            .mockResolvedValue(`http://127.0.0.1:${port}${path}`),
          voteReject,
        };
        const hitsAtPrompt: string[][] = [];
        const question = vi.fn().mockImplementation(async () => {
          hitsAtPrompt.push([...hits]);
          return choice;
        });

        const handle = (downloadFailures: number) =>
          handleDisputedJob(
            client as unknown as ERC8183Client,
            777,
            "voter",
            "https://gateway.example/ipfs/",
            { question } as unknown as ReturnType<typeof createInterface>,
            downloadFailures,
          );
        // The refused download counts as a failed one: retried, then voted on.
        for (let n = 0; n < MANIFEST_DOWNLOAD_ATTEMPTS - 1; n++) {
          await expect(handle(n)).rejects.toBeInstanceOf(
            ManifestUnavailableError,
          );
        }
        await handle(MANIFEST_DOWNLOAD_ATTEMPTS - 1);

        expect(hitsAtPrompt).toEqual([[]]);
        expect(hits).toEqual([]);
        expect(question).toHaveBeenCalledTimes(1);
        expect(output.mock.calls.flat().join("\n")).toContain(
          "deliverable unavailable: could not fetch manifest",
        );
        expect(output).not.toHaveBeenCalledWith("test-only-loopback-content");
        if (choice === "r") {
          expect(voteReject).toHaveBeenCalledTimes(1);
          expect(voteReject).toHaveBeenCalledWith(777n);
        } else {
          expect(voteReject).not.toHaveBeenCalled();
          expect(output).toHaveBeenCalledWith("  skipped job 777");
        }
      },
    );
  });
});
