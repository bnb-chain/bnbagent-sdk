/**
 * Get APEX job details and Evaluator assertion info
 * Usage: JOB_ID=11 npm run get-job
 */
import { formatUnits } from "viem";
import { publicClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, OOV3_ADDRESS } from "./config.js";

const JOB_ID = BigInt(process.argv[2] || process.env.JOB_ID || "1");

// Matches AgenticCommerceUpgradeable.JobStatus enum
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
        // Order must match AgenticCommerceUpgradeable.Job struct exactly:
        // id, client, provider, evaluator, description, budget, expiredAt, status, hook
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
    name: "getAssertionInfo",
    outputs: [
      { name: "assertionId", type: "bytes32" },
      { name: "initiated", type: "bool" },
      { name: "disputed", type: "bool" },
      { name: "livenessEnd", type: "uint256" },
      { name: "settleable", type: "bool" },
    ],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [],
    name: "liveness",
    outputs: [{ name: "", type: "uint64" }],
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

const OOV3_ASSERTION_ABI = [
  {
    name: "getAssertion",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "assertionId", type: "bytes32" }],
    outputs: [
      {
        name: "",
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
] as const;

async function main() {
  console.log("=".repeat(60));
  console.log("APEX Job & Assertion Query");
  console.log("=".repeat(60));
  console.log("ERC-8183 Address:", ERC8183_ADDRESS);
  console.log("APEX Evaluator Address:", APEX_EVALUATOR_ADDRESS);
  console.log("Job ID:", JOB_ID.toString());
  console.log("");

  // Query APEX Job
  console.log("--- APEX Job Info ---");
  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [JOB_ID],
  });

  console.log("Job ID:", job.id.toString());
  console.log("Client:", job.client);
  console.log("Provider:", job.provider);
  console.log("Evaluator:", job.evaluator);
  console.log("Hook:", job.hook);
  console.log("Budget:", formatUnits(job.budget, 18), "U");
  console.log("Expired At:", new Date(Number(job.expiredAt) * 1000).toLocaleString());
  console.log("Status:", STATUS_LABELS[job.status] ?? `Unknown(${job.status})`);

  // Show full description — parse as JSON if possible, otherwise raw
  console.log("");
  console.log("--- Description ---");
  try {
    const parsed = JSON.parse(job.description);
    // Add human-readable timestamps for unix epoch fields
    const timestampFields = ["negotiated_at", "quote_expires_at"];
    for (const field of timestampFields) {
      if (typeof parsed[field] === "number") {
        const date = new Date(parsed[field] * 1000);
        parsed[field + "_human"] = date.toLocaleString("en-US", { timeZoneName: "short" });
      }
    }
    console.log(JSON.stringify(parsed, null, 2));
  } catch {
    console.log(job.description);
  }

  const isAPEX = job.evaluator.toLowerCase() === APEX_EVALUATOR_ADDRESS.toLowerCase();
  console.log("");
  console.log("Uses APEX Evaluator:", isAPEX ? "✓ Yes" : "✗ No");

  if (!isAPEX) {
    console.log("This job does not use APEX Evaluator. No assertion info available.");
    return;
  }

  // Query Evaluator Info
  console.log("");
  console.log("--- APEX Evaluator Info ---");

  const [liveness, minBond] = await Promise.all([
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "liveness",
    }),
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "getMinimumBond",
    }),
  ]);

  console.log("Liveness Period:", Number(liveness) / 60, "minutes");
  console.log("Minimum Bond:", formatUnits(minBond, 18), "U");
  console.log("Bond Model: Provider-pays-bond (provider approves evaluator, evaluator pulls bond per assertion)");

  // Query Assertion Info
  console.log("");
  console.log("--- Assertion Info ---");

  // Query individual mappings to avoid revert in getAssertionInfo
  const MAPPING_ABI = [
    {
      inputs: [{ name: "jobId", type: "uint256" }],
      name: "jobToAssertion",
      outputs: [{ name: "", type: "bytes32" }],
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
    {
      inputs: [{ name: "jobId", type: "uint256" }],
      name: "jobDisputed",
      outputs: [{ name: "", type: "bool" }],
      stateMutability: "view",
      type: "function",
    },
  ] as const;

  const [assertionId, initiated, disputed] = await Promise.all([
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: MAPPING_ABI,
      functionName: "jobToAssertion",
      args: [JOB_ID],
    }),
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: MAPPING_ABI,
      functionName: "jobAssertionInitiated",
      args: [JOB_ID],
    }),
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: MAPPING_ABI,
      functionName: "jobDisputed",
      args: [JOB_ID],
    }),
  ]);

  let livenessEnd = 0n;
  let settleable = false;

  console.log("Assertion ID:", assertionId);
  console.log("Initiated:", initiated ? "✓ Yes" : "✗ No");
  console.log("Disputed:", disputed ? "⚠️ Yes (DVM Arbitration)" : "✗ No");

  // If assertion exists, try to get liveness info from OOv3
  const ZERO_BYTES32 = "0x0000000000000000000000000000000000000000000000000000000000000000";

  if (assertionId !== ZERO_BYTES32 && initiated) {
    // Get detailed assertion from OOv3
    console.log("");
    console.log("--- OOv3 Assertion Details ---");

    try {
      const oov3Assertion = await publicClient.readContract({
        address: OOV3_ADDRESS,
        abi: OOV3_ASSERTION_ABI,
        functionName: "getAssertion",
        args: [assertionId],
      });

      const expirationTime = Number(oov3Assertion.expirationTime);
      const now = Math.floor(Date.now() / 1000);
      const remaining = expirationTime - now;

      console.log("Asserter:", oov3Assertion.asserter);
      console.log("Assertion Time:", new Date(Number(oov3Assertion.assertionTime) * 1000).toLocaleString());
      console.log("Expiration Time:", new Date(expirationTime * 1000).toLocaleString());

      if (remaining > 0) {
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;
        console.log("Time Remaining:", `${mins}m ${secs}s`);
        console.log("Settleable:", "✗ No (challenge period active)");
      } else {
        console.log("Time Remaining:", "✓ Expired");
        console.log("Settleable:", oov3Assertion.settled ? "Already settled" : "✓ Yes (ready to settle)");
      }

      console.log("Settled:", oov3Assertion.settled ? "✓ Yes" : "✗ No");
      if (oov3Assertion.settled) {
        console.log("Settlement Resolution:", oov3Assertion.settlementResolution ? "TRUE (Approved)" : "FALSE (Rejected)");
      }
      console.log("Bond:", formatUnits(oov3Assertion.bond, 18), "U");
      console.log("Disputer:", oov3Assertion.disputer === "0x0000000000000000000000000000000000000000" ? "None" : oov3Assertion.disputer);
    } catch (e) {
      console.log("Could not fetch OOv3 assertion details:", (e as Error).message);
    }
  } else if (!initiated) {
    console.log("Settleable:", "✗ No");
    console.log("");
    if (job.status === 1) {
      console.log("ℹ️  Job is Funded but not yet Submitted. Assertion will be created when Provider submits.");
    } else if (job.status === 0) {
      console.log("ℹ️  Job is Open. Waiting for funding.");
    } else if (job.status === 2) {
      console.log("⚠️  Job is Submitted but assertion NOT initiated!");
      console.log("   This likely means the afterAction hook failed (e.g., insufficient bond at submit time).");
      console.log("   You can manually initiate: evaluator.initiateAssertion(jobId)");
    }
  }

  console.log("");
  console.log("=".repeat(60));
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
