/**
 * Resolve a disputed APEX job by pushing price to MockOracle
 *
 * Usage: npm run resolve-apex-dispute -- <jobId> <true|false>
 *   true  = Provider wins (assertion correct, job completes)
 *   false = Client wins (assertion incorrect, job rejected)
 *
 * This script:
 * 1. Gets the assertion details from APEX Evaluator
 * 2. Finds the price request in MockOracle
 * 3. Pushes the resolution (1e18 for true, 0 for false)
 * 4. Settles the assertion via APEX Evaluator.settleJob()
 */
import { parseUnits } from "viem";
import { publicClient, getSettleWalletClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, OOV3_ADDRESS } from "./config.js";

const MOCK_ORACLE_ADDRESS = "0xd48D0Bdcf46E87352ECD9Cb9A22A27e9a761F8c2" as `0x${string}`;

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
  {
    inputs: [{ name: "jobId", type: "uint256" }],
    name: "settleJob",
    outputs: [],
    stateMutability: "nonpayable",
    type: "function",
  },
] as const;

const OOV3_ABI = [
  {
    name: "getAssertion",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "assertionId", type: "bytes32" }],
    outputs: [
      {
        type: "tuple",
        components: [
          {
            name: "escalationManagerSettings",
            type: "tuple",
            components: [
              { name: "arbitrateViaEscalationManager", type: "bool" },
              { name: "discardOracle", type: "bool" },
              { name: "validateDisputers", type: "bool" },
              { name: "assertingCaller", type: "address" },
              { name: "escalationManager", type: "address" },
            ],
          },
          { name: "asserter", type: "address" },
          { name: "assertionTime", type: "uint64" },
          { name: "settled", type: "bool" },
          { name: "currency", type: "address" },
          { name: "expirationTime", type: "uint64" },
          { name: "settlementResolution", type: "bool" },
          { name: "domainId", type: "bytes32" },
          { name: "identifier", type: "bytes32" },
          { name: "bond", type: "uint256" },
          { name: "callbackRecipient", type: "address" },
          { name: "disputer", type: "address" },
        ],
      },
    ],
  },
  {
    name: "cachedOracle",
    type: "function",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "address" }],
  },
] as const;

const MOCK_ORACLE_ABI = [
  {
    name: "pushPriceByRequestId",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "requestId", type: "bytes32" },
      { name: "price", type: "int256" },
    ],
    outputs: [],
  },
] as const;

