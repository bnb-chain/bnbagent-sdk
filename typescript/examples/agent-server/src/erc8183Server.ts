/**
 * HTTP serving factory for an ERC-8183 provider agent — the TypeScript
 * counterpart of `python/examples/agent-server/src/erc8183_server.py`.
 *
 * `createErc8183Server(...)` wires:
 *   - the ERC-8183 read/write routes (`negotiate`, `job/:id`,
 *     `job/:id/response`, `job/:id/verify`, `status`, `health`) onto a tiny
 *     `node:http` router, and
 *   - when `onJob` is given, a background funded-job loop
 *     (`fundedJobWatcher`) that picks up FUNDED jobs, runs `onJob`, and
 *     submits the deliverable on-chain — no external trigger exposed.
 *
 * The SDK underneath (`ERC8183JobOps`, `fundedJobWatcher`,
 * `NegotiationHandler`) is the headless primitive layer; everything here is
 * the example's own serving shell — copy the directory and own it. Settle is
 * permissionless on-chain and is delegated to `scripts/settle.ts`.
 */

import { type Server, createServer } from "node:http";
import {
  ERC8183Client,
  ERC8183Config,
  ERC8183JobOps,
  JobStatus,
  NegotiationHandler,
  fundedJobWatcher,
} from "../../../src/erc8183/index.js";
import {
  RateLimitExceeded,
  SlidingWindowLimiter,
} from "../../../src/utils/index.js";
import { type Route, makeRequestListener, route, sendJson } from "./http.js";

/** Result of {@link onJob}: a deliverable string, or `[string, metadata]`. */
export type OnJobResult =
  | string
  | [string, Record<string, unknown>]
  | Promise<string | [string, Record<string, unknown>]>;

/** Handler invoked for each funded job — the ONLY function an agent writes. */
export type OnJob = (job: Record<string, unknown>) => OnJobResult;

/** Options accepted by {@link createErc8183Server}. */
export interface Erc8183ServerOpts {
  /** Pre-built config; defaults to `ERC8183Config.fromEnv()`. */
  config?: ERC8183Config;
  /** Funded-job handler. When set, a background poll loop is started. */
  onJob?: OnJob;
  /** Route prefix. Default `/erc8183`. */
  prefix?: string;
  /** Funded-poll cadence (seconds). Falls back to
   * `ERC8183_FUNDED_POLL_INTERVAL` (default `30`). */
  fundedPollInterval?: number;
  /** Metadata merged into every submitted deliverable. */
  taskMetadata?: Record<string, unknown>;
  /** Extra routes to serve alongside the ERC-8183 ones (e.g. a `/search`
   * testing endpoint). */
  extraRoutes?: Route[];
}

/** A configured ERC-8183 server, ready to `listen()`. */
export interface Erc8183Server {
  readonly config: ERC8183Config;
  readonly jobOps: ERC8183JobOps;
  readonly agentAddress: string;
  readonly paymentToken: string;
  readonly paymentTokenDecimals: number;
  /** Start listening and (if `onJob` was given) the funded-job watcher. */
  listen(port: number, host?: string): Server;
  /** Stop the funded-job watcher (idempotent). */
  stop(): void;
}

// The SDK's error_code values are transport-neutral; this HTTP shell owns the
// mapping to status codes (mirrors the Python reference's _HTTP_STATUS).
const HTTP_STATUS: Record<string, number> = {
  budget_too_low: 402,
  not_assigned: 403,
  not_found: 404,
  job_expired: 408,
  wrong_status: 409,
  quote_expired: 410,
  description_invalid: 410,
  submit_deadline_passed: 410,
  payload_too_large: 413,
  internal_error: 500,
  chain_unavailable: 503,
};

function httpStatus(result: Record<string, unknown>, fallback: number): number {
  const code = result.error_code as string | undefined;
  return (code && HTTP_STATUS[code]) || fallback;
}

function readIntEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) {
    return fallback;
  }
  const value = Number(raw.trim());
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/**
 * Build (but don't start) an ERC-8183 provider server from config.
 *
 * Async because the underlying clients (`ERC8183Client`,
 * `NegotiationHandler.fromErc8183Client`) do an RPC round-trip at
 * construction to bind the network / payment token.
 */
