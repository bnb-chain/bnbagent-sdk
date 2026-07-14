/**
 * Blockchain News Agent — ERC-8183 provider (HTTP serving reference).
 *
 * TypeScript port of `python/examples/agent-server/src/service.py`. A news
 * search agent that:
 *   1. receives funded jobs via ERC-8183 (the funded-job poll loop),
 *   2. searches DuckDuckGo for blockchain news,
 *   3. returns a formatted report the SDK submits on-chain.
 *
 * Unlike the Python reference there is no TWAK wallet switch — the TypeScript
 * SDK ships an EVM wallet provider only, so this uses `ERC8183Config.fromEnv()`
 * (EVM keystore) end to end.
 *
 * Run:
 *   pnpm -C typescript example:agent-server
 *   # or: pnpm -C typescript exec tsx examples/agent-server/src/service.ts
 *
 * Env (examples/agent-server/.env — see .env.example):
 *   WALLET_PASSWORD, PRIVATE_KEY (first run), NETWORK, ERC8183_AGENT_URL,
 *   ERC8183_SERVICE_PRICE, PORT (default 8003), ERC8183_FUNDED_POLL_INTERVAL, …
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { ERC8183Config } from "../../../src/erc8183/index.js";
import { loadEnv } from "../../../src/index.js";
import { LocalStorageProvider } from "../../../src/storage/index.js";
import { createErc8183Server } from "./erc8183Server.js";
import { route, sendJson } from "./http.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/agent-server
loadEnv(ROOT);

// ── Storage backend — pick ONE ──
// (a) Local filesystem (default). ERC8183_AGENT_URL is required: the SDK
//     rewrites file:// URLs to this public base URL.
const storage = LocalStorageProvider.fromEnv();
// (b) IPFS via Pinata — set STORAGE_API_KEY (Pinata JWT), then swap to:
// import { IPFSStorageProvider } from "../../../src/storage/index.js";
// const storage = IPFSStorageProvider.fromEnv();

const PORT = Number(process.env.PORT ?? "8003");

// ---------------------------------------------------------------------------
// News search (DuckDuckGo lite over global fetch — no runtime dependency)
// ---------------------------------------------------------------------------

interface NewsItem {
  title: string;
  body: string;
  url: string;
}

function stripTags(html: string): string {
  return html
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Best-effort blockchain-news search via DuckDuckGo's dependency-free "lite"
 * HTML endpoint. Returns `[]` on any failure (network, parse, empty) so the
 * agent still produces a deliverable rather than stranding a funded job.
 */
async function searchNews(query: string, maxResults = 10): Promise<NewsItem[]> {
  try {
    const resp = await fetch("https://lite.duckduckgo.com/lite/", {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "Mozilla/5.0 (compatible; bnbagent-example/1.0)",
      },
      body: new URLSearchParams({ q: query }).toString(),
      signal: AbortSignal.timeout(15_000),
    });
    if (!resp.ok) {
      return [];
    }
    const html = await resp.text();
    const linkRe =
      /<a[^>]*class="result-link"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
    const snippetRe = /<td[^>]*class="result-snippet"[^>]*>([\s\S]*?)<\/td>/g;
    const snippets = [...html.matchAll(snippetRe)].map((m) =>
      stripTags(m[1] ?? ""),
    );
    const items: NewsItem[] = [];
    let i = 0;
    for (const m of html.matchAll(linkRe)) {
      if (items.length >= maxResults) {
        break;
      }
      items.push({
        url: m[1] ?? "",
        title: stripTags(m[2] ?? ""),
        body: snippets[i] ?? "",
      });
      i += 1;
    }
    return items;
  } catch (error) {
    console.warn(
      `[blockchain-news] search failed: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
    return [];
  }
}

function formatNewsResults(query: string, items: NewsItem[]): string {
  if (items.length === 0) {
    return `No news found for query: ${query}`;
  }
  const lines = [
    "# Blockchain News Search Results",
    "",
    `**Query:** ${query}`,
    `**Results:** ${items.length} items`,
    "",
    "---",
    "",
  ];
  items.forEach((item, idx) => {
    lines.push(`## ${idx + 1}. ${item.title}`, "");
    if (item.body) {
      lines.push(item.body, "");
    }
    if (item.url) {
      lines.push(`[Read more](${item.url})`, "");
    }
    lines.push("---", "");
  });
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// The ERC-8183 task handler — the ONLY function you need to write.
// The SDK calls this for each funded job; return (report, metadata).
// ---------------------------------------------------------------------------

async function processTask(
  job: Record<string, unknown>,
): Promise<[string, Record<string, unknown>]> {
  const { parseJobDescription } = await import("../../../src/erc8183/index.js");
  const rawDescription = String(job.description ?? "blockchain news");
  const parsed = parseJobDescription(rawDescription);
  const query = parsed?.task ?? rawDescription;
  console.info(`[blockchain-news] searching news for: ${query.slice(0, 80)}…`);

  const results = await searchNews(query, 10);
  console.info(`[blockchain-news] found ${results.length} news items`);

  return [
    formatNewsResults(query, results),
    { agent: "blockchain-news", query },
  ];
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const config = ERC8183Config.fromEnv(storage);

  // Direct /search endpoint for testing without ERC-8183.
  const searchRoute = route("POST", "/search", async ({ res, body }) => {
    const req = (body ?? {}) as { query?: string; max_results?: number };
    if (!req.query) {
      return sendJson(res, 400, { error: "query is required" });
    }
    const results = await searchNews(req.query, req.max_results ?? 10);
    sendJson(res, 200, {
      success: true,
      query: req.query,
      results_count: results.length,
      results,
    });
  });

  const server = await createErc8183Server({
    config,
    onJob: processTask,
    extraRoutes: [searchRoute],
  });

  console.log(`
${"=".repeat(55)}
  Blockchain News Agent (ERC-8183 Provider)
${"=".repeat(55)}
  Port:       ${PORT}
  Agent:      ${server.agentAddress}
  Commerce:   ${config.effectiveCommerceAddress}
  Router:     ${config.effectiveRouterAddress}
  Policy:     ${config.effectivePolicyAddress}
  Storage:    ${storage.constructor.name}
  Price:      ${Number(config.servicePrice) / 1e18} tokens

  ERC-8183 endpoints:
    POST /erc8183/negotiate      — Negotiation
    GET  /erc8183/job/{id}       — Job details
    GET  /erc8183/status         — Agent status
  Direct (testing):
    POST /search                 — Direct news search
    GET  /erc8183/health         — Health check
${"=".repeat(55)}
`);

  server.listen(PORT);
  console.info(`[blockchain-news] listening on http://localhost:${PORT}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
