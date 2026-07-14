/**
 * Register the Blockchain News Agent on the ERC-8004 Identity Registry.
 *
 * One-time on-chain operation so clients can discover this agent. This
 * example serves a plain HTTP surface, so it registers a generic `"web"`
 * endpoint (the EIP-8004 endpoint `name` is an open string; A2A / MCP are the
 * spec-named types). For a protocol-typed registration see
 * `../../a2a-agent/scripts/register.ts`.
 *
 * TypeScript port of `python/examples/agent-server/scripts/register.py`
 * (EVM-only — the TS SDK ships no TWAK wallet).
 *
 * Run:
 *   pnpm -C typescript exec tsx examples/agent-server/scripts/register.ts
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { AgentEndpoint, ERC8004Agent } from "../../../src/erc8004/index.js";
import { EVMWalletProvider, loadEnv } from "../../../src/index.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/agent-server
loadEnv(ROOT);

async function main(): Promise<void> {
  const network = process.env.NETWORK ?? "bsc-testnet";
  const walletPassword = process.env.WALLET_PASSWORD;
  const privateKey = process.env.PRIVATE_KEY;
  if (!walletPassword) {
    throw new Error("WALLET_PASSWORD is required (set it in .env)");
  }
  if (!privateKey) {
    throw new Error("PRIVATE_KEY is required on first run (see .env.example)");
  }

  const name = process.env.AGENT_NAME ?? "blockchain-news";
  const description =
    process.env.AGENT_DESCRIPTION ??
    "Blockchain news search agent. Searches DuckDuckGo for latest news and " +
      "delivers structured results via the ERC-8183 protocol.";
  const agentHost = (process.env.AGENT_HOST ?? "http://localhost:8003").replace(
    /\/+$/,
    "",
  );
  const endpointUrl = `${agentHost}/erc8183/status`;

  const wallet = new EVMWalletProvider({
    password: walletPassword,
    privateKey,
  });
  const sdk = await ERC8004Agent.create({ walletProvider: wallet, network });

  console.log(`Registering ${name} (${sdk.walletAddress})`);
  console.log(`  web endpoint: ${endpointUrl}`);

  const agentUri = sdk.generateAgentUri({
    name,
    description,
    endpoints: [new AgentEndpoint({ name: "web", endpoint: endpointUrl })],
  });
  const result = await sdk.registerAgent(agentUri);

  console.log(`  tx:       ${result.transactionHash}`);
  console.log(`  agent_id: ${result.agentId}`);
  console.log("Save AGENT_ID so clients can discover this agent on-chain.");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
