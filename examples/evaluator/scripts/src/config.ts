import { createPublicClient, createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { bscTestnet } from "viem/chains";
import "dotenv/config";

export const OOV3_ADDRESS = (process.env.OOV3_ADDRESS || "0xFc5bb3e475cc9264760Cf33b1e9ea7B87942C709") as `0x${string}`;
export const ERC8183_ADDRESS = (process.env.ERC8183_ADDRESS || "0x0afef5748837ef7961e85db65daf97a4f92a00a8") as `0x${string}`;
export const APEX_EVALUATOR_ADDRESS = (process.env.APEX_EVALUATOR_ADDRESS || "0x549cb8bee93794b104c78d025aa5b24832a94984") as `0x${string}`;

export const RPC_URL = process.env.RPC_URL || "https://data-seed-prebsc-2-s2.binance.org:8545";

export const publicClient = createPublicClient({
  chain: bscTestnet,
  transport: http(RPC_URL),
});

export function getWalletClient() {
  const privateKey = process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("PRIVATE_KEY env var is required for write operations");
  }
  const account = privateKeyToAccount(privateKey as `0x${string}`);
  return createWalletClient({
    account,
    chain: bscTestnet,
    transport: http(RPC_URL),
  });
}

export function getDisputeWalletClient() {
  const privateKey = process.env.DISPUTE_PRIVATE_KEY || process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("DISPUTE_PRIVATE_KEY env var is required for dispute operations");
  }
  const account = privateKeyToAccount(privateKey as `0x${string}`);
  return createWalletClient({
    account,
    chain: bscTestnet,
    transport: http(RPC_URL),
  });
}

export function getSettleWalletClient() {
  const privateKey = process.env.SETTLE_PRIVATE_KEY || process.env.PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("SETTLE_PRIVATE_KEY or PRIVATE_KEY env var is required for settle operations");
  }
  const account = privateKeyToAccount(privateKey as `0x${string}`);
  return createWalletClient({
    account,
    chain: bscTestnet,
    transport: http(RPC_URL),
  });
}
