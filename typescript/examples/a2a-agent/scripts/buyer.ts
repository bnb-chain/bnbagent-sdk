/**
 * Buyer counterpart: discover the agent, fetch its A2A card, get a signed quote.
 *
 * Stages — each gated by what you configure:
 *   1. Discover (optional): when AGENT_ID is set, resolve the provider's A2A
 *      endpoint from the ERC-8004 registry (the inverse of register.ts).
 *      Otherwise fall back to A2A_BASE_URL directly.
 *   2. Resolve message URL: GET /.well-known/agent-card.json and use the card's
 *      advertised url; if discovery fails (e.g. a POST-only AgentCore invoke
 *      endpoint serves no GET-able card), POST skills straight to the base.
 *   3. Quote: JSON-RPC message/send with negotiation terms → wallet-signed quote.
 *   4. On-chain (optional): when BUYER_PRIVATE_KEY is set, anchor the quoted
 *      description with createJob → registerJob → setBudget → fund, then read
 *      the job's status once. Without the key, stops after the quote.
 *
 * Delivery models differ by seller: this example server is negotiate +
 * status-read only. A studio-style seller instead delivers on a PUSH signal —
 * after funding, send an A2A `{"skill":"notify_funded","job_id":<int>}` message
 * to trigger delivery, then poll the CHAIN for SUBMITTED. See `checkStatus`.
 *
 * TypeScript port of `python/examples/a2a-agent/scripts/buyer.py`.
 *
 * Run:
 *   pnpm -C typescript exec tsx examples/a2a-agent/scripts/buyer.ts
 */

import { randomUUID } from "node:crypto";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { AgentURIGenerator, ERC8004Agent } from "../../../src/erc8004/index.js";
import {
  ERC8183Client,
  buildJobDescription,
} from "../../../src/erc8183/index.js";
import { EVMWalletProvider, loadEnv } from "../../../src/index.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/a2a-agent
loadEnv(ROOT);

const NETWORK = process.env.NETWORK ?? "bsc-testnet";

/** Seller base URL: ERC-8004 discovery when AGENT_ID is set; A2A_BASE_URL otherwise. */
async function discoverBaseUrl(): Promise<string> {
  const agentId = process.env.AGENT_ID;
  if (agentId) {
    const key = process.env.BUYER_PRIVATE_KEY ?? process.env.PRIVATE_KEY;
    if (!key) {
      throw new Error(
        "AGENT_ID is set but no key to build the lookup client — set BUYER_PRIVATE_KEY or PRIVATE_KEY",
      );
    }
    // Read-only lookup still needs a wallet for client construction.
    const wallet = new EVMWalletProvider({
      password: "lookup-only",
      privateKey: key,
    });
    const sdk = await ERC8004Agent.create({
      walletProvider: wallet,
      network: NETWORK,
    });
    const info = await sdk.getAgentInfo(Number(agentId));
    const registration = AgentURIGenerator.decodeRegistrationFileFromBase64(
      info.agentURI as string,
    );
    const services =
      (registration.services as { name?: string; endpoint?: string }[]) ?? [];
    for (const ep of services) {
      if (ep.name === "A2A" && ep.endpoint) {
        console.log(`[discover] agent ${agentId} → ${ep.endpoint}`);
        return ep.endpoint;
      }
    }
    throw new Error(`agent ${agentId} has no A2A endpoint registered`);
  }
  return (process.env.A2A_BASE_URL ?? "http://localhost:8010").replace(
    /\/+$/,
    "",
  );
}

/**
 * Agent-card discovery URL for `base`, inserting the well-known path BEFORE
 * any query string. Naive `${base}/.well-known/agent-card.json` breaks on an
 * AgentCore invoke URL (`…/invocations?qualifier=DEFAULT`): the path would land
 * after the query and the URL becomes unreachable (BUG-025). A URL object keeps
 * the query where it belongs and is idempotent when the path is already there.
 */
function agentCardUrl(base: string): string {
  const u = new URL(base);
  const path = u.pathname.replace(/\/+$/, "");
  if (!path.endsWith("/.well-known/agent-card.json")) {
    u.pathname = `${path}/.well-known/agent-card.json`;
  }
  return u.toString();
}

/**
 * Resolve the URL to POST JSON-RPC `message/send` to.
 *
 * Tries A2A discovery first (GET the agent card, use its advertised `url`).
 * AgentCore invoke endpoints are POST-only and serve no GET-able card, so a
 * failed discovery is expected there — fall back to POSTing skill payloads
 * straight to the base invoke URL instead of hard-failing (BUG-025).
 */
async function resolveMessageUrl(base: string): Promise<string> {
  const cardUrl = agentCardUrl(base);
  try {
    const resp = await fetch(cardUrl, { signal: AbortSignal.timeout(10_000) });
    if (resp.ok) {
      const card = (await resp.json()) as {
        name: string;
        url: string;
        skills: { id: string }[];
      };
      console.log(
        `[card] ${card.name} — skills: ${card.skills.map((s) => s.id).join(", ")}`,
      );
      return card.url ?? base;
    }
    console.warn(
      `[discover] agent-card GET → HTTP ${resp.status}; skipping discovery`,
    );
  } catch (error) {
    console.warn(
      `[discover] agent-card GET failed (${
        error instanceof Error ? error.message : String(error)
      }); skipping discovery`,
    );
  }
  console.log(
    `[discover] POSTing skills directly to ${base} (POST-only endpoint, no GET-able card)`,
  );
  return base;
}

