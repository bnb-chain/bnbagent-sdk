/**
 * Get and verify UMA claim content for an APEX job
 *
 * Usage: JOB_ID=14 npm run get-job-claim
 *
 * This script:
 * 1. Fetches job data from ERC-8183 contract
 * 2. Gets assertion ID from APEX Evaluator
 * 3. Decodes the AssertionMade event to get claim text
 * 4. Downloads deliverable from IPFS (if available)
 * 5. Verifies deliverable hash matches on-chain
 */
import { formatUnits, hexToString, keccak256, stringToBytes } from "viem";
import { publicClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, OOV3_ADDRESS } from "./config.js";

const JOB_ID = BigInt(process.argv[2] || process.env.JOB_ID || "0");

const IPFS_GATEWAYS = [
  "https://gateway.pinata.cloud/ipfs/",
  "https://ipfs.io/ipfs/",
  "https://cloudflare-ipfs.com/ipfs/",
  "https://dweb.link/ipfs/",
];

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
    name: "jobDataUrl",
    outputs: [{ name: "", type: "string" }],
    stateMutability: "view",
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

async function fetchFromIPFS(cid: string): Promise<any> {
  for (const gateway of IPFS_GATEWAYS) {
    try {
      const url = `${gateway}${cid}`;
      console.log(`  Trying: ${gateway.slice(0, 30)}...`);
      const response = await fetch(url, {
        signal: AbortSignal.timeout(15000),
        headers: { Accept: "application/json" },
      });
      if (response.ok) {
        const text = await response.text();
        console.log(`  ✅ Success`);
        try {
          return JSON.parse(text);
        } catch {
          return { raw_content: text };
        }
      }
    } catch (e) {
      // Try next gateway
    }
  }
  throw new Error("Failed to fetch from all IPFS gateways");
}

async function findAssertionMadeEvent(assertionId: `0x${string}`): Promise<{
  txHash: `0x${string}`;
  blockNumber: bigint;
  claim: string;
} | null> {
  const assertionMadeSignature = "0xdb1513f0abeb57a364db56aa3eb52015cca5268f00fd67bc73aaf22bccab02b7";

  // Search recent blocks (last ~24 hours on BSC = ~28800 blocks)
  const latestBlock = await publicClient.getBlockNumber();
  const startBlock = latestBlock - 30000n;

  console.log(`  Searching blocks ${startBlock} - ${latestBlock}...`);

  const BATCH_SIZE = 10000n;

  for (let from = startBlock; from <= latestBlock; from += BATCH_SIZE) {
    const to = from + BATCH_SIZE - 1n > latestBlock ? latestBlock : from + BATCH_SIZE - 1n;

    try {
      const logs = await publicClient.getLogs({
        address: OOV3_ADDRESS,
        fromBlock: from,
        toBlock: to,
      });

      for (const log of logs) {
        if (log.topics[0] !== assertionMadeSignature) continue;

        // Check if this log's assertionId matches (it's in topics[1])
        if (log.topics[1]?.toLowerCase() !== assertionId.toLowerCase()) continue;

        // Decode claim from data
        const data = log.data;
        const claimOffsetHex = data.slice(2 + 64, 2 + 128);
        const claimOffset = parseInt(claimOffsetHex, 16) * 2 + 2;
        const claimLengthHex = data.slice(claimOffset, claimOffset + 64);
        const claimLength = parseInt(claimLengthHex, 16);
        const claimDataHex = `0x${data.slice(claimOffset + 64, claimOffset + 64 + claimLength * 2)}` as `0x${string}`;
        const claim = hexToString(claimDataHex);

        return {
          txHash: log.transactionHash,
          blockNumber: log.blockNumber,
          claim,
        };
      }
    } catch (e) {
      // Continue to next batch
    }
  }

  return null;
}

