/**
 * Voter watch loop — polls for disputed jobs, fetches the IPFS manifest,
 * prompts for a vote.
 *
 * For each newly disputed job:
 *   1. Reads `deliverable_url` via `client.getDeliverableUrl` (parses the
 *      on-chain `JobInitialised` `optParams`).
 *   2. Downloads the `DeliverableManifest` from IPFS/HTTP.
 *   3. Verifies the manifest hash against the on-chain `job.deliverable`.
 *   4. Prints the response content for review.
 *   5. Prompts: [r]eject / [s]kip.
 *
 * Once `rejectVotes >= voteQuorum` for a disputed job, settles automatically
 * and prints the result.
 *
 * Adaptation note: the Python reference (`python/examples/voter/watch.py`)
 * subscribes directly to the `OptimisticPolicy` contract's `Disputed` /
 * `VoteCast` event logs via `erc8183.policy.contract.events...get_logs()`.
 * `PolicyClient`'s public surface in this SDK does not expose raw event-log
 * reads (`readEvents` is a `ContractBase`-internal seam used only by the
 * typed event helpers it already exposes), so this port polls job state
 * directly instead: `commerce.jobCounter()` discovers jobs created during the
 * run, then `policy.disputed()` is re-polled for each still-undisputed job on
 * EVERY tick (a dispute is raised long after creation, so a job cannot be
 * checked just once), and `policy.rejectVotes()` tracks quorum. Two behavioral
 * differences from the event-log version: it starts from the current job
 * counter (like the Python reference starting at the current block head), so a
 * dispute on a job created before startup is not caught; and detection lags by
 * up to one poll interval rather than firing on the event.
 *
 * Usage:
 *     cd typescript/examples/voter
 *     pnpm -C ../.. exec tsx watch.ts
 *
 * Port of `python/examples/voter/watch.py`. Hits live testnet — run
 * manually, not in CI.
 */

import { dirname } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";
import { DeliverableManifest } from "../../src/erc8183/index.js";
import { ERC8183Client, EVMWalletProvider, loadEnv } from "../../src/index.js";

const ROOT = dirname(fileURLToPath(import.meta.url));
const POLL_INTERVAL_MS = 12_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchManifest(
  deliverableUrl: string,
  gatewayUrl: string,
): Promise<DeliverableManifest | null> {
  try {
    const url = deliverableUrl.startsWith("ipfs://")
      ? `${gatewayUrl.replace(/\/+$/, "")}/${deliverableUrl.slice("ipfs://".length)}`
      : deliverableUrl;
    const res = await fetch(url, { signal: AbortSignal.timeout(15_000) });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = (await res.json()) as Record<string, unknown>;
    return DeliverableManifest.fromDict(data);
  } catch (error) {
    console.log(
      `  [warn] could not fetch manifest: ${(error as Error).message}`,
    );
    return null;
  }
}

async function handleQuorumReached(
  client: ERC8183Client,
  jobId: number,
  rejectVotes: number,
  quorum: number,
): Promise<void> {
  console.log(`\n${"=".repeat(60)}`);
  console.log(
    `  QUORUM REACHED job_id=${jobId}  (${rejectVotes}/${quorum} reject votes)`,
  );
  console.log("=".repeat(60));
  console.log(`  settling job ${jobId}...`);
  try {
    await client.settle(BigInt(jobId));
    console.log(`  settle(${jobId}) success (OK)`);
  } catch (error) {
    console.error(`  [error] settle failed: ${(error as Error).message}`);
  }
}

