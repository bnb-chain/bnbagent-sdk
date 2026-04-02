/**
 * Settle all settleable APEX jobs using APEX Evaluator
 * 
 * Scans all jobs from 1 to jobCounter and settles any that are:
 * - Status = Submitted (2)
 * - Uses APEX Evaluator
 * - Challenge period has expired (isSettleable = true)
 * 
 * Usage:
 *   pnpm tsx src/settle-jobs.ts           # Settle all
 *   pnpm tsx src/settle-jobs.ts --dry-run # Show what would be settled
 * 
 * Environment:
 *   RPC_URL                  - Blockchain RPC (default: BSC Testnet)
 *   ERC8183_ADDRESS          - ERC-8183 contract address
 *   APEX_EVALUATOR_ADDRESS   - APEX Evaluator contract address
 *   SETTLE_PRIVATE_KEY       - Private key for transactions (or PRIVATE_KEY)
 */
import { formatUnits } from "viem";
import { publicClient, getSettleWalletClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS } from "./config.js";

const DRY_RUN = process.argv.includes("--dry-run");

const STATUS_LABELS: Record<number, string> = {
  0: "Open",
  1: "Funded",
  2: "Submitted",
  3: "Completed",
  4: "Rejected",
  5: "Expired",
};

const ERC8183_ABI = [
  {
    inputs: [],
    name: "jobCounter",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "getJob",
    outputs: [
      {
        components: [
          { name: "id", type: "uint256" },
          { name: "client", type: "address" },
          { name: "provider", type: "address" },
          { name: "evaluator", type: "address" },
          { name: "description", type: "string" },
          { name: "budget", type: "uint256" },
          { name: "expiredAt", type: "uint256" },
          { name: "status", type: "uint8" },
          { name: "hook", type: "address" },
        ],
        name: "",
        type: "tuple",
      },
    ],
    stateMutability: "view",
    type: "function",
  },
] as const;

const EVALUATOR_ABI = [
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "settleJob",
    outputs: [],
    stateMutability: "nonpayable",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "isSettleable",
    outputs: [{ name: "", type: "bool" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "jobAssertionInitiated",
    outputs: [{ name: "", type: "bool" }],
    stateMutability: "view",
    type: "function",
  },
] as const;

interface SettleableJob {
  jobId: bigint;
  client: string;
  provider: string;
  budget: bigint;
}

async function findSettleableJobs(): Promise<SettleableJob[]> {
  const counter = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "jobCounter",
  });

  console.log(`Scanning jobs 1 to ${counter}...`);

  const settleable: SettleableJob[] = [];

  for (let jobId = 1n; jobId <= counter; jobId++) {
    try {
      const job = await publicClient.readContract({
        address: ERC8183_ADDRESS,
        abi: ERC8183_ABI,
        functionName: "getJob",
        args: [jobId],
      });

      // Skip if not Submitted (status 2)
      if (job.status !== 2) continue;

      // Skip if not using APEX Evaluator
      if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) continue;

      // Check if assertion is initiated
      const initiated = await publicClient.readContract({
        address: APEX_EVALUATOR_ADDRESS,
        abi: EVALUATOR_ABI,
        functionName: "jobAssertionInitiated",
        args: [jobId],
      });

      if (!initiated) continue;

      // Check if settleable
      const isSettleable = await publicClient.readContract({
        address: APEX_EVALUATOR_ADDRESS,
        abi: EVALUATOR_ABI,
        functionName: "isSettleable",
        args: [jobId],
      });

      if (isSettleable) {
        settleable.push({
          jobId,
          client: job.client,
          provider: job.provider,
          budget: job.budget,
        });
      }
    } catch (e: any) {
      // Skip jobs that can't be read (e.g., deleted)
      console.warn(`  Job ${jobId}: ${e.shortMessage || e.message}`);
    }
  }

  return settleable;
}

async function settleJob(jobId: bigint, walletClient: ReturnType<typeof getSettleWalletClient>): Promise<boolean> {
  try {
    const { request } = await publicClient.simulateContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "settleJob",
      args: [jobId],
      account: walletClient.account,
      gas: 500_000n, // Must cover full chain: OOv3.settle → callback → erc8183.complete + hooks + transfer
    });

    const txHash = await walletClient.writeContract(request);
    console.log(`  TX: ${txHash}`);

    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
    return receipt.status === "success";
  } catch (e: any) {
    console.error(`  Error: ${e.shortMessage || e.message}`);
    return false;
  }
}

async function main() {
  console.log("=".repeat(60));
  console.log("APEX Job Batch Settlement");
  console.log("=".repeat(60));
  console.log("ERC-8183:", ERC8183_ADDRESS);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("Mode:", DRY_RUN ? "DRY RUN (no transactions)" : "LIVE");
  console.log("");

  const jobs = await findSettleableJobs();

  if (jobs.length === 0) {
    console.log("No settleable jobs found.");
    return;
  }

  console.log("");
  console.log(`Found ${jobs.length} settleable job(s):`);
  console.log("");

  for (const job of jobs) {
    console.log(`  Job #${job.jobId}`);
    console.log(`    Client:   ${job.client}`);
    console.log(`    Provider: ${job.provider}`);
    console.log(`    Budget:   ${formatUnits(job.budget, 18)} tokens`);
    console.log("");
  }

  if (DRY_RUN) {
    console.log("Dry run complete. Run without --dry-run to settle.");
    return;
  }

  const walletClient = getSettleWalletClient();
  console.log(`Settler: ${walletClient.account.address}`);
  console.log("");

  let settled = 0;
  let failed = 0;

  for (const job of jobs) {
    console.log(`Settling job #${job.jobId}...`);
    const success = await settleJob(job.jobId, walletClient);
    if (success) {
      console.log(`  ✅ Settled`);
      settled++;
    } else {
      console.log(`  ❌ Failed`);
      failed++;
    }
    console.log("");
  }

  console.log("=".repeat(60));
  console.log(`Summary: ${settled} settled, ${failed} failed`);
  console.log("=".repeat(60));
}

main().catch(console.error);