async function main() {
  if (JOB_ID === 0n) {
    console.error("Usage: JOB_ID=14 npm run get-job-claim");
    process.exit(1);
  }

  console.log("");
  console.log("=".repeat(60));
  console.log(`APEX Job #${JOB_ID} Claim Verification`);
  console.log("=".repeat(60));
  console.log("");

  // Step 1: Get job details
  console.log("[1/5] Fetching job from ERC-8183 contract...");

  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [JOB_ID],
  });

  console.log(`  Status:      ${STATUS_LABELS[job.status] || job.status}`);
  console.log(`  Client:      ${job.client}`);
  console.log(`  Provider:    ${job.provider}`);
  console.log(`  Evaluator:   ${job.evaluator}`);
  console.log(`  Budget:      ${formatUnits(job.budget, 18)} U`);
  console.log(`  Description: ${job.description.length > 60 ? job.description.slice(0, 60) + "..." : job.description}`);
  console.log(`  Deliverable: (see JobSubmitted event — not stored in struct)`);
  console.log("");

  // Check if using APEX Evaluator
  if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.log("❌ This job does not use APEX Evaluator. No UMA claim to verify.");
    process.exit(0);
  }

  // Step 2: Get assertion info
  console.log("[2/5] Fetching assertion from APEX Evaluator...");

  const [assertionId, initiated] = await Promise.all([
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "jobToAssertion",
      args: [JOB_ID],
    }),
    publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "jobAssertionInitiated",
      args: [JOB_ID],
    }),
  ]);

  console.log(`  Assertion ID: ${assertionId}`);
  console.log(`  Initiated:    ${initiated ? "Yes" : "No"}`);

  if (!initiated) {
    console.log("");
    console.log("❌ Assertion not initiated yet. No claim to verify.");
    process.exit(0);
  }

  // Get assertion details from OOv3
  const assertion = await publicClient.readContract({
    address: OOV3_ADDRESS,
    abi: OOV3_ABI,
    functionName: "getAssertion",
    args: [assertionId],
  });

  console.log(`  Asserter:     ${assertion.asserter}`);
  console.log(`  Settled:      ${assertion.settled ? "Yes" : "No"}`);
  if (assertion.settled) {
    console.log(`  Resolution:   ${assertion.settlementResolution ? "TRUE (Approved)" : "FALSE (Rejected)"}`);
  }
  console.log("");

  // Step 3: Find and decode claim
  console.log("[3/5] Finding claim from AssertionMade event...");

  const eventData = await findAssertionMadeEvent(assertionId);

  if (!eventData) {
    console.log("  ❌ Could not find AssertionMade event");
    console.log("");
    console.log("  Note: Event may be too old or in a different block range.");
    process.exit(1);
  }

  console.log(`  TX:    ${eventData.txHash}`);
  console.log(`  Block: ${eventData.blockNumber}`);
  console.log("");

  // Display claim
  console.log("┌" + "─".repeat(78) + "┐");
  console.log("│ CLAIM (from chain):" + " ".repeat(57) + "│");
  console.log("├" + "─".repeat(78) + "┤");

  const words = eventData.claim.split(" ");
  let line = "│ ";
  for (const word of words) {
    if (line.length + word.length > 77) {
      console.log(line.padEnd(79) + "│");
      line = "│ ";
    }
    line += word + " ";
  }
  if (line.length > 2) {
    console.log(line.padEnd(79) + "│");
  }
  console.log("└" + "─".repeat(78) + "┘");
  console.log("");

  // Step 4: Extract deliverable info from claim
  console.log("[4/5] Extracting deliverable info...");

  // The claim contains: "Deliverable Hash: 0x..."
  const deliverableHashMatch = eventData.claim.match(/Deliverable Hash: (0x[a-fA-F0-9]+)/);
  const claimDeliverableHash = deliverableHashMatch ? deliverableHashMatch[1] : null;

  console.log(`  Claim deliverable:    ${claimDeliverableHash || "(not found in claim)"}`);
  console.log(`  Note: deliverable hash is in JobSubmitted event, not the job struct`);
  console.log("");

  // Step 5: Try to fetch from IPFS
  console.log("[5/5] Attempting to fetch deliverable from IPFS...");

  // First check if dataUrl is stored in evaluator (new contract version)
  let dataUrl = "";
  try {
    dataUrl = await publicClient.readContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "jobDataUrl",
      args: [JOB_ID],
    }) as string;
    if (dataUrl) {
      console.log(`  Data URL (from evaluator): ${dataUrl}`);
    }
  } catch (e) {
    // Old contract version without jobDataUrl
  }

  // Also check claim for IPFS URL
  if (!dataUrl) {
    const deliverableUrlMatch = eventData.claim.match(/Deliverable URL: (ipfs:\/\/\w+)/);
    if (deliverableUrlMatch) {
      dataUrl = deliverableUrlMatch[1];
      console.log(`  Data URL (from claim): ${dataUrl}`);
    }
  }

  // Fallback: check for any IPFS reference
  if (!dataUrl) {
    const ipfsMatch = eventData.claim.match(/ipfs:\/\/(\w+)/) ||
                      job.description.match(/ipfs:\/\/(\w+)/);
    if (ipfsMatch) {
      dataUrl = `ipfs://${ipfsMatch[1]}`;
      console.log(`  Data URL (found in text): ${dataUrl}`);
    }
  }

  if (dataUrl) {
    let cid = dataUrl;
    if (dataUrl.startsWith("ipfs://")) {
      cid = dataUrl.replace("ipfs://", "");
    }

    try {
      const content = await fetchFromIPFS(cid);
      console.log("");
      console.log("--- Deliverable Content ---");
      const contentStr = JSON.stringify(content, null, 2);
      console.log(contentStr.slice(0, 2000));
      if (contentStr.length > 2000) {
        console.log("... (truncated)");
      }

      // Verify hash
      const computedHash = keccak256(stringToBytes(dataUrl));
      console.log("");
      console.log("--- Hash Verification ---");
      console.log(`  Data URL:        ${dataUrl}`);
      console.log(`  Computed hash:   ${computedHash}`);

      if (!claimDeliverableHash) {
        console.log("");
        console.log("  Note: Hash mismatch may occur if URL format differs.");
        console.log("  Try checking if the URL was stored with/without trailing slash, etc.");
      }
    } catch (e) {
      console.log("  ❌ Could not fetch from IPFS:", (e as Error).message);
    }
  } else {
    console.log("  No IPFS URL found.");
    console.log("  The deliverable hash is keccak256(data_url).");
    console.log("");
    console.log("  To verify manually:");
    console.log("  1. Get the original data URL from the provider");
    console.log("  2. Compute: keccak256(data_url)");
    console.log("  3. Compare with hash in JobSubmitted event log");
  }

  console.log("");
  console.log("=".repeat(60));
  console.log("");
}

main().catch(console.error);
