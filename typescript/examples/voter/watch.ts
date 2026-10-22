/**
 * Voter watch loop — polls for disputed jobs, fetches the IPFS manifest,
 * prompts for a vote.
 *
 * For each newly disputed job:
 *   1. Reads `deliverable_url` via `client.getDeliverableUrl` (parses the
 *      on-chain `JobInitialised` `optParams`, found from the job's
 *      `submittedAt`).
 *   2. Downloads the `DeliverableManifest` from IPFS/HTTP.
 *   3. Verifies the manifest hash against the on-chain `job.deliverable`.
 *   4. Prints the response content for review.
 *   5. Prompts: [r]eject / [s]kip.
 *
 * Under OptimisticPolicy silence past the voting window
 * (`submittedAt + disputeWindow`) approves the job, so a disputed job is always
 * put to a vote before the window closes:
 *   - deliverable reviewable: prompted with its content;
 *   - deliverable unavailable (the provider published no usable
 *     `deliverable_url`, an unsupported URL, a malformed manifest, or a
 *     download failing `MANIFEST_DOWNLOAD_ATTEMPTS` ticks in a row): prompted
 *     with a warning;
 *   - deliverable not verified (the chain could not be read to look it up):
 *     retried, and prompted with a warning once the window is within the
 *     decision margin (`VOTER_DECISION_MARGIN_SECONDS`, default 30 minutes, at
 *     most half the window).
 * Text from the deliverable is printed escaped (see {@link printable}).
 * Other RPC errors are retried every tick. A failure on one job never stops
 * the loop. The RPC must serve `eth_getLogs` for blocks as old as the voting
 * window (the default bsc-mainnet RPC does not); this is checked at startup.
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
import { fileURLToPath, pathToFileURL } from "node:url";
import { DeliverableManifest } from "../../src/erc8183/index.js";
import { ERC8183Client, EVMWalletProvider, loadEnv } from "../../src/index.js";
import {
  fetchPublicJson,
  publicGatewayUrl,
} from "../../src/utils/publicHttp.js";

const ROOT = dirname(fileURLToPath(import.meta.url));
const POLL_INTERVAL_MS = 12_000;
/**
 * Ticks a failing download is retried before the voter is asked to vote on an
 * unavailable deliverable: the provider controls whether it can be fetched.
 */
export const MANIFEST_DOWNLOAD_ATTEMPTS = 3;
const MANIFEST_TIMEOUT_MS = 15_000; // for a whole download, body included
/** A manifest carries up to MAX_RESPONSE_BYTES (5 MiB by default) of response. */
export const MANIFEST_MAX_BYTES = 8 * 1024 * 1024;
export const DEFAULT_DECISION_MARGIN_SECONDS = 30 * 60;
const FETCHABLE_SCHEMES = ["ipfs://", "https://", "http://"];

type Prompt = Pick<ReturnType<typeof createInterface>, "question">;

