/**
 * Structural mirrors of the `@altananetwork/sdk` public types.
 *
 * The Altana SDK is an **optional** peer dependency (GPL-3.0-or-later,
 * ESM-only), so nothing in `src/` may import from it at the type level —
 * otherwise the generated `.d.ts` bundle would carry
 * `import("@altananetwork/sdk")` references that break consumers who never
 * installed the peer. These mirrors keep our public declaration files
 * self-contained; `tests/altanaTypeCompat.test.ts` pins mutual
 * assignability against the real package (a devDependency) so any drift
 * fails `pnpm typecheck` instead of surfacing at runtime.
 *
 * Mirrored from `@altananetwork/sdk@0.3.3` `dist/*.d.ts` (config, client,
 * internal/{signer,sessions,types,relay}).
 */

import type { Chain } from "viem";

/** Discriminator for the curve/ceremony backing an {@link AltanaSigner}. */
export type AltanaSignerType = "privateKey" | "injected" | "passkey";

/**
 * The signer every Altana SDK function accepts: an address, a public key,
 * and the ability to sign arbitrary 32-byte digests. Mirrors
 * `@altananetwork/sdk`'s `Signer`.
 */
export interface AltanaSigner {
  type: AltanaSignerType;
  address: `0x${string}`;
  publicKey: `0x${string}`;
  signDigest(digest: `0x${string}`): Promise<`0x${string}`>;
}

/**
 * A single allowed-call rule (AND semantics between the fields). Mirrors
 * `@altananetwork/sdk`'s `CallPermission` — the exact three-way union
 * matters: a bare `{}` must not satisfy the type.
 */
export type AltanaCallPermission =
  | { signature: string; to: `0x${string}` }
  | { signature: string }
  | { to: `0x${string}` };

/**
 * A spending cap for a token over a rolling period. Mirrors
 * `@altananetwork/sdk`'s `SpendPermission`. `limit` is the ONLY bigint in
 * the whole session structure (load-bearing for the byte-exact session
 * serde in `./session.js`).
 */
export interface AltanaSpendPermission {
  limit: bigint;
  period: "minute" | "hour" | "day" | "week" | "month" | "year";
  /** Omit for the native token (BNB). */
  token?: `0x${string}`;
}

/** Mirrors `@altananetwork/sdk`'s `SessionPermissions`. */
export interface AltanaSessionPermissions {
  /** Allowed calls. If omitted, ALL targets are allowed — use carefully. */
  calls?: readonly AltanaCallPermission[];
  /** Per-token spending caps. */
  spend?: readonly AltanaSpendPermission[];
}

/**
 * A live session — the result of `grantSession`. Mirrors
 * `@altananetwork/sdk`'s `Session`.
 *
 * The on-chain account hash-commits `permissions` + `expiry` + role +
 * `publicKey` byte-exactly at grant time, so a persisted session MUST
 * round-trip without any normalization (no key reordering, no
 * bigint→number). Use `serializeSession`/`deserializeSession` from
 * `./session.js` — never hand-roll JSON for this object.
 */
export interface AltanaSession {
  /** The wallet this session can act on. */
  walletAddress: `0x${string}`;
  /** The session key's signer. The agent signs with this. */
  signer: AltanaSigner;
  /** Public key registered on-chain. Identifier for revocation. */
  publicKey: `0x${string}`;
  /** Granted permissions (enforced on-chain). */
  permissions: AltanaSessionPermissions;
  /** Unix epoch seconds when this session expires. */
  expiry: number;
}

/**
 * Per-network Altana deployment config. Mirrors `@altananetwork/sdk`'s
 * `NetworkConfig` — the SDK performs no whitelist validation, so a custom
 * config (e.g. the legacy BSC-testnet stack in `examples/altana/`) is
 * first-class.
 */
export interface AltanaNetworkConfig {
  chain: Chain;
  chainId: number;
  keyStore: `0x${string}`;
  keyStoreController: `0x${string}`;
  /** Public RPC URL for reads. */
  publicRpcUrl: string;
  /** Block explorer base URL. */
  explorer: string;
  /** Altana relay endpoint. */
  relayUrl: string;
}

/**
 * Network selector accepted by `AltanaWalletProvider`: the `"bnb-mainnet"`
 * preset (resolved to the SDK's own `BNB` export at first use — no address
 * copies to drift) or a fully explicit {@link AltanaNetworkConfig}.
 */
export type AltanaNetwork = "bnb-mainnet" | AltanaNetworkConfig;

/** A wallet handle — a pure `{ address }` value. Mirrors the SDK's `Wallet`. */
export interface AltanaWallet {
  address: `0x${string}`;
}

/** A single relay call. Mirrors `@altananetwork/sdk`'s `Call`. */
export interface AltanaCall {
  to: `0x${string}`;
  value?: bigint;
  data?: `0x${string}`;
}

/**
 * Result of a relay `execute`. Mirrors `@altananetwork/sdk`'s
 * `ExecuteResult` — note there is NO receipt; the integration fetches one
 * itself from `transactionHash` via the execution context's PublicClient.
 */
export interface AltanaExecuteResult {
  callsId: `0x${string}`;
  transactionHash?: `0x${string}`;
  status: "PENDING" | "CONFIRMED" | "FAILED";
}

// ── Internal SDK-module shapes (deliberately NOT exported from the barrel) ──
// These type only the subset of the SDK the provider actually calls, so the
// mirror surface stays small. Method syntax (not arrow properties) is
// intentional: parameter bivariance is what lets the real, wider SDK types
// satisfy these mirrors.

