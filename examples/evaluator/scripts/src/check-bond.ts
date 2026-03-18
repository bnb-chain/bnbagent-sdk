import { formatEther } from "viem";
import { publicClient, APEX_EVALUATOR_ADDRESS } from "./config";

const abi = [
  { name: "bondBalance", type: "function", inputs: [], outputs: [{ type: "uint256" }], stateMutability: "view" },
  { name: "bondToken", type: "function", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" },
  { name: "owner", type: "function", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" },
] as const;

async function main() {
  const addr = APEX_EVALUATOR_ADDRESS;
  
  const [balance, token, owner] = await Promise.all([
    publicClient.readContract({ address: addr, abi, functionName: "bondBalance" }),
    publicClient.readContract({ address: addr, abi, functionName: "bondToken" }),
    publicClient.readContract({ address: addr, abi, functionName: "owner" }),
  ]);
  
  console.log("APEX Evaluator:", addr);
  console.log("Bond Token:", token);
  console.log("Bond Balance:", formatEther(balance), "tokens");
  console.log("Owner:", owner);
  
  if (balance > 0n) {
    console.log("\n⚠️  Bond balance is not 0. Need to withdraw before upgrading.");
  } else {
    console.log("\n✅ Bond balance is 0. Ready for upgrade.");
  }
}

main().catch(console.error);
