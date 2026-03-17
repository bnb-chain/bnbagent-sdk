import { formatEther, parseEther } from "viem";
import { publicClient, getWalletClient, APEX_EVALUATOR_ADDRESS } from "./config";

const abi = [
  { name: "bondBalance", type: "function", inputs: [], outputs: [{ type: "uint256" }], stateMutability: "view" },
  { name: "withdrawBond", type: "function", inputs: [{ name: "amount", type: "uint256" }], outputs: [], stateMutability: "nonpayable" },
  { name: "owner", type: "function", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" },
] as const;

async function main() {
  const addr = APEX_EVALUATOR_ADDRESS;
  const walletClient = getWalletClient();
  
  console.log("Withdraw Bond from APEX Evaluator");
  console.log("=".repeat(50));
  console.log("APEX Evaluator:", addr);
  console.log("Caller:", walletClient.account.address);
  
  // Check owner
  const owner = await publicClient.readContract({ address: addr, abi, functionName: "owner" });
  console.log("Owner:", owner);
  
  if (owner.toLowerCase() !== walletClient.account.address.toLowerCase()) {
    throw new Error(`Caller is not owner. Owner: ${owner}`);
  }
  
  // Check balance
  const balance = await publicClient.readContract({ address: addr, abi, functionName: "bondBalance" });
  console.log("Bond Balance:", formatEther(balance), "tokens");
  
  if (balance === 0n) {
    console.log("\n✅ Bond balance is already 0. No withdrawal needed.");
    return;
  }
  
  // Withdraw all
  console.log("\nWithdrawing all bond...");
  const hash = await walletClient.writeContract({
    address: addr,
    abi,
    functionName: "withdrawBond",
    args: [balance],
  });
  
  console.log("Tx hash:", hash);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  console.log("Status:", receipt.status);
  
  // Verify
  const newBalance = await publicClient.readContract({ address: addr, abi, functionName: "bondBalance" });
  console.log("\nNew Bond Balance:", formatEther(newBalance), "tokens");
  console.log(newBalance === 0n ? "✅ Ready for upgrade!" : "❌ Still has balance");
}

main().catch(console.error);
