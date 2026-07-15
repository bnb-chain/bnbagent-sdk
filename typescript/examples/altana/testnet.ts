/**
 * Shared BSC-testnet (97) constants for the Altana examples.
 *
 * The examples run on Altana's OFFICIAL testnet stack — the SDK's
 * `BNB_TESTNET` export, i.e. `network: "bnb-testnet"` on the provider
 * (E2E-verified 12/12 on 2026-07-15; relay testnet-relay.altana.network
 * needs SDK >= 0.5.1). The legacy functor deployment (relay.functor.sh +
 * the `getKeys→getActiveKeys` shim) that used to live here is retired.
 */

/** United Stables testnet token — the ERC-8183 payment token on 97. */
export const U_TESTNET: `0x${string}` =
  "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565";
