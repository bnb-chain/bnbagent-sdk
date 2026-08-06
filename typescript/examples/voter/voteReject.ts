/**
 * Cast `voteReject` on a disputed ERC-8183 job.
 *
 * Usage:
 *     pnpm -C typescript exec tsx examples/voter/voteReject.ts <jobId>
 *
 * Performs three pre-flight checks before sending any transaction:
 * 1. Caller is a whitelisted voter.
 * 2. The job has actually been disputed.
 * 3. The caller hasn't already voted.
 *
 * Port of `python/examples/voter/vote_reject.py`. Hits live testnet — run
 * manually, not in CI.
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { ERC8183Client, EVMWalletProvider, loadEnv } from "../../src/index.js";

const ROOT = dirname(fileURLToPath(import.meta.url));

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main(): Promise<number> {
  const jobIdArg = process.argv[2];
  if (!jobIdArg) {
    console.error("Usage: voteReject.ts <jobId>");
    return 2;
  }
  if (!/^\d+$/.test(jobIdArg)) {
    console.error(`jobId must be an integer, got ${JSON.stringify(jobIdArg)}`);
    return 2;
  }
  const jobId = BigInt(jobIdArg);

  loadEnv(ROOT);
  const pk = process.env.VOTER_PRIVATE_KEY;
  if (!pk) {
    console.error("VOTER_PRIVATE_KEY is required");
    return 2;
  }

  const network = process.env.NETWORK ?? "bsc-testnet";
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

  if (!(await client.policy.isVoter(voter))) {
    console.error(
      `${voter} is NOT a whitelisted voter on ${client.policy.address}`,
    );
    return 1;
  }
  if (!(await client.policy.disputed(jobId))) {
    console.error(
      `jobId=${jobId} has not been disputed yet; voteReject would revert`,
    );
    return 1;
  }
  if (await client.policy.hasVoted(jobId, voter)) {
    console.log(`${voter} already voted on jobId=${jobId}`);
    return 0;
  }

  const quorum = await client.policy.voteQuorum();
  const current = await client.policy.rejectVotes(jobId);
  console.log(
    `[voter] casting voteReject on jobId=${jobId} (${current}/${quorum} votes)`,
  );

  const res = await client.voteReject(jobId);
  console.log(`[voter] tx: ${res.transactionHash}`);

  // The RPC node may briefly serve a stale block — retry until the vote shows up.
  let newTotal = current;
  for (let i = 0; i < 8; i++) {
    newTotal = await client.policy.rejectVotes(jobId);
    if (newTotal > current) {
      break;
    }
    await sleep(2_000);
  }

  if (newTotal >= quorum) {
    console.log(
      `[voter] quorum reached (${newTotal}/${quorum}); any settler can now call router.settle(${jobId})`,
    );
  } else {
    console.log(
      `[voter] current reject votes: ${newTotal}/${quorum} — still below quorum`,
    );
  }
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
