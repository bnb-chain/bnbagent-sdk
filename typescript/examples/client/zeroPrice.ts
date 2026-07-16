/**
 * Flow F — seller-side zero price.
 *
 * A provider offers a job for free (`budget == 0`). The escrow state machine
 * is unchanged (Open -> Funded -> Submitted -> Completed), but no tokens ever
 * move: `fund(0)` deposits nothing (and the SDK skips the ERC-20 approve
 * entirely), and `settle` pays nobody.
 *
 * createJob -> registerJob -> PROVIDER setBudget(0) -> fund(0) -> submit ->
 * wait past dispute window -> settle -> COMPLETED, with the provider's token
 * balance unchanged.
 *
 * Zero price is **seller-side**: only the provider may set `budget == 0`
 * (the kernel reverts a client-initiated zero budget with
 * `ZeroBudgetSellerOnly`). This demo therefore REQUIRES
 * `PROVIDER_PRIVATE_KEY`. Port of `python/examples/client/zero_price.py`.
 * Hits live testnet — run manually, not in CI.
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
  if (!s.providerPk) {
    throw new Error(
      "PROVIDER_PRIVATE_KEY is required for the zero-price flow: only the " +
        "provider (seller) may set budget == 0.",
    );
  }
  const client = await makePrimaryClient(s);
  const provider = await makeClient(s.providerPk, s.network);

  banner("ZERO-PRICE — seller offers a free job, nobody is paid");

  const expiredAt = await expiryFor(client); // disputeWindow + 10 min slack
  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: zero-price",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId}`);

  await client.registerJob(jobId);
  console.log("[client] registerJob -> OptimisticPolicy");

  // Seller-side: the PROVIDER sets the zero budget. A client-initiated
  // setBudget(0) would revert with ZeroBudgetSellerOnly.
  await provider.setBudget(jobId, 0n);
  console.log("[provider] setBudget 0 (seller-side zero price)");

  const providerBalanceBefore = await client.tokenBalance(s.providerAddress);

  // fund(0) moves no tokens and needs no allowance — the SDK detects
  // amount == 0 and skips the ERC-20 approve entirely.
  await client.fund(jobId, 0n);
  console.log("[client] fund 0 OK (Open -> Funded, no transfer, no approve)");

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
      content: `zero-price result for job ${jobId}`,
      contentType: "text/plain",
    },
  });
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

  const providerBalanceAfter = await client.tokenBalance(s.providerAddress);
  if (providerBalanceAfter !== providerBalanceBefore) {
    throw new Error(
      `expected provider balance unchanged for a free job, ` +
        `before=${providerBalanceBefore} after=${providerBalanceAfter}`,
    );
  }
  console.log(
    `[client] provider balance unchanged (${providerBalanceAfter}) — nobody was paid`,
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
