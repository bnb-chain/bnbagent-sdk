/**
 * Read-only live smoke test against BNB Chain testnet.
 *
 * Unlike the unit suite (which mocks the transport), this script drives the
 * SDK's read path against the ACTUAL deployed ERC-8183 contracts over a real
 * RPC. Its purpose is to validate the one thing mocks structurally cannot:
 * that viem's ABI encode/decode wiring agrees with the real on-chain bytes
 * (tuple field order, address checksumming, uint widths, enum decoding).
 *
 * It signs nothing, sends no transactions, and spends no gas — it only calls
 * `view` functions. It is intentionally NOT part of the PR CI (a flaky/slow
 * public RPC must never gate a merge); run it manually or on a schedule:
 *
 *   pnpm run smoke:testnet
 *   RPC_URL_BSC_TESTNET=https://your-node pnpm run smoke:testnet
 *
 * Exit 0 on success (all assertions pass), 1 on any failure.
 */

import { getAddress } from "viem";
import {
  ERC8183Client,
} from "../../src/erc8183/index.js";
import {
  BSC_TESTNET_CHAIN_ID,
  getAddress as getDeployment,
} from "../../src/networks/index.js";

const NETWORK = "bsc-testnet";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`ASSERTION FAILED: ${msg}`);
}

function line(label: string, value: unknown): void {
  console.log(`  ${label.padEnd(22)} ${String(value)}`);
}

async function main(): Promise<void> {
  console.log("=== BNBAgent TS SDK — read-only testnet smoke ===\n");
  console.log(`network: ${NETWORK} (chainId ${BSC_TESTNET_CHAIN_ID})`);

  // Read-only construction: no walletProvider. create() also asserts the RPC's
  // reported chainId matches the configured network — if that throws, either
  // the RPC is wrong or unreachable.
  const client = await ERC8183Client.create({ network: NETWORK });
  console.log("client constructed (read-only, chainId assertion passed)\n");

  console.log("Commerce kernel:");
  const [jobCounter, paymentToken, feeBp, treasury] = await Promise.all([
    client.commerce.jobCounter(),
    client.paymentToken(),
    client.commerce.platformFeeBp(),
    client.commerce.platformTreasury(),
  ]);
  line("jobCounter", jobCounter);
  line("paymentToken", paymentToken);
  line("platformFeeBp", feeBp);
  line("platformTreasury", treasury);

  console.log("\nPayment token (ERC20):");
  const [decimals, symbol] = await Promise.all([
    client.tokenDecimals(),
    client.tokenSymbol(),
  ]);
  line("decimals", decimals);
  line("symbol", symbol);

  console.log("\nPolicy (OptimisticPolicy):");
  const [disputeWindow, voteQuorum, activeVoters] = await Promise.all([
    client.policy.disputeWindow(),
    client.policy.voteQuorum(),
    client.policy.activeVoterCount(),
  ]);
  line("disputeWindow (s)", disputeWindow);
  line("voteQuorum", voteQuorum);
  line("activeVoterCount", activeVoters);

  console.log("\nRouter (EvaluatorRouter):");
  const [paused, routerCommerce] = await Promise.all([
    client.router.paused(),
    client.router.commerce(),
  ]);
  line("paused", paused);
  line("commerce()", routerCommerce);

  // --- Assertions: decode sanity + registry parity against the real chain ---
  console.log("\nAssertions:");

  assert(typeof jobCounter === "bigint" && jobCounter >= 0n, "jobCounter is a non-negative bigint");
  assert(getAddress(paymentToken) === paymentToken, "paymentToken is a checksummed address");
  assert(typeof decimals === "number" && decimals >= 0 && decimals <= 36, "decimals is a plausible uint8");
  assert(typeof symbol === "string" && symbol.length > 0, "symbol is a non-empty string");
  assert(disputeWindow > 0n, "disputeWindow is positive");
  assert(typeof voteQuorum === "number" && voteQuorum >= 0, "voteQuorum decoded as number");
  assert(typeof paused === "boolean", "paused decoded as boolean");

  // Registry parity: the payment token the deployed commerce kernel reports
  // MUST equal the address hardcoded in networks/addresses.ts, and the router
  // MUST point back at the commerce proxy in the registry.
  const deployment = getDeployment(BSC_TESTNET_CHAIN_ID);
  assert(
    paymentToken === deployment.paymentToken,
    `on-chain paymentToken ${paymentToken} matches registry ${deployment.paymentToken}`,
  );
  assert(
    routerCommerce === deployment.commerceProxy,
    `router.commerce() ${routerCommerce} matches registry commerceProxy ${deployment.commerceProxy}`,
  );
  console.log("  ✓ decode sanity");
  console.log("  ✓ on-chain paymentToken matches registry");
  console.log("  ✓ router.commerce() matches registry commerce proxy");

  // If any jobs exist, decode a real one end-to-end — this exercises the
  // 11-field getJob tuple decode against real on-chain data, the single
  // highest-risk decode path.
  if (jobCounter > 0n) {
    console.log("\nDecoding a real job (getJob tuple decode):");
    const job = await client.getJob(1n);
    line("job.id", job.id);
    line("job.client", job.client);
    line("job.provider", job.provider);
    line("job.status", job.status);
    line("job.budget", job.budget);
    line("job.expiredAt", job.expiredAt);
    line("job.submittedAt", job.submittedAt);
    line("job.deliverable", `${job.deliverable.slice(0, 18)}…`);
    assert(job.id === 1n, "getJob(1) returns job with id 1 (tuple index 0 correct)");
    assert(getAddress(job.client) === job.client, "job.client checksummed (tuple decode aligned)");
    assert(job.deliverable.startsWith("0x") && job.deliverable.length === 66, "deliverable is bytes32 (tuple index 10 correct)");
    console.log("  ✓ getJob tuple decoded with correct field alignment");
  } else {
    console.log("\n(no jobs on chain yet — skipping getJob tuple-decode check)");
  }

  console.log("\n=== ALL SMOKE ASSERTIONS PASSED ===");
}

main().catch((err) => {
  console.error("\n=== SMOKE TEST FAILED ===");
  console.error(err instanceof Error ? `${err.name}: ${err.message}` : String(err));
  process.exitCode = 1;
});
