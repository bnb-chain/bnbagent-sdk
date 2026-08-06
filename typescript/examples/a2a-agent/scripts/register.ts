/**
 * Register this A2A agent on the ERC-8004 Identity Registry.
 *
 * One-time operation. The registered endpoint is the A2A discovery document
 * (`{base}/.well-known/agent-card.json`) — built with `AgentEndpoint.a2a()`,
 * so buyers that discover this agent on-chain can fetch the card directly.
 *
 * TypeScript port of `python/examples/a2a-agent/scripts/register.py`
 * (EVM-only — the TS SDK ships no TWAK wallet).
 *
 * Run:
 *   pnpm -C typescript exec tsx examples/a2a-agent/scripts/register.ts
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { AgentEndpoint, ERC8004Agent } from "../../../src/erc8004/index.js";
import { EVMWalletProvider, loadEnv } from "../../../src/index.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/a2a-agent
loadEnv(ROOT);

async function main(): Promise<void> {
  const network = process.env.NETWORK ?? "bsc-testnet";
  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("PRIVATE_KEY is required (see .env.example)");
  }
  const baseUrl = process.env.A2A_BASE_URL ?? "http://localhost:8010";
  const name = process.env.AGENT_NAME ?? "a2a-demo-agent";
  const description =
    process.env.AGENT_DESCRIPTION ??
    "Demo provider that quotes ERC-8183 jobs over the A2A protocol.";

  const wallet = new EVMWalletProvider({
    password: process.env.WALLET_PASSWORD ?? "demo-password",
    privateKey,
  });
  const sdk = await ERC8004Agent.create({ walletProvider: wallet, network });

  const endpoint = AgentEndpoint.a2a(baseUrl, { version: "0.3.0" });
  console.log(`Registering ${name} (${sdk.walletAddress})`);
  console.log(`  A2A endpoint: ${endpoint.endpoint}`);

  const agentUri = sdk.generateAgentUri({
    name,
    description,
    endpoints: [endpoint],
  });
  const result = await sdk.registerAgent(agentUri);

  console.log(`  tx:       ${result.transactionHash}`);
  console.log(`  agent_id: ${result.agentId}`);
  console.log(
    "Save AGENT_ID to .env so buyers can discover this agent on-chain.",
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
