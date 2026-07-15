/**
 * Altana BSC-testnet (97) network configs — LEGACY stack.
 *
 * `@altananetwork/sdk` 0.5.0 ships an official testnet deployment as the
 * `BNB_TESTNET` export (new KeyStore addresses, relay
 * relay-testnet.altana.network), reachable in `src/` as the
 * `network: "bnb-testnet"` preset — use that for anything new. This file
 * keeps the LEGACY deployment (relay.functor.sh, the pre-0.5.0 KeyStore)
 * that `./e2e.ts` still runs against: as of 2026-07-15 the official
 * testnet relay serves a mismatched TLS certificate (`*.up.railway.app`),
 * so no strict TLS client can execute through it — reported to Altana;
 * the E2E switches to the preset (and this file + `./shim.js` retire)
 * once that is fixed and 12/12 passes on the official stack. The legacy
 * KeyStore has one ABI drift (`getKeys` vs `getActiveKeys`), handled by
 * `./shim.js`; the official deployment answers `getKeys` natively (probed
 * on-chain 2026-07-15), so the shim dies with the legacy stack.
 * Field-verified 2026-07-10 and re-verified 2026-07-14 (12/12 E2E on SDK
 * 0.4.0).
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