async function handleDisputedJob(
  client: ERC8183Client,
  jobId: number,
  voter: string,
  gatewayUrl: string,
  rl: ReturnType<typeof createInterface>,
): Promise<void> {
  console.log(`\n${"=".repeat(60)}`);
  console.log(`  DISPUTED job_id=${jobId}`);
  console.log("=".repeat(60));

  const alreadyVoted = await client.policy.hasVoted(BigInt(jobId), voter);
  if (alreadyVoted) {
    console.log("  Already voted on this job — skipping.");
    return;
  }

  const job = await client.getJob(BigInt(jobId));
  console.log(`  client   : ${job.client}`);
  console.log(`  provider : ${job.provider}`);
  console.log(`  budget   : ${job.budget}`);
  console.log(`  status   : ${job.status}`);

  const deliverableUrl = await client.getDeliverableUrl(BigInt(jobId));
  if (deliverableUrl) {
    console.log(`  IPFS URL : ${deliverableUrl}`);
  } else {
    console.log("  [warn] no deliverable_url found on-chain");
  }

  let manifest: DeliverableManifest | null = null;
  if (deliverableUrl) {
    manifest = await fetchManifest(deliverableUrl, gatewayUrl);
  }

  if (manifest) {
    const hashOk = manifest.verify(job.deliverable);
    console.log(`  hash ok  : ${hashOk ? "yes" : "NO — MISMATCH"}`);
    console.log(`\n--- Deliverable content (job_id=${manifest.jobId}) ---`);
    console.log(manifest.response.content);
    console.log("---");
  } else {
    console.log("  (no manifest available for review)");
  }

  const choice = (await rl.question("\n  [r]eject  [s]kip  > "))
    .trim()
    .toLowerCase();
  if (choice === "r") {
    console.log(`  casting voteReject(${jobId})...`);
    await client.voteReject(BigInt(jobId));
    console.log(`  voteReject(${jobId}) submitted`);
    console.log("  (polling rejectVotes to check quorum...)");
  } else {
    console.log(`  skipped job ${jobId}`);
  }
}

async function main(): Promise<void> {
  loadEnv(ROOT);

  const pk = process.env.VOTER_PRIVATE_KEY;
  if (!pk) {
    throw new Error("VOTER_PRIVATE_KEY is required in .env");
  }

  const network = process.env.NETWORK ?? "bsc-testnet";
  const gateway =
    process.env.STORAGE_GATEWAY_URL ?? "https://gateway.pinata.cloud/ipfs/";

  const wallet = new EVMWalletProvider({
    password: "example",
    privateKey: pk,
    persist: false,
  });
  const client = await ERC8183Client.create({
    walletProvider: wallet,
    network,
  });
  const voter = client.address;
  if (!voter) {
    throw new Error("voter address unavailable");
  }

  const quorum = await client.policy.voteQuorum();

  console.log("Voter watch loop");
  console.log(`  network  : ${client.network.name}`);
  console.log(`  rpc      : ${client.network.rpcUrl}`);
  console.log(`  policy   : ${client.policy.address}`);
  console.log(`  voter    : ${voter}`);
  console.log(`  listed   : ${await client.policy.isVoter(voter)}`);
  console.log(`  quorum   : ${quorum}`);
  console.log(`  gateway  : ${gateway}`);
  console.log(
    "\nWatching for disputed jobs / vote quorum (Ctrl+C to stop)...\n",
  );

  const rl = createInterface({ input: process.stdin, output: process.stdout });

  const seenDisputed = new Set<number>();
  const settled = new Set<number>();
  // Jobs discovered but not yet observed disputed — re-polled EVERY tick, not
  // just the tick they first appear. A dispute is raised long after a job is
  // created (create -> fund -> submit -> dispute), so a job scanned once at
  // creation is `disputed=false`; we must keep polling it until it flips.
  const watching = new Set<number>();
  // Start from the current job counter so we only track jobs created during
  // this run (mirrors the Python reference starting at the current block
  // head) — this also bounds `watching` to new jobs instead of replaying all
  // historical ones. A dispute on a job created before startup is not caught.
  let lastCounter = await client.commerce.jobCounter();

  try {
    while (true) {
      const counter = await client.commerce.jobCounter();
      if (counter > lastCounter) {
        for (let id = lastCounter + 1n; id <= counter; id++) {
          watching.add(Number(id));
        }
        lastCounter = counter;
      }

      for (const jobId of [...watching]) {
        const isDisputed = await client.policy.disputed(BigInt(jobId));
        if (isDisputed) {
          watching.delete(jobId);
          if (!seenDisputed.has(jobId)) {
            const ts = new Date().toLocaleTimeString();
            console.log(`[${ts}] Disputed — jobId=${jobId}`);
            seenDisputed.add(jobId);
            await handleDisputedJob(client, jobId, voter, gateway, rl);
          }
        }
      }

      for (const jobId of seenDisputed) {
        if (settled.has(jobId)) {
          continue;
        }
        const rejectVotes = await client.policy.rejectVotes(BigInt(jobId));
        if (rejectVotes > 0) {
          const ts = new Date().toLocaleTimeString();
          console.log(
            `[${ts}] rejectVotes=${rejectVotes}/${quorum} for jobId=${jobId}`,
          );
        }
        if (rejectVotes >= quorum) {
          settled.add(jobId);
          await handleQuorumReached(client, jobId, rejectVotes, quorum);
        }
      }

      await sleep(POLL_INTERVAL_MS);
    }
  } finally {
    rl.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
