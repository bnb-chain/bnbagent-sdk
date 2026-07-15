/**
 * BSC-testnet (97) Altana network config — LEGACY Functor deployment.
 *
 * ⚠️ Deliberately outside `src/`: the official Altana stack has no testnet
 * today (the SDK exports mainnets only; relay.altana.network does not
 * serve 97). What still works on 97 is the pre-rename Functor
 * infrastructure — old KeyStore/Controller contracts plus the old
 * relay.functor.sh, all field-verified 2026-07-10 — with exactly one ABI
 * drift handled by `./shim.js`. This may disappear whenever the legacy
 * relay is retired; the mainnet path in `src/` is unaffected. An official
 * new testnet deployment has been requested from the Altana team.
 *
 * Addresses match agent-verify-demo `src/08-altana.js` (the reference run).
 */

import { bscTestnet } from "viem/chains";
import type { AltanaNetworkConfig } from "../../src/wallets/index.js";

/** United Stables testnet token — the ERC-8183 payment token on 97. */
export const U_TESTNET: `0x${string}` =
  "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565";

/**
 * The legacy testnet deployment. `publicRpcUrl` here points at a public
 * node; for real use pass this through {@link makeAltanaTestnetConfig}
 * with the shim's URL so `getKeys` reads work.
 */
export const ALTANA_BSC_TESTNET: AltanaNetworkConfig = {
  chain: bscTestnet,
  chainId: 97,
  keyStore: "0x2F77991da4a66D1EE83f0622a4e6A2E94c89BCbE",
  keyStoreController: "0x30b34f10F0a271dAFe6a0A900bCB2Cb94927e39d",
  publicRpcUrl: "https://bsc-testnet-rpc.publicnode.com",
  explorer: "https://testnet.bscscan.com",
  relayUrl: "https://relay.functor.sh",
};

/**
 * The testnet config with `publicRpcUrl` swapped for the shim (or any
 * custom RPC). Everything else — contracts, relay — stays the legacy
 * deployment.
 */
export function makeAltanaTestnetConfig(opts: {
  publicRpcUrl: string;
}): AltanaNetworkConfig {
  return { ...ALTANA_BSC_TESTNET, publicRpcUrl: opts.publicRpcUrl };
}
