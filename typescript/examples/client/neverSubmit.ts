/**
 * Flow D — provider never submits, client reclaims via expiry.
 *
 * createJob -> register -> setBudget -> fund -> (provider silent) -> wait
 * past `expiredAt` -> `claimRefund` -> EXPIRED.
 *
 * Wall-clock wait scales with the contract's `disputeWindow` because
 * `expiredAt` must be at least `disputeWindow` in the future for Commerce to
 * accept the job.
 *
 * Port of `python/examples/client/never_submit.py`. Hits live testnet — run
 * manually, not in CI.
 */

import { JobStatus } from "../../src/erc8183/index.js";
import {
  banner,
  expiryFor,
  loadSettings,
  makePrimaryClient,
  sleep,
} from "./_helpers.js";

async function main(): Promise<void> {
  const s = loadSettings();
  const client = await makePrimaryClient(s);

  banner("NEVER SUBMIT — provider silent, refund at expiry");

  const decimals = await client.tokenDecimals();
  const budget = 1n * 10n ** BigInt(decimals);
  const expiredAt = await expiryFor(client, 1);

  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: never-submit",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId} expiredAt=${expiredAt}`);
  await client.registerJob(jobId);
  await client.setBudget(jobId, budget);
  await client.fund(jobId, budget);

  const wait = Number(expiredAt) - Math.floor(Date.now() / 1000) + 3;
  console.log(`[client] waiting ${wait}s for expiry (provider is silent)...`);
  if (wait > 0) {
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
