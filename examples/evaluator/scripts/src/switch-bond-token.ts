/**
 * Switch APEX Evaluator bond token from TUSD to U Token
 *
 * Steps:
 * 1. Withdraw all TUSD balance
 * 2. Set bond token to U
 * 3. Deposit U tokens
 */

import { parseUnits, formatUnits } from "viem";
import { publicClient, getWalletClient, APEX_EVALUATOR_ADDRESS } from "./config";

const U_TOKEN = "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565" as `0x${string}`;
const DEPOSIT_AMOUNT = parseUnits("10", 18); // Deposit 10 U tokens

const ERC20_ABI = [
  { name: "approve", type: "function", inputs: [{ name: "spender", type: "address" }, { name: "amount", type: "uint256" }], outputs: [{ type: "bool" }], stateMutability: "nonpayable" },
  { name: "balanceOf", type: "function", inputs: [{ name: "account", type: "address" }], outputs: [{ type: "uint256" }], stateMutability: "view" },
  { name: "symbol", type: "function", inputs: [], outputs: [{ type: "string" }], stateMutability: "view" },
] as const;

const EVALUATOR_ABI = [
  { name: "bondToken", type: "function", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" },
  { name: "bondBalance", type: "function", inputs: [], outputs: [{ type: "uint256" }], stateMutability: "view" },
  { name: "getMinimumBond", type: "function", inputs: [], outputs: [{ type: "uint256" }], stateMutability: "view" },
  { name: "withdrawBond", type: "function", inputs: [{ name: "amount", type: "uint256" }], outputs: [], stateMutability: "nonpayable" },
  { name: "setBondToken", type: "function", inputs: [{ name: "newBondToken", type: "address" }], outputs: [], stateMutability: "nonpayable" },
  { name: "depositBond", type: "function", inputs: [{ name: "amount", type: "uint256" }], outputs: [], stateMutability: "nonpayable" },
  { name: "owner", type: "function", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" },
] as const;

async function main() {
  const walletClient = getWalletClient();
  const account = walletClient.account!;
  
  console.log("============================================================");
  console.log("Switch APEX Evaluator Bond Token: TUSD → U");
  console.log("============================================================");
  console.log("");
  console.log("Wallet:", account.address);
  console.log("APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("");

  // Check ownership
  const owner = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "owner",
  });
  
  if (owner.toLowerCase() !== account.address.toLowerCase()) {
    console.error(`❌ Error: You are not the owner of APEX Evaluator`);
    console.error(`   Owner: ${owner}`);
    console.error(`   Your wallet: ${account.address}`);
    return;
  }
  console.log("✅ Owner check passed");

  // Get current state
  const currentBondToken = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "bondToken",
  });

  const currentBalance = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "bondBalance",
  });

  const currentSymbol = await publicClient.readContract({
    address: currentBondToken,
    abi: ERC20_ABI,
    functionName: "symbol",
  });

  console.log(`Current Bond Token: ${currentSymbol} (${currentBondToken})`);
  console.log(`Current Balance: ${formatUnits(currentBalance, 18)} ${currentSymbol}`);
  console.log("");

  if (currentBondToken.toLowerCase() === U_TOKEN.toLowerCase()) {
    console.log("✅ Already using U Token as bond token!");
    return;
  }

  // Step 1: Withdraw all TUSD
  if (currentBalance > 0n) {
    console.log(`Step 1: Withdrawing ${formatUnits(currentBalance, 18)} ${currentSymbol}...`);
    
    const withdrawHash = await walletClient.writeContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "withdrawBond",
      args: [currentBalance],
    });
    
    console.log(`   TX: ${withdrawHash}`);
    const withdrawReceipt = await publicClient.waitForTransactionReceipt({ hash: withdrawHash });
    console.log(`   Status: ${withdrawReceipt.status === "success" ? "✅ Success" : "❌ Failed"}`);
    console.log("");
  } else {
    console.log("Step 1: No balance to withdraw, skipping...");
    console.log("");
  }

  // Step 2: Set bond token to U
  console.log(`Step 2: Setting bond token to U (${U_TOKEN})...`);
  
  const setBondTokenHash = await walletClient.writeContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "setBondToken",
    args: [U_TOKEN],
  });
  
  console.log(`   TX: ${setBondTokenHash}`);
  const setBondTokenReceipt = await publicClient.waitForTransactionReceipt({ hash: setBondTokenHash });
  console.log(`   Status: ${setBondTokenReceipt.status === "success" ? "✅ Success" : "❌ Failed"}`);
  console.log("");

  // Step 3: Check U token balance and deposit
  const uBalance = await publicClient.readContract({
    address: U_TOKEN,
    abi: ERC20_ABI,
    functionName: "balanceOf",
    args: [account.address],
  });

  console.log(`Step 3: Depositing U tokens...`);
  console.log(`   Your U balance: ${formatUnits(uBalance, 18)} U`);

  if (uBalance < DEPOSIT_AMOUNT) {
    console.log(`   ⚠️ Insufficient U balance. Need at least ${formatUnits(DEPOSIT_AMOUNT, 18)} U`);
    console.log(`   Skipping deposit. You can deposit later with:`);
    console.log(`   npx tsx src/deposit-bond.ts <amount>`);
    console.log("");
  } else {
    // Approve
    console.log(`   Approving ${formatUnits(DEPOSIT_AMOUNT, 18)} U...`);
    const approveHash = await walletClient.writeContract({
      address: U_TOKEN,
      abi: ERC20_ABI,
      functionName: "approve",
      args: [APEX_EVALUATOR_ADDRESS, DEPOSIT_AMOUNT],
    });
    await publicClient.waitForTransactionReceipt({ hash: approveHash });
    console.log(`   Approved: ${approveHash}`);

    // Deposit
    console.log(`   Depositing ${formatUnits(DEPOSIT_AMOUNT, 18)} U...`);
    const depositHash = await walletClient.writeContract({
      address: APEX_EVALUATOR_ADDRESS,
      abi: EVALUATOR_ABI,
      functionName: "depositBond",
      args: [DEPOSIT_AMOUNT],
    });
    
    console.log(`   TX: ${depositHash}`);
    const depositReceipt = await publicClient.waitForTransactionReceipt({ hash: depositHash });
    console.log(`   Status: ${depositReceipt.status === "success" ? "✅ Success" : "❌ Failed"}`);
    console.log("");
  }

  // Final state
  console.log("============================================================");
  console.log("Final State");
  console.log("============================================================");
  
  const newBondToken = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "bondToken",
  });

  const newBalance = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "bondBalance",
  });

  const newMinBond = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: EVALUATOR_ABI,
    functionName: "getMinimumBond",
  });

  const newSymbol = await publicClient.readContract({
    address: newBondToken,
    abi: ERC20_ABI,
    functionName: "symbol",
  });

  console.log(`Bond Token: ${newSymbol} (${newBondToken})`);
  console.log(`Min Bond per Assertion: ${formatUnits(newMinBond, 18)} ${newSymbol}`);
  console.log(`Current Balance: ${formatUnits(newBalance, 18)} ${newSymbol}`);
  
  const balanceNum = Number(formatUnits(newBalance, 18));
  const bondNum = Number(formatUnits(newMinBond, 18));
  console.log(`Can create: ${Math.floor(balanceNum / bondNum)} assertions`);
}

main().catch(console.error);
