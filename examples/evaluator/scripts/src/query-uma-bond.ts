import { formatUnits } from "viem";
import { publicClient, OOV3_ADDRESS, APEX_EVALUATOR_ADDRESS } from "./config";

const PAYMENT_TOKEN_ADDRESS = (process.env.PAYMENT_TOKEN_ADDRESS || "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565") as `0x${string}`;

async function main() {
  console.log("============================================================");
  console.log("UMA OOv3 Bond Configuration (BSC Testnet)");
  console.log("============================================================");
  console.log("");

  // Query OOv3 minimum bond for payment token (U Token)
  const minBondU = await publicClient.readContract({
    address: OOV3_ADDRESS,
    abi: [{
      name: "getMinimumBond",
      type: "function",
      inputs: [{ name: "currency", type: "address" }],
      outputs: [{ type: "uint256" }],
      stateMutability: "view",
    }],
    functionName: "getMinimumBond",
    args: [PAYMENT_TOKEN_ADDRESS],
  });

  // Query APEX Evaluator bond token
  const bondToken = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: [{
      name: "bondToken",
      type: "function",
      inputs: [],
      outputs: [{ type: "address" }],
      stateMutability: "view",
    }],
    functionName: "bondToken",
  });

  // Query APEX Evaluator getMinimumBond (queries OOv3 internally)
  const evaluatorMinBond = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: [{
      name: "getMinimumBond",
      type: "function",
      inputs: [],
      outputs: [{ type: "uint256" }],
      stateMutability: "view",
    }],
    functionName: "getMinimumBond",
  });

  const bondBalance = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: [{
      name: "bondBalance",
      type: "function",
      inputs: [],
      outputs: [{ type: "uint256" }],
      stateMutability: "view",
    }],
    functionName: "bondBalance",
  });

  const liveness = await publicClient.readContract({
    address: APEX_EVALUATOR_ADDRESS,
    abi: [{
      name: "liveness",
      type: "function",
      inputs: [],
      outputs: [{ type: "uint64" }],
      stateMutability: "view",
    }],
    functionName: "liveness",
  });

  // Get token info
  const symbol = await publicClient.readContract({
    address: bondToken as `0x${string}`,
    abi: [{
      name: "symbol",
      type: "function",
      inputs: [],
      outputs: [{ type: "string" }],
      stateMutability: "view",
    }],
    functionName: "symbol",
  });

  const decimals = await publicClient.readContract({
    address: bondToken as `0x${string}`,
    abi: [{
      name: "decimals",
      type: "function",
      inputs: [],
      outputs: [{ type: "uint8" }],
      stateMutability: "view",
    }],
    functionName: "decimals",
  });

  const tokenName = await publicClient.readContract({
    address: bondToken as `0x${string}`,
    abi: [{
      name: "name",
      type: "function",
      inputs: [],
      outputs: [{ type: "string" }],
      stateMutability: "view",
    }],
    functionName: "name",
  });

  // Check some common tokens minimum bond on UMA OOv3
  const commonTokens = [
    { symbol: "U (Payment Token)", address: PAYMENT_TOKEN_ADDRESS },
    { symbol: "USDT", address: "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd" as `0x${string}` },
    { symbol: "USDC", address: "0x64544969ed7EBf5f083679233325356EbE738930" as `0x${string}` },
    { symbol: "BUSD", address: "0xeD24FC36d5Ee211Ea25A80239Fb8C4Cfd80f12Ee" as `0x${string}` },
  ];

  console.log("Contracts:");
  console.log("  UMA OOv3:", OOV3_ADDRESS);
  console.log("  APEX Evaluator:", APEX_EVALUATOR_ADDRESS);
  console.log("");
  
  console.log("--- Current Bond Token (APEX Evaluator) ---");
  console.log("  Address:", bondToken);
  console.log("  Name:", tokenName);
  console.log("  Symbol:", symbol);
  console.log("  Decimals:", decimals);
  console.log("");
  
  console.log("--- Minimum Bond Requirements ---");
  console.log("  APEX Evaluator getMinimumBond():", formatUnits(evaluatorMinBond as bigint, decimals as number), symbol);
  console.log("  (This is the bond required per assertion)");
  console.log("");

  console.log("--- UMA OOv3 Supported Tokens & Min Bond ---");
  for (const token of commonTokens) {
    try {
      const minBond = await publicClient.readContract({
        address: OOV3_ADDRESS,
        abi: [{
          name: "getMinimumBond",
          type: "function",
          inputs: [{ name: "currency", type: "address" }],
          outputs: [{ type: "uint256" }],
          stateMutability: "view",
        }],
        functionName: "getMinimumBond",
        args: [token.address],
      });
      
      if (minBond > 0n) {
        console.log(`  ${token.symbol}: ${formatUnits(minBond as bigint, 18)} tokens (min bond)`);
        console.log(`     Address: ${token.address}`);
      } else {
        console.log(`  ${token.symbol}: Not supported (min bond = 0)`);
      }
    } catch (e) {
      console.log(`  ${token.symbol}: Error querying (may not be supported)`);
    }
  }
  console.log("");
  
  console.log("--- APEX Evaluator State ---");
  console.log("  Current Bond Balance:", formatUnits(bondBalance as bigint, decimals as number), symbol);
  console.log("  Liveness Period:", Number(liveness) / 60, "minutes");
  console.log("");
  
  console.log("--- Summary ---");
  const balanceNum = Number(formatUnits(bondBalance as bigint, decimals as number));
  const bondNum = Number(formatUnits(evaluatorMinBond as bigint, decimals as number));
  const maxAssertions = bondNum > 0 ? Math.floor(balanceNum / bondNum) : 0;
  console.log(`  Minimum Bond per Assertion: ${bondNum} ${symbol}`);
  console.log(`  Current Balance: ${balanceNum} ${symbol}`);
  console.log(`  Can create ${maxAssertions} assertions with current balance`);
  
  if (balanceNum < bondNum) {
    console.log("");
    console.log("  ⚠️  WARNING: Insufficient bond balance!");
    console.log(`     Need to deposit at least ${bondNum - balanceNum} ${symbol} more`);
    console.log(`     Run: npx tsx src/deposit-bond.ts ${bondNum}`);
  }
}

main().catch(console.error);
