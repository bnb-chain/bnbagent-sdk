/**
 * Manually initiate assertion for an APEX job
 * Usage: JOB_ID=14 npm run initiate-assertion
 *
 * Use this when:
 * - Job is in Submitted status but assertion wasn't initiated
 * - (e.g., bond was insufficient when provider submitted)
 *
 * Access control:
 * - Only the job's **provider** can call initiateAssertion().
 *   The contract checks `msg.sender == job.provider` and reverts
 *   with CallerNotAllowed(address) otherwise.
 * - PRIVATE_KEY must correspond to the provider address of the job.
 */
import { formatUnits } from "viem";
import { publicClient, getWalletClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS } from "./config.js";

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
    name: "initiateAssertion",
    outputs: [],
    stateMutability: "nonpayable",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "jobAssertionInitiated",
    outputs: [{ name: "", type: "bool" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "jobToAssertion",
    outputs: [{ name: "", type: "bytes32" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "bondBalance",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "getMinimumBond",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
] as const;

async function main() {
  if (JOB_ID === 0n) {
    console.error("Usage: JOB_ID=14 npm run initiate-assertion");
    console.error("   or: npm run initiate-assertion -- 14");
    process.exit(1);
  }

  const walletClient = getWalletClient();

  console.log("=".repeat(60));
  console.log("Initiate Assertion for APEX Job");
  console.log("=".repeat(60));
  console.log("Job ID:", JOB_ID.toString());
  console.log("ERC-8183:", ERC8183_ADDRESS);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("Caller:", walletClient.account.address);
  console.log("");

  // Check job
  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [JOB_ID],
  });

  console.log("Job Status:", STATUS_LABELS[job.status] || job.status);

  if (job.status !== 2) {
    console.error(`❌ Job is not in Submitted status (current: ${STATUS_LABELS[job.status] ?? job.status}). Cannot initiate assertion.`);
    process.exit(1);
  }

  if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.error("❌ Job does not use APEX Evaluator.");
    process.exit(1);
  }

  // Check if already initiated
  const initiated = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "jobAssertionInitiated",
    args: [JOB_ID],
  });

  if (initiated) {
    const assertionId = await publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "jobToAssertion",
      args: [JOB_ID],
    });
    console.log("✓ Assertion already initiated:", assertionId);
    process.exit(0);
  }

  // Check bond balance
  const [bondBalance, minBond] = await Promise.all([
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "bondBalance",
    }),
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "getMinimumBond",
    }),
  ]);

  console.log("Bond Balance:", formatUnits(bondBalance, 18), "U");
  console.log("Minimum Bond:", formatUnits(minBond, 18), "U");

  if (bondBalance < minBond) {
    console.error("❌ Insufficient bond balance in evaluator.");
    console.error("   Run: AMOUNT=10 npm run deposit-bond");
    process.exit(1);
  }

  console.log("");
  console.log("Initiating assertion...");

  try {
    const { request } = await publicClient.simulateContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "initiateAssertion",
      args: [JOB_ID],
      account: walletClient.account,
    });

    const txHash = await walletClient.writeContract(request);
    console.log("TX:", txHash);
    console.log("Waiting for confirmation...");

    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
    console.log("Status:", receipt.status === "success" ? "✅ Success" : "❌ Failed");

    if (receipt.status === "success") {
      const assertionId = await publicClient.readContract({
        address: APEX_EVALUATOR_ADDRESS,
        abi: EVALUATOR_ABI,
        functionName: "jobToAssertion",
        args: [JOB_ID],
      });
      console.log("");
      console.log("✓ Assertion initiated!");
      console.log("  Assertion ID:", assertionId);
      console.log("  Challenge period: 30 minutes");
    }
  } catch (e: any) {
    console.error("Error initiating assertion:", e.shortMessage || e.message);
  }

  console.log("");
  console.log("=".repeat(60));
}

main().catch(console.error);