interface Quote {
  provider_address: string;
  response: { terms: { price: string; currency?: string } };
  negotiation_hash?: string;
  provider_sig?: string;
  [key: string]: unknown;
}

interface RpcReply {
  error?: { message: string };
  result?: { parts: { data: Record<string, unknown> }[] };
}

/** POST a single-skill A2A `message/send` and return the parsed JSON-RPC reply. */
async function sendSkill(
  messageUrl: string,
  data: Record<string, unknown>,
): Promise<RpcReply> {
  const rpc = {
    jsonrpc: "2.0",
    id: 1,
    method: "message/send",
    params: {
      message: {
        kind: "message",
        role: "user",
        messageId: randomUUID(),
        parts: [{ kind: "data", data }],
      },
    },
  };
  const resp = await fetch(messageUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(rpc),
    signal: AbortSignal.timeout(30_000),
  });
  return (await resp.json()) as RpcReply;
}

async function negotiate(messageUrl: string): Promise<Quote> {
  const reply = await sendSkill(messageUrl, {
    skill: "negotiate-erc8183-job",
    task_description: "Summarize the latest BNB Chain ecosystem news",
    terms: {
      deliverables: "One markdown summary of the latest BNB Chain news",
      quality_standards: "At least 5 sourced items, no older than 48h",
    },
  });
  if (reply.error) {
    throw new Error(`A2A error: ${reply.error.message}`);
  }
  const quote = reply.result?.parts[0]?.data as Quote;
  const terms = quote.response?.terms ?? {};
  console.log(`[quote] price=${terms.price} currency=${terms.currency}`);
  console.log(`[quote] negotiation_hash=${quote.negotiation_hash}`);
  console.log(
    `[quote] provider_sig=${String(quote.provider_sig).slice(0, 42)}…`,
  );
  return quote;
}

async function fundJob(quote: Quote): Promise<bigint | null> {
  const buyerKey = process.env.BUYER_PRIVATE_KEY;
  if (!buyerKey) {
    console.log(
      "[on-chain] BUYER_PRIVATE_KEY not set — stopping after quote (chain-free run)",
    );
    return null;
  }
  const wallet = new EVMWalletProvider({
    password: process.env.BUYER_WALLET_PASSWORD ?? "demo-password",
    privateKey: buyerKey,
  });
  const client = await ERC8183Client.create({
    walletProvider: wallet,
    network: NETWORK,
  });

  const provider = quote.provider_address;
  const price = BigInt(quote.response.terms.price);
  // Anchor the SAME signed terms on-chain so provider_sig stays verifiable:
  // EOA recovery or ERC-1271 verification resolves to job.provider.
  const description = buildJobDescription(quote);

  const created = await client.createJob({
    provider,
    expiredAt: BigInt(Math.floor(Date.now() / 1000) + 26 * 3600), // > 24h dispute window
    description,
  });
  const jobId = created.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(
    `[on-chain] createJob → job ${jobId} (${created.transactionHash})`,
  );
  await client.registerJob(jobId);
  await client.setBudget(jobId, price);
  await client.fund(jobId, price);
  console.log(`[on-chain] job ${jobId} FUNDED with ${price} raw units`);
  return jobId;
}

/**
 * Read the funded job's on-chain status once, via this example server's
 * `erc8183-job-status` skill.
 *
 * Two delivery models — mind the difference:
 *   • THIS example server negotiates and reads status but performs NO
 *     delivery, so the job stays FUNDED; a single read shows the lifecycle
 *     without spinning forever.
 *   • A studio-style seller delivers on a PUSH signal instead: after funding,
 *     the buyer sends an A2A `{"skill":"notify_funded","job_id":<int>}` message
 *     to trigger delivery, then polls the CHAIN for the job reaching SUBMITTED
 *     to read the deliverable_url. There is no server-side job-query endpoint.
 */
async function checkStatus(messageUrl: string, jobId: bigint): Promise<void> {
  const reply = await sendSkill(messageUrl, {
    skill: "erc8183-job-status",
    job_id: Number(jobId),
  });
  if (reply.error) {
    console.warn(`[status] lookup failed: ${reply.error.message}`);
    return;
  }
  const status = reply.result?.parts[0]?.data ?? {};
  console.log(`[status] job ${jobId} → ${JSON.stringify(status)}`);
}

async function main(): Promise<void> {
  const messageUrl = await resolveMessageUrl(await discoverBaseUrl());
  const quote = await negotiate(messageUrl);
  const jobId = await fundJob(quote);
  if (jobId !== null) {
    await checkStatus(messageUrl, jobId);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
