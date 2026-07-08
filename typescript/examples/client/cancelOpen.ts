/**
 * Flow E — client cancels before funding.
 *
 * createJob -> (no setBudget / no fund) -> client `cancelOpen` -> REJECTED.
 * No escrow ever moved, no provider action needed.
 *
 * Port of `python/examples/client/cancel_open.py`. Hits live testnet — run
 * manually, not in CI.
 */

import { JobStatus } from "../../src/erc8183/index.js";
import {
  banner,
  expiryFor,
  loadSettings,
  makePrimaryClient,
} from "./_helpers.js";

async function main(): Promise<void> {
  const s = loadSettings();
  const client = await makePrimaryClient(s);

  banner("CANCEL OPEN — client cancels before funding");

  const expiredAt = await expiryFor(client, 1);
  const res = await client.createJob({
    provider: s.providerAddress,
    expiredAt,
    description: "ERC-8183 demo: cancel-open",
  });
  const jobId = res.jobId;
  if (jobId === null) {
    throw new Error("createJob did not return a jobId");
  }
  console.log(`[client] createJob jobId=${jobId}`);

  await client.registerJob(jobId);
  console.log("[client] registerJob (optional, shown for completeness)");

  await client.cancelOpen(jobId);
  const job = await client.getJob(jobId);
  if (job.status !== JobStatus.REJECTED) {
    throw new Error(`expected REJECTED, got ${JobStatus[job.status]}`);
  }
  console.log(
    `[client] cancel OK -> ${JobStatus[job.status]} (no escrow moved)`,
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
