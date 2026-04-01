/**
 * Settle an APEX job's UMA assertion after challenge period expires
 * Usage: JOB_ID=14 npm run settle-job
 */
import { formatUnits } from "viem";
import { publicClient, getSettleWalletClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS } from "./config.js";

const JOB_ID = BigInt(process.argv[2] || process.env.JOB_ID || "0");

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

async function main() {
  if (JOB_ID === 0n) {
    console.error("Usage: JOB_ID=14 npm run settle-job");
    console.error("   or: npm run settle-job -- 14");
    process.exit(1);
  }

  const walletClient = getSettleWalletClient();
  console.log("=".repeat(60));
  console.log("Settle APEX Job");
  console.log("=".repeat(60));
  console.log("Job ID:", JOB_ID.toString());
  console.log("ERC-8183:", ERC8183_ADDRESS);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("Settler:", walletClient.account.address);
  console.log("");

  // Check job status
  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [JOB_ID],
  });

  console.log("Current Status:", STATUS_LABELS[job.status] || job.status);

  if (job.status !== 2) {
    console.error(`❌ Job is not in Submitted status (current: ${STATUS_LABELS[job.status] ?? job.status}). Cannot settle.`);
    process.exit(1);
  }

  if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.error("❌ Job does not use APEX Evaluator.");
    process.exit(1);
  }

  // Check if assertion is initiated
  const initiated = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "jobAssertionInitiated",
    args: [JOB_ID],
  });

  if (!initiated) {
    console.error("❌ Assertion not initiated. Run initiate-assertion first.");
    process.exit(1);
  }

  // Check if settleable
  const settleable = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "isSettleable",
    args: [JOB_ID],
  });

  if (!settleable) {
    console.error("❌ Not settleable yet. Challenge period may still be active.");
    process.exit(1);
  }

  console.log("✓ Job is settleable");
  console.log("");
  console.log("Settling...");

  try {
    const { request } = await publicClient.simulateContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "settleJob",
      args: [JOB_ID],
      account: walletClient.account,
      gas: 500_000n, // Must cover full chain: OOv3.settle → callback → erc8183.complete + hooks + transfer
    });

    const txHash = await walletClient.writeContract(request);
    console.log("TX:", txHash);
    console.log("Waiting for confirmation...");

    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
    console.log("Status:", receipt.status === "success" ? "✅ Success" : "❌ Failed");

    // Check new job status
    const newJob = await publicClient.readContract({
      address: ERC8183_ADDRESS,
      abi: ERC8183_ABI,
      functionName: "getJob",
      args: [JOB_ID],
    });

    console.log("");
    console.log("Job after settle:");
    console.log("  Status:", STATUS_LABELS[newJob.status] || newJob.status);
    console.log("  Budget:", formatUnits(newJob.budget, 18), "U");
  } catch (e: any) {
    console.error("Error settling job:", e.shortMessage || e.message);
  }

  console.log("");
  console.log("=".repeat(60));
}

main().catch(console.error);
