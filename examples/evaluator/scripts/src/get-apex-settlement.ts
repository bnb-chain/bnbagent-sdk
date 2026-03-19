/**
 * Get settlement details for an APEX job
 * Usage: npm run get-apex-settlement -- <jobId>
 */
import { formatUnits } from "viem";
import { publicClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, OOV3_ADDRESS } from "./config.js";

const STATUS_LABELS: Record<number, string> = {
  0: "None",
  1: "Open",
  2: "Funded",
  3: "Submitted",
  4: "Completed",
  5: "Rejected",
  6: "Expired",
};

const ERC8183_ABI = [
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "getJob",
    outputs: [
      {
        components: [
          { name: "client", type: "address" },
          { name: "provider", type: "address" },
          { name: "evaluator", type: "address" },
          { name: "hook", type: "address" },
          { name: "budget", type: "uint256" },
          { name: "expiredAt", type: "uint256" },
          { name: "status", type: "uint8" },
          { name: "deliverable", type: "bytes32" },
          { name: "description", type: "string" },
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
    name: "jobToAssertion",
    outputs: [{ name: "", type: "bytes32" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "jobDisputed",
    outputs: [{ name: "", type: "bool" }],
    stateMutability: "view",
    type: "function",
  },
] as const;

const JOB_COMPLETED_EVENT = {
  name: "JobCompleted",
  type: "event",
  inputs: [
    { name: "jobId", type: "uint256", indexed: true },
    { name: "reason", type: "bytes32", indexed: false },
  ],
} as const;

const JOB_REJECTED_EVENT = {
  name: "JobRejected",
  type: "event",
  inputs: [
    { name: "jobId", type: "uint256", indexed: true },
    { name: "reason", type: "bytes32", indexed: false },
  ],
} as const;

const ASSERTION_SETTLED_EVENT = {
  name: "AssertionSettled",
  type: "event",
  inputs: [
    { name: "assertionId", type: "bytes32", indexed: true },
    { name: "bondRecipient", type: "address", indexed: true },
    { name: "disputed", type: "bool", indexed: false },
    { name: "settlementResolution", type: "bool", indexed: false },
    { name: "settleCaller", type: "address", indexed: false },
  ],
} as const;

const ERC20_TRANSFER_EVENT = {
  name: "Transfer",
  type: "event",
  inputs: [
    { name: "from", type: "address", indexed: true },
    { name: "to", type: "address", indexed: true },
    { name: "value", type: "uint256", indexed: false },
  ],
} as const;

async function main() {
  const jobId = process.argv[2] || process.env.JOB_ID;

  if (!jobId) {
    console.error("Usage: npm run get-apex-settlement -- <jobId>");
    process.exit(1);
  }

  console.log(`\n============================================================`);
  console.log(`APEX Job #${jobId} Settlement Details`);
  console.log(`============================================================\n`);

  // Get job details
  console.log("[1/4] Fetching job details...");

  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [BigInt(jobId)],
  });

  console.log(`  Status:    ${STATUS_LABELS[job.status] || job.status}`);
  console.log(`  Client:    ${job.client}`);
  console.log(`  Provider:  ${job.provider}`);
  console.log(`  Evaluator: ${job.evaluator}`);
  console.log(`  Budget:    ${formatUnits(job.budget, 18)} U`);

  if (job.status !== 4 && job.status !== 5) {
    console.log(`\n⚠️  Job is not settled yet (Completed=4, Rejected=5)`);
    console.log(`  Current status: ${STATUS_LABELS[job.status]}`);
  }

  const latestBlock = await publicClient.getBlockNumber();
  const BATCH_SIZE = 4900n;
  const MAX_BATCHES = 30;

  // Search for JobCompleted or JobRejected event
  console.log("\n[2/4] Searching for settlement event...");

  let settlementEvent: any = null;
  let settlementType: "completed" | "rejected" | null = null;
  let currentToBlock = latestBlock;

  for (let i = 0; i < MAX_BATCHES && !settlementEvent; i++) {
    const currentFromBlock = currentToBlock - BATCH_SIZE > 0n ? currentToBlock - BATCH_SIZE : 0n;

    try {
      // Search JobCompleted
      const completedEvents = await publicClient.getLogs({
        address: ERC8183_ADDRESS,
        event: JOB_COMPLETED_EVENT,
        args: { jobId: BigInt(jobId) },
        fromBlock: currentFromBlock,
        toBlock: currentToBlock,
      });

      if (completedEvents.length > 0) {
        settlementEvent = completedEvents[0];
        settlementType = "completed";
        break;
      }

      // Search JobRejected
      const rejectedEvents = await publicClient.getLogs({
        address: ERC8183_ADDRESS,
        event: JOB_REJECTED_EVENT,
        args: { jobId: BigInt(jobId) },
        fromBlock: currentFromBlock,
        toBlock: currentToBlock,
      });

      if (rejectedEvents.length > 0) {
        settlementEvent = rejectedEvents[0];
        settlementType = "rejected";
        break;
      }
    } catch {
      // Continue
    }

    currentToBlock = currentFromBlock - 1n;
    if (currentFromBlock <= 0n) break;
  }

  if (!settlementEvent) {
    console.log("  ❌ Settlement event not found");
    console.log("  The job may not have been settled yet.");
    
    // Check if it uses APEX Evaluator
    if (job.evaluator.toLowerCase() === APEX_EVALUATOR_ADDRESS.toLowerCase()) {
      console.log("\n  This job uses APEX Evaluator. Checking assertion status...");
      
      const [assertionId, isDisputed] = await Promise.all([
        publicClient.readContract({
          address: APEX_EVALUATOR_ADDRESS,
          abi: EVALUATOR_ABI,
          functionName: "jobToAssertion",
          args: [BigInt(jobId)],
        }),
        publicClient.readContract({
          address: APEX_EVALUATOR_ADDRESS,
          abi: EVALUATOR_ABI,
          functionName: "jobDisputed",
          args: [BigInt(jobId)],
        }),
      ]);

      if (assertionId !== "0x0000000000000000000000000000000000000000000000000000000000000000") {
        console.log(`  Assertion ID: ${assertionId}`);
        console.log(`  Disputed: ${isDisputed}`);
        
        if (isDisputed) {
          console.log("\n  💡 Use 'resolve-apex-dispute' to resolve the dispute.");
        } else {
          console.log("\n  💡 Use 'settle-apex-job' to settle after liveness period.");
        }
      }
    }
    
    process.exit(1);
  }

  const txHash = settlementEvent.transactionHash;
  const blockNumber = settlementEvent.blockNumber;

  console.log(`  ✅ Found ${settlementType === "completed" ? "JobCompleted" : "JobRejected"} event`);
  console.log(`\n  Settlement Details:`);
  console.log(`  ────────────────────────────────────────`);
  console.log(`  Type:     ${settlementType === "completed" ? "✅ COMPLETED (Provider paid)" : "❌ REJECTED (Client refunded)"}`);
  console.log(`  Reason:   ${settlementEvent.args.reason}`);
  console.log(`  TX Hash:  ${txHash}`);
  console.log(`  Block:    ${blockNumber}`);

  // Get transaction receipt
  console.log(`\n[3/4] Fetching transaction details...`);
  const receipt = await publicClient.getTransactionReceipt({ hash: txHash });
  const block = await publicClient.getBlock({ blockNumber });

  console.log(`  Status:    ${receipt.status === "success" ? "✅ Success" : "❌ Failed"}`);
  console.log(`  Gas Used:  ${receipt.gasUsed}`);
  console.log(`  Timestamp: ${new Date(Number(block.timestamp) * 1000).toISOString()}`);

  // Find token transfers
  console.log(`\n[4/4] Finding token transfers...`);

  const transferLogs = receipt.logs.filter(
    (log) => log.topics[0] === "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
  );

  if (transferLogs.length > 0) {
    console.log(`\n  Token Transfers:`);
    console.log(`  ────────────────────────────────────────`);

    for (const log of transferLogs) {
      const from = `0x${log.topics[1]?.slice(26)}`;
      const to = `0x${log.topics[2]?.slice(26)}`;
      const value = BigInt(log.data);
      const token = log.address;

      let label = "";
      const fromLower = from.toLowerCase();
      const toLower = to.toLowerCase();
      
      if (fromLower === ERC8183_ADDRESS.toLowerCase()) {
        if (toLower === job.provider.toLowerCase()) {
          label = " ← PROVIDER PAYMENT";
        } else if (toLower === job.client.toLowerCase()) {
          label = " ← CLIENT REFUND";
        }
      }

      console.log(`  From:   ${from}`);
      console.log(`  To:     ${to}${label}`);
      console.log(`  Amount: ${formatUnits(value, 18)} tokens`);
      console.log(`  Token:  ${token}`);
      console.log(`  ────────────────────────────────────────`);
    }
  } else {
    console.log(`  No token transfers found in this TX`);
  }

  // Check for OOv3 AssertionSettled if using APEX Evaluator
  if (job.evaluator.toLowerCase() === APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.log(`\n[Bonus] Checking OOv3 AssertionSettled...`);

    const assertionId = await publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "jobToAssertion",
      args: [BigInt(jobId)],
    });

    if (assertionId !== "0x0000000000000000000000000000000000000000000000000000000000000000") {
      currentToBlock = latestBlock;
      let assertionSettledEvent: any = null;

      for (let i = 0; i < MAX_BATCHES && !assertionSettledEvent; i++) {
        const currentFromBlock = currentToBlock - BATCH_SIZE > 0n ? currentToBlock - BATCH_SIZE : 0n;

        try {
          const events = await publicClient.getLogs({
            address: OOV3_ADDRESS,
            event: ASSERTION_SETTLED_EVENT,
            args: { assertionId },
            fromBlock: currentFromBlock,
            toBlock: currentToBlock,
          });

          if (events.length > 0) {
            assertionSettledEvent = events[0];
            break;
          }
        } catch {
          // Continue
        }

        currentToBlock = currentFromBlock - 1n;
        if (currentFromBlock <= 0n) break;
      }

      if (assertionSettledEvent) {
        console.log(`  ✅ AssertionSettled found`);
        console.log(`  Assertion ID:   ${assertionId}`);
        console.log(`  Bond Recipient: ${assertionSettledEvent.args.bondRecipient}`);
        console.log(`  Disputed:       ${assertionSettledEvent.args.disputed}`);
        console.log(`  Resolution:     ${assertionSettledEvent.args.settlementResolution ? "TRUE (Provider wins)" : "FALSE (Client wins)"}`);
        console.log(`  Settle Caller:  ${assertionSettledEvent.args.settleCaller}`);
        console.log(`  TX Hash:        ${assertionSettledEvent.transactionHash}`);
      } else {
        console.log(`  AssertionSettled event not found`);
      }
    }
  }

  console.log(`\n============================================================\n`);
}

main().catch(console.error);
