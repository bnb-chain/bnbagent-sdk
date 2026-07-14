/**
 * Buyer counterpart: discover the agent, fetch its A2A card, get a signed quote.
 *
 * Three stages — each gated by what you configure:
 *   1. Discover (optional): when AGENT_ID is set, resolve the provider's A2A
 *      endpoint from the ERC-8004 registry (the inverse of register.ts).
 *      Otherwise fall back to A2A_BASE_URL directly.
 *   2. Quote: fetch /.well-known/agent-card.json, then JSON-RPC message/send
 *      with negotiation terms → wallet-signed quote.
 *   3. On-chain (optional): when BUYER_PRIVATE_KEY is set, anchor the quoted
 *      description with createJob → registerJob → setBudget → fund. Without it,
 *      stops after printing the quote — a chain-free first run.
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

/** ERC-8004 discovery when AGENT_ID is set; A2A_BASE_URL fallback otherwise. */
async function discoverCardUrl(): Promise<string> {
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
  const base = (process.env.A2A_BASE_URL ?? "http://localhost:8010").replace(
    /\/+$/,
    "",
  );
  return `${base}/.well-known/agent-card.json`;
}

interface Quote {
  provider_address: string;
  response: { terms: { price: string; currency?: string } };
  negotiation_hash?: string;
  provider_sig?: string;
  [key: string]: unknown;
}

async function getQuote(cardUrl: string): Promise<Quote> {
  const cardResp = await fetch(cardUrl, {
    signal: AbortSignal.timeout(10_000),
  });
  if (!cardResp.ok) {
    throw new Error(`card fetch failed: HTTP ${cardResp.status}`);
  }
  const card = (await cardResp.json()) as {
    name: string;
    url: string;
    skills: { id: string }[];
  };
  console.log(
    `[card] ${card.name} — skills: ${card.skills.map((s) => s.id).join(", ")}`,
  );

  const inquiry = {
    skill: "negotiate-erc8183-job",
    task_description: "Summarize the latest BNB Chain ecosystem news",
    terms: {
      deliverables: "One markdown summary of the latest BNB Chain news",
      quality_standards: "At least 5 sourced items, no older than 48h",
    },
  };
  const rpc = {
    jsonrpc: "2.0",
    id: 1,
    method: "message/send",
    params: {
      message: {
        kind: "message",
        role: "user",
        messageId: randomUUID(),
        parts: [{ kind: "data", data: inquiry }],
      },
    },
  };
  const reply = (await (
    await fetch(card.url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(rpc),
      signal: AbortSignal.timeout(30_000),
    })
  ).json()) as {
    error?: { message: string };
    result?: { parts: { data: Quote }[] };
  };
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

async function fundJob(quote: Quote): Promise<void> {
  const buyerKey = process.env.BUYER_PRIVATE_KEY;
  if (!buyerKey) {
    console.log(
      "[on-chain] BUYER_PRIVATE_KEY not set — stopping after quote (chain-free run)",
    );
    return;
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
  // ecrecover(negotiation_hash, provider_sig) == job.provider.
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
}

async function main(): Promise<void> {
  const quote = await getQuote(await discoverCardUrl());
  await fundJob(quote);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
