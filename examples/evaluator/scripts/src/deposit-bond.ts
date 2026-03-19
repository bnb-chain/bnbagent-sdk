/**
 * Deposit bond to APEX Evaluator
 * Usage: AMOUNT=10 npm run deposit-bond
 */
import { formatUnits, parseUnits } from "viem";
import { publicClient, getWalletClient, APEX_EVALUATOR_ADDRESS } from "./config.js";

const U_ADDRESS = (process.env.PAYMENT_TOKEN_ADDRESS || "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565") as `0x${string}`;
const AMOUNT = parseUnits(process.env.AMOUNT || "10", 18);

const ERC20_ABI = [
  {
    inputs: [{ name: "account", type: "address" }],
    name: "balanceOf",
    outputs: [{ name: "", type: "uint256" }],
    stateMutability: "view",
    type: "function",
  },
  {
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    name: "approve",
    outputs: [{ name: "", type: "bool" }],
    stateMutability: "nonpayable",
    type: "function",
  },
  {
    inputs: [
      { name: "to", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    name: "allocateTo",
    outputs: [],
    stateMutability: "nonpayable",
    type: "function",
  },
] as const;

const EVALUATOR_ABI = [
  {
    inputs: [{ name: "amount", type: "uint256" }],
    name: "depositBond",
    outputs: [],
    stateMutability: "nonpayable",
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
  const walletClient = getWalletClient();
  const account = walletClient.account.address;

  console.log("=".repeat(60));
  console.log("Deposit Bond to APEX Evaluator");
  console.log("=".repeat(60));
  console.log("Account:", account);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("Amount to deposit:", formatUnits(AMOUNT, 18), "U");
  console.log("");

  // Check current balance
  let balance = await publicClient.readContract({
    address: U_ADDRESS,
    abi: ERC20_ABI,
    functionName: "balanceOf",
    args: [account],
  });
  console.log("Current U balance:", formatUnits(balance, 18));

  // If balance insufficient, mint test tokens
  if (balance < AMOUNT) {
    console.log("");
    console.log("Insufficient balance, minting test tokens...");
    const mintAmount = AMOUNT - balance + parseUnits("10", 18); // mint extra
    
    const mintHash = await walletClient.writeContract({
      address: U_ADDRESS,
      abi: ERC20_ABI,
      functionName: "allocateTo",
      args: [account, mintAmount],
    });
    console.log("Mint tx:", mintHash);
    await publicClient.waitForTransactionReceipt({ hash: mintHash });
    console.log("✓ Minted", formatUnits(mintAmount, 18), "U");

    balance = await publicClient.readContract({
      address: U_ADDRESS,
      abi: ERC20_ABI,
      functionName: "balanceOf",
      args: [account],
    });
    console.log("New balance:", formatUnits(balance, 18), "U");
  }

  // Approve
  console.log("");
  console.log("Approving APEX Evaluator...");
  const approveHash = await walletClient.writeContract({
    address: U_ADDRESS,
    abi: ERC20_ABI,
    functionName: "approve",
    args: [APEX_EVALUATOR_ADDRESS, AMOUNT],
  });
  console.log("Approve tx:", approveHash);
  await publicClient.waitForTransactionReceipt({ hash: approveHash });
  console.log("✓ Approved");

  // Deposit
  console.log("");
  console.log("Depositing bond...");
  const depositHash = await walletClient.writeContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "depositBond",
    args: [AMOUNT],
  });
  console.log("Deposit tx:", depositHash);
  await publicClient.waitForTransactionReceipt({ hash: depositHash });
  console.log("✓ Deposited", formatUnits(AMOUNT, 18), "U");

  // Verify
  console.log("");
  console.log("--- Verification ---");
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
  
  if (bondBalance >= minBond) {
    console.log("✓ Bond balance sufficient for assertions!");
  } else {
    console.log("⚠️ Still insufficient, deposit more.");
  }

  console.log("");
  console.log("=".repeat(60));
}

main().catch((err) => {
  console.error("Error:", err);
  process.exit(1);
});