/** Admin-path execute options (mirror of one arm of `ClientExecuteOptions`). */
export interface AltanaAdminExecuteOptions {
  wallet: AltanaWallet;
  signer: AltanaSigner;
  calls: AltanaCall | readonly AltanaCall[];
  feeToken?: `0x${string}`;
  noWait?: boolean;
  chainId?: number;
}

/** Session-path execute options (mirror of the other arm). */
export interface AltanaSessionExecuteOptions {
  session: AltanaSession;
  calls: AltanaCall | readonly AltanaCall[];
  feeToken?: `0x${string}`;
  noWait?: boolean;
  chainId?: number;
}

/** Mirror of `ClientGrantSessionOptions`. */
export interface AltanaGrantSessionOptions {
  wallet: AltanaWallet;
  signer: AltanaSigner;
  permissions: AltanaSessionPermissions;
  /** Unix epoch seconds. */
  expiry: number;
  /** Omit to let the SDK generate a fresh secp256k1 session signer. */
  sessionSigner?: AltanaSigner;
  feeToken?: `0x${string}`;
  chainId?: number;
}

/** Mirror of `ClientRevokeSessionOptions`. */
export interface AltanaRevokeSessionOptions {
  wallet: AltanaWallet;
  signer: AltanaSigner;
  session: AltanaSession | `0x${string}`;
  feeToken?: `0x${string}`;
  chainId?: number;
}

/** The subset of the SDK `Client` the provider uses. */
export interface AltanaSdkClient {
  createWallet(opts?: {
    signer?: AltanaSigner;
  }): Promise<AltanaWallet & { signer: AltanaSigner }>;
  execute(
    opts: AltanaAdminExecuteOptions | AltanaSessionExecuteOptions,
  ): Promise<AltanaExecuteResult>;
  grantSession(opts: AltanaGrantSessionOptions): Promise<AltanaSession>;
  revokeSession(opts: AltanaRevokeSessionOptions): Promise<AltanaExecuteResult>;
}

/** The subset of the `@altananetwork/sdk` module surface the provider uses. */
export interface AltanaSdkModule {
  createClient(opts: {
    chains: AltanaNetworkConfig[];
    defaultChainId?: number;
  }): AltanaSdkClient;
  signerFromPrivateKey(privateKey: `0x${string}`): AltanaSigner;
  /** The SDK's own BNB-mainnet deployment config. */
  BNB: AltanaNetworkConfig;
}

// ── x402 surface (`@altananetwork/sdk` >= 0.4.0) ──────────────────────────
// Mirrored from the shipped 0.4.0 `dist/{client,x402}.d.ts` and pinned by
// `tests/altanaTypeCompat.test.ts`. One asymmetry vs the client methods:
// `signX402Payment` is a MODULE-level function with positional parameters
// `(session, requirement, opts?)` — chain-independent, no chainId. At
// runtime the provider duck-checks this surface and raises an upgrade
// error on pre-0.4.0 installs (see `provider.ts` `#x402Sdk`).

/**
 * Mirror of `ClientApproveSignatureCheckerOptions` (docs). Approving a
 * checker executes a relay self-call to
 * `setSignatureCheckerApproval(sessionKeyHash, checker, isApproved)` — the
 * session's ERC-1271 `isValidSignature` only answers callers approved here.
 */
export interface AltanaApproveSignatureCheckerOptions {
  wallet: AltanaWallet;
  signer: AltanaSigner;
  session: AltanaSession;
  checker: `0x${string}`;
  feeToken?: `0x${string}`;
  chainId?: number;
}

/** Options for the documented `signOrderTypedData` (EIP-712 in, hashed by the SDK). */
export interface AltanaSignOrderTypedDataOptions {
  session: AltanaSession;
  /** viem `TypedDataDefinition`-shaped payload. */
  typedData: Record<string, unknown>;
}

/** Result of `signX402Payment`: the complete `X-PAYMENT` header value. */
export interface AltanaSignX402PaymentResult {
  header: string;
  payload?: unknown;
}

/** Mirror of `ClientApproveTokenForPermit2Options` (0.4.0). */
export interface AltanaApproveTokenForPermit2Options {
  wallet: AltanaWallet;
  signer: AltanaSigner;
  token: `0x${string}`;
  /** Omit for unlimited; SET IT — the allowance is the on-chain x402 ceiling. */
  amount?: bigint;
  feeToken?: `0x${string}`;
  chainId?: number;
}

/** The x402 client methods added in `@altananetwork/sdk` >= 0.4.0. */
export interface AltanaSdkClientX402 {
  signOrderTypedData(
    opts: AltanaSignOrderTypedDataOptions,
  ): Promise<`0x${string}`>;
  approveTokenForPermit2(
    opts: AltanaApproveTokenForPermit2Options,
  ): Promise<AltanaExecuteResult>;
  approveSignatureChecker(
    opts: AltanaApproveSignatureCheckerOptions,
  ): Promise<AltanaExecuteResult>;
  revokeSignatureChecker(
    opts: AltanaApproveSignatureCheckerOptions,
  ): Promise<AltanaExecuteResult>;
}

/** The module-level x402 additions in `@altananetwork/sdk` >= 0.4.0. */
export interface AltanaSdkModuleX402 {
  /** Canonical Permit2 — the checker for the permit2-exact/B402 rail. */
  PERMIT2_ADDRESS: `0x${string}`;
  /**
   * Sign one raw 402 `accepts[]` entry with the session key → the
   * complete `X-PAYMENT` header value. Positional parameters, chain
   * independent (the requirement's `network` carries the chain); the SDK
   * owns the rail-specific typed-data construction (permit2-exact/B402
   * witness, EIP-3009).
   */
  signX402Payment(
    session: AltanaSession,
    requirement: Record<string, unknown>,
  ): Promise<AltanaSignX402PaymentResult>;
}
