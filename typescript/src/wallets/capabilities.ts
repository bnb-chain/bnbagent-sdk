/**
 * Wallet capability registry — an open set of string constants.
 *
 * A capability is a routing-relevant "can this wallet do X" bit, consumed by
 * {@link WalletProvider.capabilities} / {@link WalletProvider.supports} to
 * pick a path (which executor, which x402 flow, which tools enter the LLM's
 * list). Behavioral variation within a supported path (e.g.
 * `fundBundlesApproval`) is **not** a capability — it stays a plain provider
 * attribute.
 *
 * Rules of the registry (design doc §3.4), ported from
 * `python/bnbagent/wallets/capabilities.py`:
 *
 * - **Open set.** These are plain strings, not an enum. Third parties may
 *   add vendor-namespaced values (`"acme.batch_sign"`) without touching the
 *   core.
 * - **Unknown ⇒ ignore, absent ⇒ unsupported.** Consumers MUST ignore
 *   capability values they do not recognise and MUST treat the absence of a
 *   value as "not supported" (the EIP-5792 omission rule). Never probe by
 *   calling and catching.
 * - **`sign.*` values are auto-derived** from method overrides by the base
 *   {@link WalletProvider.capabilities} (declaration cannot drift from
 *   behavior). Corollary: never override a `sign*` method just to raise —
 *   the base default already raises a descriptive
 *   {@link UnsupportedWalletOperation}, and an override-to-raise would
 *   falsely claim the capability. Non-`sign.*` capabilities are declared via
 *   `extraCapabilities`.
 */

/** EIP-191 personal-sign (`signMessage`). Auto-derived. */
export const SIGN_MESSAGE = "sign.message";

/**
 * Raw transaction signing (`signTransaction`). Auto-derived; the
 * prerequisite for the default `LocalExecutor` path.
 */
export const SIGN_TRANSACTION = "sign.transaction";

/**
 * EIP-712 typed-data signing (`signTypedData`). Auto-derived; the
 * prerequisite for `X402Signer`.
 */
export const SIGN_TYPED_DATA = "sign.typed_data";

/** Arbitrary mechanical contract calls (vs. a fixed command menu). */
export const CALLS_ARBITRARY = "calls.arbitrary";

/** The wallet broadcasts its own transactions (it is its own executor). */
export const BROADCAST_SELF = "broadcast.self";

/** Executes ERC-8004 identity intents natively. */
export const INTENTS_ERC8004 = "intents.erc8004";

/** Executes ERC-8183 job intents natively. */
export const INTENTS_ERC8183 = "intents.erc8183";

/**
 * The SDK can complete an x402 payment with this wallet (locally signed or
 * fully delegated to the wallet backend).
 */
export const X402_PAY = "x402.pay";

/** Transactions can be sponsored via a paymaster (MegaFuel) broadcast. */
export const PAYMASTER_SPONSOR = "paymaster.sponsor";
