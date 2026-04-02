/**
 * Dispute an APEX job's UMA assertion during the challenge period
 * Usage: JOB_ID=14 npm run dispute-job
 *
 * Prerequisites:
 * - Job must be in "Submitted" status with assertion initiated
 * - Challenge period must not have expired
 * - Disputer must have enough tokens for bond (same amount as assertion bond)
 */
import { formatUnits } from "viem";
import { publicClient, getDisputeWalletClient, ERC8183_ADDRESS, APEX_EVALUATOR_ADDRESS, OOV3_ADDRESS } from "./config.js";

const JOB_ID = BigInt(process.argv[2] || process.env.JOB_ID || "0");
const U_ADDRESS = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565" as `0x${string}`;

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
] as const;

const OOV3_ABI = [
  {
    name: "disputeAssertion",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "assertionId", type: "bytes32" },
      { name: "disputer", type: "address" },
    ],
    outputs: [],
  },
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

const ERC20_ABI = [
  {
    name: "balanceOf",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ name: "", type: "uint256" }],
  },
  {
    name: "approve",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ name: "", type: "bool" }],
  },
  {
    name: "allowance",
    type: "function",
    stateMutability: "view",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ name: "", type: "uint256" }],
  },
  {
    name: "allocateTo",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "to", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [],
  },
] as const;

async function main() {
  if (JOB_ID === 0n) {
    console.error("Usage: JOB_ID=14 npm run dispute-job");
    console.error("   or: npm run dispute-job -- 14");
    process.exit(1);
  }

  const walletClient = getDisputeWalletClient();
  const disputer = walletClient.account.address;

  console.log("=".repeat(60));
  console.log("Dispute APEX Job Assertion");
  console.log("=".repeat(60));
  console.log("Job ID:", JOB_ID.toString());
  console.log("ERC-8183:", ERC8183_ADDRESS);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("OOv3:", OOV3_ADDRESS);
  console.log("Disputer:", disputer);
  console.log("");

  // Check job
  const job = await publicClient.readContract({
    address: ERC8183_ADDRESS,
    abi: ERC8183_ABI,
    functionName: "getJob",
    args: [JOB_ID],
  });

  if (job.status !== 2) {
    const STATUS_LABELS: Record<number, string> = {
      0: "Open", 1: "Funded", 2: "Submitted", 3: "Completed", 4: "Rejected", 5: "Expired",
    };
    console.error(`❌ Job is not in Submitted status (current: ${STATUS_LABELS[job.status] ?? job.status}).`);
    process.exit(1);
  }

  if (job.evaluator.toLowerCase() !== APEX_EVALUATOR_ADDRESS.toLowerCase()) {
    console.error("❌ Job does not use APEX Evaluator.");
    process.exit(1);
  }

  // Get assertion ID
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

  if (!initiated) {
    console.error("❌ Assertion not initiated.");
    process.exit(1);
  }

  console.log("Assertion ID:", assertionId);

  // Get assertion details from OOv3
  const assertion = await publicClient.readContract({
    address: OOV3_ADDRESS,
    abi: OOV3_ABI,
    functionName: "getAssertion",
    args: [assertionId],
  });

  if (assertion.settled) {
    console.error("❌ Assertion already settled.");
    process.exit(1);
  }

  const now = Math.floor(Date.now() / 1000);
  const expirationTime = Number(assertion.expirationTime);

  if (now >= expirationTime) {
    console.error("❌ Challenge period expired. Cannot dispute.");
    process.exit(1);
  }

  const remaining = expirationTime - now;
  console.log("Expiration:", new Date(expirationTime * 1000).toLocaleString());
  console.log("Time remaining:", `${Math.floor(remaining / 60)}m ${remaining % 60}s`);
  console.log("Bond required:", formatUnits(assertion.bond, 18), "U");
  console.log("");

  // Check disputer balance
  let balance = await publicClient.readContract({
    address: U_ADDRESS,
    abi: ERC20_ABI,
    functionName: "balanceOf",
    args: [disputer],
  });

  console.log("Disputer U balance:", formatUnits(balance, 18));

  if (balance < assertion.bond) {
    console.log("Insufficient balance, minting test tokens...");
    const mintAmount = assertion.bond * 2n;
    const mintHash = await walletClient.writeContract({
      address: U_ADDRESS,
      abi: ERC20_ABI,
      functionName: "allocateTo",
      args: [disputer, mintAmount],
    });
    await publicClient.waitForTransactionReceipt({ hash: mintHash });
    console.log("✓ Minted", formatUnits(mintAmount, 18), "U");

    balance = await publicClient.readContract({
      address: U_ADDRESS,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [disputer],
    });
  }

  // Check allowance
  const allowance = await publicClient.readContract({
    address: U_ADDRESS,
    abi: ERC20_ABI,
    functionName: "allowance",
    args: [disputer, OOV3_ADDRESS],
  });

  if (allowance < assertion.bond) {
    console.log("Approving OOv3...");
    const approveHash = await walletClient.writeContract({
      address: U_ADDRESS,
      abi: ERC20_ABI,
      functionName: "approve",
      args: [OOV3_ADDRESS, assertion.bond * 10n],
    });
    await publicClient.waitForTransactionReceipt({ hash: approveHash });
    console.log("✓ Approved");
  }

  // Dispute
  console.log("");
  console.log("Disputing assertion...");

  try {
    const { request } = await publicClient.simulateContract({
      address: OOV3_ADDRESS,
      abi: OOV3_ABI,
      functionName: "disputeAssertion",
      args: [assertionId, disputer],
      account: walletClient.account,
    });

    const txHash = await walletClient.writeContract(request);
    console.log("TX:", txHash);
    console.log("Waiting for confirmation...");

    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });
    console.log("Status:", receipt.status === "success" ? "✅ Disputed!" : "❌ Failed");

    if (receipt.status === "success") {
      console.log("");
      console.log("⚠️  Assertion disputed! It will go to UMA DVM for arbitration.");
      console.log("   The DVM will vote on whether the work satisfies requirements.");
    }
  } catch (e: any) {
    console.error("Error disputing:", e.shortMessage || e.message);
  }

  console.log("");
  console.log("=".repeat(60));
}

main().catch(console.error);
