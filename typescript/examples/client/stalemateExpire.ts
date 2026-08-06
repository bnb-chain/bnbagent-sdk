/**
 * Flow C — stalemate -> expiry refund.
 *
 * Client disputes but quorum is never reached. Settlement is impossible
 * because the policy stays PENDING. Once the job passes its `expiredAt`
 * deadline, the client (or anyone) calls `claimRefund` — the universal
 * escape hatch on the kernel.
 *
 * NOTE: `expiredAt` must accommodate the policy's dispute window because the
 * provider has to submit before `expiredAt - disputeWindow`. The script's
 * wall-clock wait scales with that window, so on a network configured with a
 * multi-hour window this demo is slow by design.
 *
 * Port of `python/examples/client/stalemate_expire.py`. Hits live testnet —
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
  sleep,
} from "./_helpers.js";

async function main(): Promise<void> {
  const s = loadSettings();
  const client = await makePrimaryClient(s);

  banner("STALEMATE — dispute without quorum, refund at expiry");

  const decimals = await client.tokenDecimals();
  const budget = 1n * 10n ** BigInt(decimals);

  // Smallest expiry that still admits a valid submit: disputeWindow + 1 min.
  const expiredAt = await expiryFor(client, 1);

  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: stalemate",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId} expiredAt=${expiredAt}`);
  await client.registerJob(jobId);
  await client.setBudget(jobId, budget);
  await client.fund(jobId, budget);

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
      content: `stalemate test result for job ${jobId}`,
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
  console.log("[client] dispute raised (no voter will act)");

  const wait = Number(expiredAt) - Math.floor(Date.now() / 1000) + 3;
  if (wait > 0) {
    console.log(`[client] waiting ${wait}s for expiry...`);
    await sleep(wait * 1000);
  }

  await client.claimRefund(jobId);
  const job = await client.getJob(jobId);
  if (job.status !== JobStatus.EXPIRED) {
    throw new Error(`expected EXPIRED, got ${JobStatus[job.status]}`);
  }
  console.log(`[client] claimRefund OK -> ${JobStatus[job.status]}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
