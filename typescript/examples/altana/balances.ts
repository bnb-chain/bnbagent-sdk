/**
 * Read-only demo of `AltanaWalletProvider.balances({ tokens })` on
 * Altana's OFFICIAL BSC-testnet stack (`network: "bnb-testnet"`, SDK >=
 * 0.5.0): native + ERC-20 balances via the vendor SDK. Amount semantics:
 * `raw` is the on-chain value transfers use; `display` is vendor-formatted
 * for humans (the vendor applies BEP-677 display scaling internally when a
 * token uses it — this SDK treats that as vendor behavior and passes it
 * through).
 *
 * Pure reads over the public RPC — no relay round-trip, no fees. Any
 * key works; an unfunded one just prints zeros.
 *
 * Usage:
 *     PRIVATE_KEY=0x... pnpm -C typescript exec tsx examples/altana/balances.ts
 */

import { formatEther } from "viem";
import { generatePrivateKey } from "viem/accounts";
import { loadEnv } from "../../src/core/env.js";
import { getEnv } from "../../src/core/envUtil.js";
import { AltanaWalletProvider } from "../../src/wallets/index.js";
import { U_TESTNET } from "./testnet.js";

loadEnv();

async function main(): Promise<void> {
  const privateKey = (getEnv("PRIVATE_KEY") ??
    generatePrivateKey()) as `0x${string}`;
  const provider = new AltanaWalletProvider({
    privateKey,
    network: "bnb-testnet",
  });
  console.log(`wallet: ${provider.address} (official testnet stack)`);

  const result = await provider.balances({ tokens: [U_TESTNET] });
  console.log(`native: ${formatEther(result.native)} tBNB`);
  for (const token of result.tokens ?? []) {
    if (!token.ok) {
      console.log(`${token.address}: read failed — ${token.error}`);
      continue;
    }
    console.log(
      `${token.symbol || token.address}: display=${token.display} raw=${token.raw}`,
    );
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