export async function createErc8183Server(
  opts: Erc8183ServerOpts = {},
): Promise<Erc8183Server> {
  const config = opts.config ?? ERC8183Config.fromEnv();
  const prefix = opts.prefix ?? "/erc8183";

  const walletProvider = config.walletProvider;
  if (!walletProvider) {
    throw new Error(
      "ERC8183Config has no walletProvider. Set WALLET_PASSWORD (+ PRIVATE_KEY " +
        "on first run) or pass a config with a wallet provider.",
    );
  }
  // effectiveNetwork applies the ERC8183_*_ADDRESS env overrides; passing the
  // resolved NetworkConfig (not the "bsc-testnet" string) keeps them in force.
  const network = config.effectiveNetwork;

  const client = await ERC8183Client.create({ walletProvider, network });
  const jobOps = await ERC8183JobOps.create({
    walletProvider,
    network,
    storageProvider: config.storage,
    servicePrice: BigInt(config.servicePrice),
    agentUrl: config.agentUrl,
  });

  // Bind the negotiation signature to this chain + commerce contract so the
  // provider_sig cannot be replayed across networks (fromErc8183Client
  // auto-fills currency/chainId/verifyingContract).
  const negotiationHandler = await NegotiationHandler.fromErc8183Client(
    client,
    {
      servicePrice: config.servicePrice,
      walletProvider,
    },
  );

  // Fetch payment token + decimals once at startup so /status doesn't cost an
  // RPC per request. Non-fatal if lookup fails (RPC down at boot).
  let paymentToken = "";
  let paymentTokenDecimals = 18;
  try {
    paymentToken = await client.paymentToken();
    paymentTokenDecimals = await client.tokenDecimals();
  } catch (error) {
    console.warn(
      `[erc8183Server] payment_token lookup failed: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }

  const negotiateLimiter = new SlidingWindowLimiter(
    readIntEnv("ERC8183_NEGOTIATE_RATE_LIMIT", 120),
    readIntEnv("ERC8183_NEGOTIATE_RATE_WINDOW", 60),
  );

  const routes: Route[] = [
    route("GET", `${prefix}/health`, ({ res }) =>
      sendJson(res, 200, { status: "ok", service: "ERC-8183 Agent" }),
    ),

    route("GET", `${prefix}/status`, ({ res }) =>
      sendJson(res, 200, {
        status: "ok",
        agent_address: jobOps.agentAddress,
        commerce_address: config.effectiveCommerceAddress,
        router_address: config.effectiveRouterAddress,
        policy_address: config.effectivePolicyAddress,
        service_price: config.servicePrice,
        currency: paymentToken,
        decimals: paymentTokenDecimals,
      }),
    ),

    route("POST", `${prefix}/negotiate`, async ({ res, body, clientIp }) => {
      try {
        negotiateLimiter.check(clientIp);
      } catch (error) {
        if (error instanceof RateLimitExceeded) {
          return sendJson(res, 429, { error: "Too many requests" });
        }
        throw error;
      }
      if (
        typeof body !== "object" ||
        body === null ||
        !("terms" in (body as Record<string, unknown>))
      ) {
        return sendJson(res, 400, {
          error:
            "Request must include 'terms' with deliverables, quality_standards",
        });
      }
      try {
        const result = await negotiationHandler.negotiate(
          body as Record<string, unknown>,
        );
        return sendJson(res, 200, result.toDict());
      } catch (error) {
        console.error(
          `[erc8183Server] negotiation failed: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
        return sendJson(res, 500, { error: "Negotiation failed" });
      }
    }),

    route("GET", `${prefix}/job/:id/response`, async ({ res, params }) => {
      const result = await jobOps.getResponse(Number(params.id));
      // getResponse distinguishes "no response" (not_found → 404) from
      // "exists but temporarily unresolvable" (chain_unavailable → 503).
      sendJson(res, result.success ? 200 : httpStatus(result, 404), result);
    }),

    route("GET", `${prefix}/job/:id/verify`, async ({ res, params }) => {
      const result = await jobOps.verifyJob(Number(params.id));
      sendJson(res, result.valid ? 200 : httpStatus(result, 400), result);
    }),

    route("GET", `${prefix}/job/:id`, async ({ res, params }) => {
      const result = await jobOps.getJob(Number(params.id));
      sendJson(res, result.success ? 200 : httpStatus(result, 500), result);
    }),

    route("GET", "/", ({ res }) =>
      sendJson(res, 200, {
        service: "ERC-8183 Agent",
        agent_address: jobOps.agentAddress,
        endpoints: {
          job: `${prefix}/job/{job_id}`,
          response: `${prefix}/job/{job_id}/response`,
          verify: `${prefix}/job/{job_id}/verify`,
          negotiate: `${prefix}/negotiate`,
          status: `${prefix}/status`,
          health: `${prefix}/health`,
        },
      }),
    ),

    ...(opts.extraRoutes ?? []),
  ];

  const listener = makeRequestListener(routes);

  // ── funded-job watcher ──
  const pollInterval =
    opts.fundedPollInterval ?? readIntEnv("ERC8183_FUNDED_POLL_INTERVAL", 30);
  let abort: AbortController | null = null;

  async function onFunded(job: Record<string, unknown>): Promise<unknown> {
    const onJob = opts.onJob;
    if (!onJob) {
      return;
    }
    const jobId = job.jobId as number;
    const taskResult = await onJob(job);
    const [content, jobMeta] = Array.isArray(taskResult)
      ? taskResult
      : [taskResult, undefined];
    const metadata = { ...(opts.taskMetadata ?? {}), ...(jobMeta ?? {}) };
    const submission = await jobOps.submitResult(
      jobId,
      content,
      Object.keys(metadata).length > 0 ? metadata : undefined,
    );
    if (submission.success) {
      console.info(
        `[erc8183Server] job #${jobId} submitted, tx=${submission.txHash}`,
      );
      return;
    }
    console.error(
      `[erc8183Server] job #${jobId} submission failed: ${submission.error}`,
    );
    // Retryable → let fundedJobWatcher re-fire next tick; permanent → drop.
    return submission.retryable ? { retry: true } : undefined;
  }

  return {
    config,
    jobOps,
    agentAddress: jobOps.agentAddress,
    paymentToken,
    paymentTokenDecimals,

    listen(port: number, host = "0.0.0.0"): Server {
      const server = createServer((req, res) => {
        void listener(req, res);
      });
      server.listen(port, host);
      if (opts.onJob) {
        abort = new AbortController();
        console.info(
          `[erc8183Server] funded-job poll loop starting (interval=${pollInterval}s)`,
        );
        void fundedJobWatcher(jobOps, onFunded, {
          interval: pollInterval,
          stop: abort.signal,
        }).catch((error) => {
          console.error(
            `[erc8183Server] funded-job watcher crashed: ${
              error instanceof Error ? error.message : String(error)
            }`,
          );
        });
      }
      return server;
    },

    stop(): void {
      abort?.abort();
      abort = null;
    },
  };
}

export { JobStatus };
