/**
 * A2A-fronted ERC-8183 provider agent.
 *
 * TypeScript port of `python/examples/a2a-agent/src/server.py`. The agent's
 * outward surface is **A2A** (agent card + JSON-RPC `message/send`); everything
 * under it is plain SDK protocol capability (`NegotiationHandler` quote
 * signing, `ERC8183Client` job reads). The SDK ships no serving runtime — this
 * file IS the serving layer, and it is yours to own.
 *
 * The wire format follows the A2A spec (card at
 * `/.well-known/agent-card.json`, JSON-RPC 2.0 `message/send` with data parts)
 * but is hand-rolled on `node:http` to stay minimal and dependency-light. For
 * a production agent, the official `@a2a-js/sdk` implements the same contract
 * with full task/streaming support — clients speaking spec A2A interoperate
 * with either.
 *
 * Skills:
 *   negotiate-erc8183-job  — returns a wallet-signed price quote
 *   erc8183-job-status     — read-only on-chain job lookup
 *
 * Run:
 *   pnpm -C typescript example:a2a-server
 *
 * Env (examples/a2a-agent/.env — see .env.example):
 *   PRIVATE_KEY / WALLET_PASSWORD, NETWORK, AGENT_NAME / AGENT_DESCRIPTION,
 *   A2A_BASE_URL (default http://localhost:8010), ERC8183_SERVICE_PRICE
 */

import { randomUUID } from "node:crypto";
import {
  type IncomingMessage,
  type ServerResponse,
  createServer,
} from "node:http";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ERC8183Client,
  JobStatus,
  NegotiationHandler,
} from "../../../src/erc8183/index.js";
import { EVMWalletProvider, loadEnv } from "../../../src/index.js";
import {
  RateLimitExceeded,
  SlidingWindowLimiter,
} from "../../../src/utils/index.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/a2a-agent
loadEnv(ROOT);

// ── Provider identity + protocol stack (plain SDK capability, no serving) ──

const NETWORK = process.env.NETWORK ?? "bsc-testnet";
const AGENT_NAME = process.env.AGENT_NAME ?? "a2a-demo-agent";
const AGENT_DESCRIPTION =
  process.env.AGENT_DESCRIPTION ??
  "Demo provider that quotes ERC-8183 jobs over the A2A protocol.";
const BASE_URL = (process.env.A2A_BASE_URL ?? "http://localhost:8010").replace(
  /\/+$/,
  "",
);
const SERVICE_PRICE =
  process.env.ERC8183_SERVICE_PRICE ?? "1000000000000000000";
const PORT = Number(new URL(BASE_URL).port || "8010");

// ── A2A surface (agent card) ──

const AGENT_CARD = {
  protocolVersion: "0.3.0",
  name: AGENT_NAME,
  description: AGENT_DESCRIPTION,
  url: `${BASE_URL}/a2a`,
  preferredTransport: "JSONRPC",
  version: "1.0.0",
  capabilities: { streaming: false, pushNotifications: false },
  defaultInputModes: ["application/json"],
  defaultOutputModes: ["application/json"],
  skills: [
    {
      id: "negotiate-erc8183-job",
      name: "Negotiate an ERC-8183 job",
      description:
        'Send a data part {"skill": "negotiate-erc8183-job", ' +
        '"task_description": "...", "terms": {...}} and receive a ' +
        "wallet-signed quote (price, currency, negotiation_hash, provider_sig). " +
        "Anchor the returned envelope on-chain via createJob.",
      tags: ["erc8183", "negotiation", "bnb-chain"],
      inputModes: ["application/json"],
      outputModes: ["application/json"],
    },
    {
      id: "erc8183-job-status",
      name: "ERC-8183 job status",
      description:
        'Send {"skill": "erc8183-job-status", "job_id": <int>} for a ' +
        "read-only on-chain job lookup.",
      tags: ["erc8183", "status"],
      inputModes: ["application/json"],
      outputModes: ["application/json"],
    },
  ],
};

// ── JSON-RPC helpers ──

interface RpcSuccess {
  jsonrpc: "2.0";
  id: unknown;
  result: unknown;
}
interface RpcError {
  jsonrpc: "2.0";
  id: unknown;
  error: { code: number; message: string };
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function rpcError(
  res: ServerResponse,
  id: unknown,
  code: number,
  message: string,
  status = 200,
): void {
  const body: RpcError = { jsonrpc: "2.0", id, error: { code, message } };
  sendJson(res, status, body);
}

function rpcResult(res: ServerResponse, id: unknown, result: unknown): void {
  const body: RpcSuccess = { jsonrpc: "2.0", id, result };
  sendJson(res, 200, body);
}

/** An A2A Message envelope carrying a single data part. */
function agentMessage(data: Record<string, unknown>): Record<string, unknown> {
  return {
    kind: "message",
    role: "agent",
    messageId: randomUUID(),
    parts: [{ kind: "data", data }],
  };
}

function extractDataPart(
  message: Record<string, unknown>,
): Record<string, unknown> | null {
  const parts = (message.parts as unknown[]) ?? [];
  for (const part of parts) {
    if (
      typeof part === "object" &&
      part !== null &&
      (part as { kind?: unknown }).kind === "data" &&
      typeof (part as { data?: unknown }).data === "object" &&
      (part as { data?: unknown }).data !== null
    ) {
      return (part as { data: Record<string, unknown> }).data;
    }
  }
  return null;
}

async function readBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(chunk as Buffer);
  }
  if (chunks.length === 0) {
    return undefined;
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
}