async function main() {
  const jobId = process.argv[2] || process.env.JOB_ID;
  const resolution = process.argv[3] || process.env.RESOLUTION;

  if (!jobId || !resolution) {
    console.error("Usage: npm run resolve-apex-dispute -- <jobId> <true|false>");
    console.error("  true  = Provider wins (assertion correct, job completes)");
    console.error("  false = Client wins (assertion incorrect, job rejected)");
    process.exit(1);
  }

  const isTrue = resolution.toLowerCase() === "true";
  const priceValue = isTrue ? parseUnits("1", 18) : 0n;

  console.log(`\n=== Resolve APEX Dispute for Job #${jobId} ===\n`);
  console.log(`Resolution: ${isTrue ? "TRUE (Provider wins)" : "FALSE (Client wins)"}`);
  console.log(`Price value: ${priceValue}`);

  // Step 1: Get job and assertion details
  console.log("\n[1/5] Fetching job details...");

  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [BigInt(jobId)],
  });

  console.log(`  Status: ${STATUS_LABELS[job.status] || job.status}`);
  console.log(`  Evaluator: ${job.evaluator}`);

  if (job.status !== 3) {
    console.error(`\n❌ Job is not in Submitted status (current: ${STATUS_LABELS[job.status]})`);
    process.exit(1);
  }

  // Check if using APEX Evaluator
  if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.error(`\n❌ Job does not use APEX Evaluator`);
    console.error(`  Expected: ${APEX_EVALUATOR_ADDRESS}`);
    console.error(`  Actual: ${job.evaluator}`);
    process.exit(1);
  }

  // Step 2: Get assertion from evaluator
  console.log("\n[2/5] Fetching assertion from APEX Evaluator...");

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

  console.log(`  Assertion ID: ${assertionId}`);
  console.log(`  Disputed: ${isDisputed}`);

  if (assertionId === "0x0000000000000000000000000000000000000000000000000000000000000000") {
    console.error("\n❌ No assertion found for this job");
    process.exit(1);
  }

  if (!isDisputed) {
    console.error("\n❌ Assertion is not disputed");
    console.log("  Use settle-apex-job if liveness has passed");
    process.exit(1);
  }

  // Step 3: Get assertion details from OOv3
  console.log("\n[3/5] Fetching assertion from OOv3...");

  const assertion = await publicClient.readContract({
    address: OOV3_ADDRESS,
    abi: OOV3_ABI,
    functionName: "getAssertion",
    args: [assertionId],
  });

  console.log(`  Settled: ${assertion.settled}`);
  console.log(`  Disputer: ${assertion.disputer}`);

  if (assertion.settled) {
    console.log("\n✅ Assertion is already settled!");
    process.exit(0);
  }

  // Step 4: Find REQUEST_ID and push price
  console.log("\n[4/5] Finding REQUEST_ID from dispute transaction...");

  let oracleAddress: `0x${string}`;
  try {
    oracleAddress = await publicClient.readContract({
      address: OOV3_ADDRESS,
      abi: OOV3_ABI,
      functionName: "cachedOracle",
    }) as `0x${string}`;
    console.log(`  Oracle: ${oracleAddress}`);
  } catch {
    oracleAddress = MOCK_ORACLE_ADDRESS;
    console.log(`  Oracle (fallback): ${oracleAddress}`);
  }

  const walletClient = getSettleWalletClient();
  console.log(`  Wallet: ${walletClient.account.address}`);

  // Search for AssertionDisputed event
  const latestBlock = await publicClient.getBlockNumber();
  const BATCH_SIZE = 4900n;
  const MAX_BATCHES = 20;

  console.log(`  Searching for AssertionDisputed event...`);

  let disputeTxHash: `0x${string}` | null = null;
  let currentToBlock = latestBlock;

  for (let i = 0; i < MAX_BATCHES && !disputeTxHash; i++) {
    const currentFromBlock = currentToBlock - BATCH_SIZE;

    try {
      const events = await publicClient.getLogs({
        address: OOV3_ADDRESS,
        event: {
          name: "AssertionDisputed",
          type: "event",
          inputs: [
            { name: "assertionId", type: "bytes32", indexed: true },
            { name: "caller", type: "address", indexed: true },
            { name: "disputer", type: "address", indexed: true },
          ],
        },
        args: { assertionId },
        fromBlock: currentFromBlock > 0n ? currentFromBlock : 0n,
        toBlock: currentToBlock,
      });

      if (events.length > 0) {
        disputeTxHash = events[0].transactionHash;
        console.log(`  Found dispute TX: ${disputeTxHash}`);
      }
    } catch {
      // Continue
    }

    currentToBlock = currentFromBlock - 1n;
    if (currentFromBlock <= 0n) break;
  }

  if (!disputeTxHash) {
    console.error("\n❌ AssertionDisputed event not found");
    process.exit(1);
  }

  // Get REQUEST_ID from dispute TX receipt
  console.log(`  Extracting REQUEST_ID...`);
  const disputeReceipt = await publicClient.getTransactionReceipt({ hash: disputeTxHash });

  const oracleLowercase = oracleAddress.toLowerCase();
  let oracleLog = disputeReceipt.logs.find(
    (log) => log.address.toLowerCase() === oracleLowercase && log.topics.length >= 4
  );

  if (!oracleLog) {
    const knownAddresses = [OOV3_ADDRESS.toLowerCase(), ERC8183_ADDRESS.toLowerCase(), APEX_EVALUATOR_ADDRESS.toLowerCase()];
    oracleLog = disputeReceipt.logs.find(
      (log) => log.topics.length >= 4 && !knownAddresses.includes(log.address.toLowerCase())
    );
    if (oracleLog) {
      oracleAddress = oracleLog.address as `0x${string}`;
    }
  }

  if (!oracleLog || !oracleLog.topics[3]) {
    console.error("\n❌ Oracle REQUEST_ID not found");
    console.log("  This deployment may use real DVM instead of MockOracle.");
    process.exit(1);
  }

  const requestId = oracleLog.topics[3] as `0x${string}`;
  console.log(`  REQUEST_ID: ${requestId}`);

  // Push price
  console.log(`\n  Pushing price to Oracle...`);
  try {
    const txHash = await walletClient.writeContract({
      address: oracleAddress,
      abi: MOCK_ORACLE_ABI,
      functionName: "pushPriceByRequestId",
      args: [requestId, priceValue],
    });

    console.log(`  TX: ${txHash}`);
    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
    console.log(`  Status: ${receipt.status === "success" ? "✅ Success" : "❌ Failed"}`);

    if (receipt.status !== "success") {
      process.exit(1);
    }
  } catch (e: any) {
    console.error(`  Error: ${e.message?.slice(0, 200)}`);
    process.exit(1);
  }

  // Step 5: Settle via APEX Evaluator
  console.log("\n[5/5] Settling job via APEX Evaluator...");

  try {
    const settleTxHash = await walletClient.writeContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "settleJob",
      args: [BigInt(jobId)],
      gas: 500_000n, // Must cover full chain: OOv3.settle → callback → erc8183.complete + hooks + transfer
    });

    console.log(`  TX: ${settleTxHash}`);
    const settleReceipt = await publicClient.waitForTransactionReceipt({ hash: settleTxHash });
    console.log(`  Status: ${settleReceipt.status === "success" ? "✅ Success" : "❌ Failed"}`);

    if (settleReceipt.status === "success") {
      console.log(`\n🎉 Dispute resolved!`);
      console.log(`  Result: ${isTrue ? "Provider wins - payment released" : "Client wins - payment refunded"}`);
    }
  } catch (e: any) {
    console.error(`  Error settling: ${e.message?.slice(0, 200)}`);
  }
}

main().catch(console.error);