/** The deliverable could not be downloaded this time; retry on the next tick. */
export class ManifestUnavailableError extends Error {
  override name = "ManifestUnavailableError";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * `value` as text a terminal shows as is. Control, format and surrogate
 * characters are escaped: from the provider's deliverable they could rewrite
 * lines already printed, such as the hash check.
 */
export function printable(value: unknown): string {
  const text =
    typeof value === "string"
      ? value
      : (JSON.stringify(value) ?? String(value));
  return text.replace(/[\p{C}\p{Z}]/gu, (c) => {
    if (c === " " || c === "\n" || c === "\t") {
      return c;
    }
    const code = c.codePointAt(0) ?? 0;
    const hex = code.toString(16);
    return code <= 0xff
      ? `\\x${hex.padStart(2, "0")}`
      : code <= 0xffff
        ? `\\u${hex.padStart(4, "0")}`
        : `\\u{${hex}}`;
  });
}

/**
 * Seconds before the voting window closes from which a disputed job is put to
 * a vote whatever is still unresolved: `VOTER_DECISION_MARGIN_SECONDS`
 * (default 30 minutes), at most half the window.
 */
export function decisionMargin(windowSeconds: number): number {
  const raw = process.env.VOTER_DECISION_MARGIN_SECONDS?.trim();
  const configured = raw ? Number(raw) : DEFAULT_DECISION_MARGIN_SECONDS;
  if (!Number.isFinite(configured) || configured < 0) {
    throw new Error(
      `VOTER_DECISION_MARGIN_SECONDS must be a non-negative number of seconds, got ${JSON.stringify(raw)}`,
    );
  }
  return Math.min(configured, windowSeconds / 2);
}

/**
 * Download and parse a `DeliverableManifest`; `null` if it is malformed
 * (including a hash that cannot be computed, e.g. a non-finite number).
 * The download goes through {@link fetchPublicJson}: public addresses only,
 * no redirects, at most {@link MANIFEST_MAX_BYTES} within 15 s. Throws
 * {@link ManifestUnavailableError} when it cannot be downloaded, for any
 * reason: the provider controls the URL and the server.
 */
export async function fetchManifest(
  deliverableUrl: string,
  gatewayUrl: string,
): Promise<DeliverableManifest | null> {
  let data: Record<string, unknown> | null;
  try {
    data = await fetchPublicJson(publicGatewayUrl(deliverableUrl, gatewayUrl), {
      timeoutMs: MANIFEST_TIMEOUT_MS,
      maxBytes: MANIFEST_MAX_BYTES,
    });
  } catch (error) {
    throw new ManifestUnavailableError(
      `could not fetch manifest: ${messageOf(error)}`,
      { cause: error },
    );
  }
  if (data === null) {
    throw new ManifestUnavailableError(
      "could not fetch manifest: the public download was refused or failed",
    );
  }
  try {
    const manifest = DeliverableManifest.fromDict(data);
    manifest.manifestHash();
    return manifest;
  } catch (error) {
    console.log(`  [warn] malformed manifest: ${printable(messageOf(error))}`);
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

/**
 * Show job details and prompt to reject or skip.
 *
 * The deliverable is reviewable, unavailable or not verified (see the module
 * comment). Without prompting, a failed download throws
 * {@link ManifestUnavailableError} until it has failed
 * {@link MANIFEST_DOWNLOAD_ATTEMPTS} times (counted by the caller in
 * `downloadFailures`), and a failed deliverable lookup propagates; both stop
 * being retried once the window is within {@link decisionMargin}. Other chain
 * reads propagate and are retried.
 */
export async function handleDisputedJob(
  client: ERC8183Client,
  jobId: number,
  voter: string,
  gatewayUrl: string,
  rl: Prompt,
  downloadFailures = 0,
): Promise<void> {
  if (await client.policy.hasVoted(BigInt(jobId), voter)) {
    console.log(`  job ${jobId}: already voted — skipping.`);
    return;
  }

  const job = await client.getJob(BigInt(jobId));
  const window = Number(await client.policy.disputeWindow());
  // voteReject reverts from submittedAt + disputeWindow on
  const windowLeft = Number(job.submittedAt) + window - Date.now() / 1000;
  if (windowLeft <= 0) {
    console.log(`  job ${jobId}: voting window closed — skipping.`);
    return;
  }
  const deciding = windowLeft <= decisionMargin(window);

  let deliverableUrl: string | null = null;
  let manifest: DeliverableManifest | null = null;
  let unavailable: string | null = null; // the provider made nothing reviewable
  let unverified: string | null = null; // the chain could not be read to look it up
  try {
    // The SDK locates the submission block from job.submittedAt, however long
    // ago it was, and throws (never returns null) when it cannot read it.
    deliverableUrl = await client.getDeliverableUrl(BigInt(jobId));
  } catch (error) {
    if (!deciding) {
      throw error;
    }
    unverified = `could not look the deliverable up on-chain (${messageOf(error)})`;
  }
  if (unverified === null) {
    if (typeof deliverableUrl !== "string" || !deliverableUrl) {
      deliverableUrl = null;
      unavailable = "the provider published no usable deliverable_url on-chain";
    } else if (!FETCHABLE_SCHEMES.some((s) => deliverableUrl?.startsWith(s))) {
      unavailable = `unsupported deliverable URL ${JSON.stringify(deliverableUrl)}`;
    } else {
      try {
        manifest = await fetchManifest(deliverableUrl, gatewayUrl);
      } catch (error) {
        if (
          !(error instanceof ManifestUnavailableError) ||
          (downloadFailures + 1 < MANIFEST_DOWNLOAD_ATTEMPTS && !deciding)
        ) {
          throw error;
        }
        unavailable = error.message;
      }
    }
  }

  console.log(`\n${"=".repeat(60)}`);
  console.log(`  DISPUTED job_id=${jobId}`);
  console.log("=".repeat(60));
  console.log(`  client   : ${job.client}`);
  console.log(`  provider : ${job.provider}`);
  console.log(`  budget   : ${job.budget}`);
  console.log(`  status   : ${job.status}`);
  console.log(`  IPFS URL : ${printable(deliverableUrl || "-")}`);

  if (manifest) {
    const hashOk = manifest.verify(job.deliverable);
    console.log(`  hash ok  : ${hashOk ? "yes" : "NO — MISMATCH"}`);
    console.log(`\n--- Deliverable content (job_id=${manifest.jobId}) ---`);
    console.log(printable(manifest.response.content));
    console.log("---");
  } else if (unverified) {
    console.log(`  [!] deliverable not verified: ${printable(unverified)}`);
    console.log(
      `      the voting window closes in ${Math.round(windowLeft / 60)} min; check the deliverable another way (e.g. another RPC) before voting`,
    );
  } else if (unavailable) {
    console.log(`  [!] deliverable unavailable: ${printable(unavailable)}`);
    console.log(
      "      there is nothing to review; the provider controls whether it can be fetched",
    );
  } else {
    console.log(
      "  (the manifest is malformed: there is no valid deliverable to review)",
    );
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

/** A block about `seconds` before `head`, from the recent block time. */
async function blockSecondsAgo(
  client: ERC8183Client,
  head: bigint,
  seconds: number,
): Promise<bigint> {
  const sample = head < 1_000n ? head : 1_000n;
  if (sample === 0n) {
    return 0n;
  }
  const [latest, earlier] = await Promise.all([
    client.publicClient.getBlock({ blockNumber: head }),
    client.publicClient.getBlock({ blockNumber: head - sample }),
  ]);
  const elapsed = Number(latest.timestamp - earlier.timestamp);
  if (elapsed <= 0) {
    return 0n;
  }
  const back = BigInt(Math.floor((seconds * Number(sample)) / elapsed));
  return head > back ? head - back : 0n;
}

/**
 * Throw unless the RPC serves `eth_getLogs` for recent blocks and for blocks
 * as old as the voting window: a deliverable is looked up at its submission.
 */
export async function requireLogRpc(client: ERC8183Client): Promise<void> {
  const head = await client.publicClient.getBlockNumber();
  const rpc = client.network.rpcUrl;
  try {
    await client.publicClient.getLogs({
      address: client.policy.address,
      fromBlock: head > 10n ? head - 10n : 0n,
      toBlock: head,
    });
  } catch (error) {
    throw new Error(
      `the RPC ${rpc} does not serve eth_getLogs (${messageOf(error)}); the voter reads JobInitialised logs to find each deliverable. Set RPC_URL to an endpoint that does.`,
      { cause: error },
    );
  }
  const window = Number(await client.policy.disputeWindow());
  const old = await blockSecondsAgo(client, head, window);
  try {
    await client.publicClient.getLogs({
      address: client.policy.address,
      fromBlock: old,
      toBlock: old + 10n,
    });
  } catch (error) {
    throw new Error(
      `the RPC ${rpc} does not serve eth_getLogs for blocks as old as the voting window (${(window / 3600).toFixed(1)} h, around block ${old}): ${messageOf(error)}. A disputed job's deliverable is read from its submission, up to that long ago. Set RPC_URL to an archive endpoint.`,
      { cause: error },
    );
  }
}

/**
 * One tick over the watched jobs. A disputed job leaves `watching` only once
 * `handle` succeeded, so a failure is retried on the next tick and never stops
 * the loop. `downloadFailures` counts each job's failed manifest downloads
 * across ticks and is passed to `handle`.
 */
export async function pollDisputedJobs(
  client: ERC8183Client,
  watching: Set<number>,
  seenDisputed: Set<number>,
  handle: (jobId: number, downloadFailures: number) => Promise<void>,
  downloadFailures: Map<number, number> = new Map(),
): Promise<void> {
  for (const jobId of [...watching]) {
    try {
      if (!(await client.policy.disputed(BigInt(jobId)))) {
        continue;
      }
      if (!seenDisputed.has(jobId)) {
        const ts = new Date().toLocaleTimeString();
        console.log(`[${ts}] Disputed — jobId=${jobId}`);
        await handle(jobId, downloadFailures.get(jobId) ?? 0);
        seenDisputed.add(jobId);
      }
      watching.delete(jobId);
      downloadFailures.delete(jobId);
    } catch (error) {
      if (error instanceof ManifestUnavailableError) {
        downloadFailures.set(jobId, (downloadFailures.get(jobId) ?? 0) + 1);
      }
      console.log(
        `  [warn] disputed job ${jobId} not handled (${printable(messageOf(error))}); retrying next tick`,
      );
    }
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
  const margin = decisionMargin(Number(await client.policy.disputeWindow()));
  await requireLogRpc(client);

  const quorum = await client.policy.voteQuorum();

  console.log("Voter watch loop");
  console.log(`  network  : ${client.network.name}`);
  console.log(`  rpc      : ${client.network.rpcUrl}`);
  console.log(`  policy   : ${client.policy.address}`);
  console.log(`  voter    : ${voter}`);
  console.log(`  listed   : ${await client.policy.isVoter(voter)}`);
  console.log(`  quorum   : ${quorum}`);
  console.log(`  gateway  : ${gateway}`);
  console.log(`  margin   : ${margin} s before a voting window closes`);
  console.log(
    "\nWatching for disputed jobs / vote quorum (Ctrl+C to stop)...\n",
  );

  const rl = createInterface({ input: process.stdin, output: process.stdout });

  const seenDisputed = new Set<number>();
  const downloadFailures = new Map<number, number>();
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
      try {
        const counter = await client.commerce.jobCounter();
        if (counter > lastCounter) {
          for (let id = lastCounter + 1n; id <= counter; id++) {
            watching.add(Number(id));
          }
          lastCounter = counter;
        }

        await pollDisputedJobs(
          client,
          watching,
          seenDisputed,
          (jobId, failures) =>
            handleDisputedJob(client, jobId, voter, gateway, rl, failures),
          downloadFailures,
        );

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
      } catch (error) {
        console.log(
          `  [warn] poll failed (${printable(messageOf(error))}); retrying`,
        );
      }

      await sleep(POLL_INTERVAL_MS);
    }
  } finally {
    rl.close();
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
