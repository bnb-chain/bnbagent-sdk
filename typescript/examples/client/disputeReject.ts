/**
 * Flow B — dispute + quorum reject.
 *
 * createJob -> register -> setBudget -> fund -> submit -> client disputes ->
 * whitelisted voter(s) voteReject (quorum met) -> settle -> REJECTED.
 *
 * Client gets a refund; provider keeps nothing.
 *
 * Port of `python/examples/client/dispute_reject.py`. Hits live testnet —
 * run manually, not in CI.
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
} from "./_helpers.js";

async function main(): Promise<void> {
  const s = loadSettings();
  const client = await makePrimaryClient(s);

  banner("DISPUTE REJECT — client disputes, voter rejects");

  const decimals = await client.tokenDecimals();
  const budget = 1n * 10n ** BigInt(decimals);
  const expiredAt = await expiryFor(client);

  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: dispute-reject",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId}`);

  await client.registerJob(jobId);
  await client.setBudget(jobId, budget);
  await client.fund(jobId, budget);
  console.log("[client] registered + funded");

  if (!s.providerPk) {
    console.log(`\nProvider must submit jobId=${jobId} before continuing.\n`);
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
      content: `dispute test result for job ${jobId}`,
      contentType: "text/plain",
    },
  });
  // In production: upload manifest.toDict() to IPFS/storage first, then pass the URL.
  const deliverableUrl = "https://example.invalid/manifest.json"; // placeholder — these scripts test on-chain flow only
  await provider.submit(jobId, manifest.manifestHash(), {
    deliverable_url: deliverableUrl,
  });
  console.log("[provider] submit OK");

  await client.dispute(jobId);
  console.log("[client] dispute raised");

  if (!s.voterPk) {
    const quorum = await client.policy.voteQuorum();
    console.log(
      `\nDispute raised on jobId=${jobId}. Need ${quorum} voteReject(s) from whitelisted voters, then anyone can call settle. See examples/voter/.\n`,
    );
    return;
  }

  const voter = await makeClient(s.voterPk, s.network);
  await voter.voteReject(jobId);
  console.log("[voter] voteReject cast");

  await client.settle(jobId);
  const job = await client.getJob(jobId);
  if (job.status !== JobStatus.REJECTED) {
    throw new Error(`expected REJECTED, got ${JobStatus[job.status]}`);
  }
  console.log(`[client] settle OK -> ${JobStatus[job.status]}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
