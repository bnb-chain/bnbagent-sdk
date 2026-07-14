/**
 * Operator-side settle for a SUBMITTED ERC-8183 job — v1 helper.
 *
 * `router.settle(jobId)` is permissionless: any wallet can finalise a
 * SUBMITTED job and pay the gas. The agent server does not auto-settle, so the
 * typical operator action after the dispute window elapses without dispute
 * (verdict = APPROVE) is to run this once per job.
 *
 * TypeScript port of `python/examples/agent-server/scripts/settle.py`.
 *
 * Run:
 *   pnpm -C typescript exec tsx examples/agent-server/scripts/settle.ts <jobId>
 *
 * Pre-flight (no transaction sent unless all pass):
 *   1. Job is SUBMITTED (not OPEN / FUNDED / already-settled).
 *   2. Policy verdict is APPROVE or REJECT (PENDING ⇒ wait, then retry).
 * If the loaded wallet is not job.provider the script still proceeds (settle
 * is permissionless) but warns.
 */

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ERC8183Client,
  JobStatus,
  Verdict,
} from "../../../src/erc8183/index.js";
import { EVMWalletProvider, loadEnv } from "../../../src/index.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // examples/agent-server
loadEnv(ROOT);

async function main(): Promise<number> {
  const jobIdArg = process.argv[2];
  if (!jobIdArg || !/^\d+$/.test(jobIdArg)) {
    console.error("usage: settle.ts <jobId>");
    return 2;
  }
  const jobId = BigInt(jobIdArg);

  const walletPassword = process.env.WALLET_PASSWORD;
  if (!walletPassword) {
    console.error("WALLET_PASSWORD is required (set it in .env)");
    return 2;
  }
  const wallet = new EVMWalletProvider({
    password: walletPassword,
    privateKey: process.env.PRIVATE_KEY,
  });
  const erc8183 = await ERC8183Client.create({
    walletProvider: wallet,
    network: process.env.NETWORK ?? "bsc-testnet",
  });
  const me = erc8183.address ?? "";

  const job = await erc8183.getJob(jobId);
  if (job.status !== JobStatus.SUBMITTED) {
    console.error(
      `jobId=${jobId} is ${JobStatus[job.status]} — settle requires SUBMITTED`,
    );
    return 1;
  }

  const [verdict] = await erc8183.getVerdict(jobId);
  if (verdict === Verdict.PENDING) {
    console.error(
      `jobId=${jobId} verdict is PENDING — wait until the dispute window elapses (or vote quorum is reached) and retry.`,
    );
    return 1;
  }

  if (job.provider.toLowerCase() !== me.toLowerCase()) {
    console.warn(
      `[warn] jobId=${jobId} provider is ${job.provider}, this wallet is ${me} — you will pay gas to settle a job you do not own.`,
    );
  }

  console.log(
    `[settler=${me}] settling jobId=${jobId} (verdict=${Verdict[verdict]}) …`,
  );
  const result = await erc8183.settle(jobId);
  console.log(`[settler] settle tx: ${result.transactionHash}`);

  const final = await erc8183.getJob(jobId);
  console.log(`[settler] jobId=${jobId} status -> ${JobStatus[final.status]}`);
  return 0;
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error instanceof Error ? error.stack : error);
    process.exit(1);
  });
