/**
 * Flow A — happy path.
 *
 * createJob -> registerJob -> setBudget -> fund -> submit -> wait past
 * dispute window -> settle -> COMPLETED.
 *
 * Port of `python/examples/client/happy.py`. Hits live testnet — run
 * manually, not in CI.
 */

import {
  DeliverableManifest,
  JobStatus,
  SCHEMA_VERSION,
} from "../../src/erc8183/index.js";
import {
  banner,
  expiryFor,
  loadSettings,
  makeClient,
  makePrimaryClient,
  sleep,
} from "./_helpers.js";

async function main(): Promise<void> {
  const s = loadSettings();
  const client = await makePrimaryClient(s);

  banner("HAPPY — create + fund + submit + settle");

  const decimals = await client.tokenDecimals();
  const budget = 1n * 10n ** BigInt(decimals); // 1 token

  const expiredAt = await expiryFor(client); // disputeWindow + 10 min slack
  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: happy",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId}`);

  await client.registerJob(jobId);
  console.log("[client] registerJob -> OptimisticPolicy");

  await client.setBudget(jobId, budget);
  const symbol = await client.tokenSymbol();
  console.log(
    `[client] setBudget ${Number(budget) / 10 ** decimals} ${symbol}`,
  );

  await client.fund(jobId, budget);
  console.log("[client] fund OK (Open -> Funded)");

  if (!s.providerPk) {
    console.log(
      `\nNo PROVIDER_PRIVATE_KEY set. Ask the provider to submit jobId=${jobId}, then rerun with --resume.\n`,
    );
    return;
  }

  const provider = await makeClient(s.providerPk, s.network);
  const manifest = new DeliverableManifest({
    version: SCHEMA_VERSION,
    jobId: Number(jobId),
    chainId: provider.network.chainId,
    contracts: {
      commerce: provider.commerce.address,
      router: provider.router.address,
      policy: provider.policy.address,
    },
    response: {
      content: `happy path result for job ${jobId}`,
      contentType: "text/plain",
    },
  });
  // In production: upload manifest.toDict() to IPFS/storage first, then pass
  // the URL. deliverableUrl = await storage.upload(manifest.toDict(), `job-${jobId}.json`);
  const deliverableUrl = "https://example.invalid/manifest.json"; // placeholder — these scripts test on-chain flow only
  await provider.submit(jobId, manifest.manifestHash(), {
    deliverable_url: deliverableUrl,
  });
  console.log("[provider] submit OK (Funded -> Submitted)");

  const window = await client.policy.disputeWindow();
  console.log(`[client] waiting ${window}s for dispute window to pass...`);
  await sleep(Number(window) * 1000 + 2_000);

  await client.settle(jobId);
  const job = await client.getJob(jobId);
  if (job.status !== JobStatus.COMPLETED) {
    throw new Error(`expected COMPLETED, got ${JobStatus[job.status]}`);
  }
  console.log(`[client] settle OK -> ${JobStatus[job.status]}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