// ── Boot ──

async function main(): Promise<void> {
  const walletPassword = process.env.WALLET_PASSWORD ?? "demo-password";
  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("PRIVATE_KEY is required (see .env.example)");
  }
  const wallet = new EVMWalletProvider({
    password: walletPassword,
    privateKey,
  });
  const client = await ERC8183Client.create({
    walletProvider: wallet,
    network: NETWORK,
  });
  // Bind the quote signature to this chain + commerce contract (anti-replay).
  const negotiationHandler = await NegotiationHandler.fromErc8183Client(
    client,
    {
      servicePrice: SERVICE_PRICE,
      walletProvider: wallet,
    },
  );
  // Every accepted negotiate burns a wallet signature — throttle it.
  const negotiateLimiter = new SlidingWindowLimiter(30, 60);

  const server = createServer((req, res) => {
    void handle(req, res).catch((error) => {
      console.error(error instanceof Error ? error.stack : error);
      if (!res.headersSent) {
        rpcError(res, null, -32603, "Internal error");
      }
    });
  });

  async function handle(
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> {
    const url = new URL(req.url ?? "/", "http://localhost");
    const method = (req.method ?? "GET").toUpperCase();

    if (method === "GET" && url.pathname === "/.well-known/agent-card.json") {
      return sendJson(res, 200, AGENT_CARD);
    }
    if (!(method === "POST" && url.pathname === "/a2a")) {
      return sendJson(res, 404, { error: "Not found" });
    }

    let body: unknown;
    try {
      body = await readBody(req);
    } catch {
      return rpcError(res, null, -32700, "Parse error", 400);
    }
    if (typeof body !== "object" || body === null) {
      return rpcError(res, null, -32600, "Invalid Request", 400);
    }
    const rpc = body as Record<string, unknown>;
    const reqId = rpc.id;
    if (rpc.jsonrpc !== "2.0" || !("method" in rpc)) {
      return rpcError(res, reqId, -32600, "Invalid Request", 400);
    }
    if (rpc.method !== "message/send") {
      return rpcError(res, reqId, -32601, `Method not found: ${rpc.method}`);
    }

    const message =
      ((rpc.params as Record<string, unknown>)?.message as Record<
        string,
        unknown
      >) ?? {};
    const data = extractDataPart(message);
    if (data === null) {
      return rpcError(
        res,
        reqId,
        -32602,
        "message must carry a data part with a 'skill' field",
      );
    }
    const skill = data.skill;

    if (skill === "negotiate-erc8183-job") {
      const clientIp = req.socket.remoteAddress ?? "unknown";
      try {
        negotiateLimiter.check(clientIp);
      } catch (error) {
        if (error instanceof RateLimitExceeded) {
          return rpcError(res, reqId, -32000, "Rate limited, retry later");
        }
        throw error;
      }
      const terms = data.terms;
      const taskDescription = data.task_description;
      if (
        typeof terms !== "object" ||
        terms === null ||
        typeof taskDescription !== "string"
      ) {
        return rpcError(
          res,
          reqId,
          -32602,
          "negotiate-erc8183-job requires 'task_description' (string) and 'terms' (object)",
        );
      }
      let envelope: Record<string, unknown>;
      try {
        const result = await negotiationHandler.negotiate({
          task_description: taskDescription,
          terms,
        });
        envelope = result.toDict();
      } catch (error) {
        console.error(
          `negotiation failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        return rpcError(res, reqId, -32603, "Negotiation failed");
      }
      // The buyer needs the provider address for createJob (and to verify
      // ecrecover(negotiation_hash, provider_sig) == provider).
      envelope.provider_address = wallet.address;
      return rpcResult(res, reqId, agentMessage(envelope));
    }

    if (skill === "erc8183-job-status") {
      const jobId = data.job_id;
      if (typeof jobId !== "number" || !Number.isInteger(jobId)) {
        return rpcError(
          res,
          reqId,
          -32602,
          "erc8183-job-status requires an integer 'job_id'",
        );
      }
      try {
        const job = await client.getJob(BigInt(jobId));
        return rpcResult(
          res,
          reqId,
          agentMessage({
            job_id: Number(job.id),
            client: job.client,
            provider: job.provider,
            status: JobStatus[job.status],
            budget: job.budget.toString(),
            expired_at: job.expiredAt.toString(),
            submitted_at: job.submittedAt.toString(),
            deliverable: job.deliverable,
          }),
        );
      } catch (error) {
        console.error(
          `job lookup failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        return rpcError(res, reqId, -32603, `Job ${jobId} lookup failed`);
      }
    }

    return rpcError(
      res,
      reqId,
      -32602,
      `Unknown skill: ${JSON.stringify(skill)}`,
    );
  }

  server.listen(PORT, () => {
    console.info(
      `A2A agent ${wallet.address} — card at ${BASE_URL}/.well-known/agent-card.json`,
    );
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
