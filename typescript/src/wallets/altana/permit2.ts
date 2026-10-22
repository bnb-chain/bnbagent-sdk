/**
 * Permit2 AllowanceTransfer cleanup for Altana wallets (SRC-1609).
 *
 * A session approved as an ERC-1271 signer for Permit2 can sign a
 * `PermitSingle`. Once anyone submits it, the allowance is stored inside
 * Permit2 under the wallet (owner) and does not depend on the session any
 * more: revoking the checker or the session, or letting it expire, leaves
 * it live, and resetting the outer `approve(Permit2, x)` only pauses it
 * until the next re-approval. Permit2's `lockdown` zeroes such allowances.
 */

import {
  type PublicClient,
  encodeAbiParameters,
  encodeFunctionData,
  getAddress,
  keccak256,
  padHex,
  parseAbi,
  parseAbiItem,
} from "viem";
import { publicKeyToAddress } from "viem/accounts";
import { erc20Abi } from "../../abis/erc20.js";
import { webAuthnP256KeyHash } from "./passkey.js";
import type { AltanaCall, AltanaExecuteResult } from "./types.js";

export const PERMIT2_ALLOWANCE_ABI = parseAbi([
  "event Permit(address indexed owner, address indexed token, address indexed spender, uint160 amount, uint48 expiration, uint48 nonce)",
  "event Approval(address indexed owner, address indexed token, address indexed spender, uint160 amount, uint48 expiration)",
  "function allowance(address owner, address token, address spender) view returns (uint160 amount, uint48 expiration, uint48 nonce)",
  "function lockdown((address token, address spender)[] approvals)",
]);

const PERMIT_EVENT = PERMIT2_ALLOWANCE_ABI[0];
const APPROVAL_EVENT = PERMIT2_ALLOWANCE_ABI[1];

/** The account's event for `setSignatureCheckerApproval` (IthacaAccount). */
export const SIGNATURE_CHECKER_APPROVAL_EVENT = parseAbiItem(
  "event SignatureCheckerApprovalSet(bytes32 indexed keyHash, address indexed checker, bool isApproved)",
);

/** Default `eth_getLogs` block range per request. */
export const DEFAULT_PERMIT2_LOG_CHUNK = 5_000n;

/** secp256k1 in IthacaAccount's `KeyType` enum. */
const SECP256K1_KEY_TYPE = 2n;

/**
 * The keyHash IthacaAccount stores for a session public key: the flat
 * 64-byte P256 key of a passkey session, or the 65-byte `0x04`-prefixed
 * secp256k1 key (stored as its address).
 */
export function sessionKeyHash(publicKey: `0x${string}`): `0x${string}` {
  if (/^0x[0-9a-fA-F]{128}$/u.test(publicKey)) {
    return webAuthnP256KeyHash(publicKey);
  }
  return keccak256(
    encodeAbiParameters(
      [{ type: "uint256" }, { type: "bytes32" }],
      [
        SECP256K1_KEY_TYPE,
        keccak256(padHex(publicKeyToAddress(publicKey), { size: 32 })),
      ],
    ),
  );
}

/** A Permit2 token/spender pair of this wallet. */
export interface AltanaPermit2Pair {
  token: `0x${string}`;
  spender: `0x${string}`;
}

/** A stored Permit2 (AllowanceTransfer) allowance that is still non-zero. */
export interface AltanaPermit2Allowance extends AltanaPermit2Pair {
  amount: bigint;
  /** Unix seconds; Permit2 rejects pulls after it. */
  expiration: number;
  nonce: number;
}

/** Options for {@link scanPermit2Allowances} / `listPermit2Allowances`. */
export interface AltanaListPermit2AllowancesOpts {
  /**
   * First block to scan. Must not be later than the block where Permit2 was
   * approved as the session's signature checker (record it from the
   * `approveX402SignatureChecker` receipt); an earlier block is only slower.
   */
  fromBlock: bigint;
  /** Last block to scan (default: the latest block). */
  toBlock?: bigint;
  /** Only these tokens (default: every token). */
  tokens?: readonly `0x${string}`[];
  /**
   * Client for the scan. Default: one on the network's `publicRpcUrl`.
   * Public BSC endpoints often limit `eth_getLogs`; pass a client on an
   * RPC that serves logs for the range.
   */
  client?: PublicClient;
  /** Blocks per `eth_getLogs` request (default 5 000). */
  chunkSize?: bigint;
}

/**
 * `permit2Cleanup` option of `revokeX402SignatureChecker` / `revokeSession`.
 * The cleanup fails unless the scanned range also shows the account's
 * approval of Permit2 as this session's signature checker.
 */
export interface AltanaPermit2CleanupOpts
  extends AltanaListPermit2AllowancesOpts {
  /**
   * Spenders whose allowances are left untouched, e.g. a router the wallet
   * uses through Permit2 on purpose. A token that still has a kept
   * allowance also keeps its outer approval, since zeroing it would
   * disable the kept spender.
   */
  keepSpenders?: readonly `0x${string}`[];
}

/** What the revoke-time Permit2 cleanup found and submitted. */
export interface AltanaPermit2CleanupResult {
  /** The live allowances that were locked down. */
  allowances: AltanaPermit2Allowance[];
  /** The live allowances left in place because of `keepSpenders`. */
  kept: AltanaPermit2Allowance[];
  /** The lockdown batch, or `null` when nothing was found. */
  lockdown: AltanaExecuteResult | null;
}

/**
 * Options of `revokeSession` and `revokeX402SignatureChecker` for the
 * Permit2 allowances the session signed (SRC-1609), which survive the
 * revoke. The session can sign one for any token.
 */
export interface AltanaRevokeOpts {
  feeToken?: `0x${string}`;
  /**
   * The outer `approve(Permit2, 0)` reset run after the revoke, in one
   * batch with any lockdown; it needs no log scan. By default it covers
   * the chain's catalog tokens and the tokens of the allowances
   * `permit2Cleanup` found; `extraTokens` adds others the wallet approved
   * to Permit2. `false` skips it, which leaves the session's stored
   * allowances able to pull; the reset also pauses x402 for the wallet's
   * other sessions until `setPermit2Allowance` raises the approval again.
   */
  permit2Reset?: false | { extraTokens?: readonly `0x${string}`[] };
  /** Scan for the session's stored allowances and lock them down. */
  permit2Cleanup?: AltanaPermit2CleanupOpts;
  /** Client for the reads (default: the network's public RPC). */
  client?: PublicClient;
}

/**
 * A revoke result, with the Permit2 cleanup when one was requested and the
 * outer Permit2 approval reset (absent with `permit2Reset: false`).
 */
export type AltanaRevokeResult = AltanaExecuteResult & {
  permit2Cleanup?: AltanaPermit2CleanupResult;
  permit2Reset?: AltanaPermit2ResetResult;
};

/**
 * Tokens whose outer `approve(Permit2, …)` a revoke set to 0. Without an
 * outer approval no stored Permit2 allowance can pull tokens, so this
 * disarms them without a log scan, but only for the tokens in `covered`.
 */
export interface AltanaPermit2ResetResult {
  tokens: `0x${string}`[];
  /**
   * Tokens whose outer approval was read. A session-signed allowance for
   * any other token the wallet approved to Permit2 can still pull.
   */
  covered: `0x${string}`[];
  /** The batch (shared with a requested lockdown), or `null` when none was needed. */
  batch: AltanaExecuteResult | null;
}

/**
 * `permit2Cleanup` option of `setPermit2Allowance`: lock down the token's
 * stored Permit2 allowances in the same batch that raises its outer
 * approval, so an allowance a session signed earlier cannot come back.
 */
export type AltanaPermit2ReapproveCleanupOpts = Omit<
  AltanaPermit2CleanupOpts,
  "tokens" | "fromBlock"
> & {
  /**
   * First block to scan. Default: the wallet's first EIP-7702 delegation
   * ({@link findDelegationBlock}), before which no session can have been
   * approved. An explicit block must not be later: the wallet must have
   * had no code and no storage at `fromBlock - 1`.
   */
  fromBlock?: bigint;
};

/**
 * Options of a non-zero `setPermit2Allowance`: exactly one of
 * `permit2Cleanup` and `skipPermit2Cleanup: true` (no session of the wallet
 * was ever a Permit2 signature checker).
 */
export type AltanaSetPermit2AllowanceOpts = { feeToken?: `0x${string}` } & (
  | {
      permit2Cleanup: AltanaPermit2ReapproveCleanupOpts;
      skipPermit2Cleanup?: never;
    }
  | { skipPermit2Cleanup: true; permit2Cleanup?: never }
);

/** A `setPermit2Allowance` result, with the lockdown when `permit2Cleanup` ran. */
export type AltanaSetPermit2AllowanceResult = AltanaExecuteResult & {
  permit2Cleanup?: AltanaPermit2CleanupResult;
};

/**
 * Every (token, spender) pair `owner` has approved in Permit2 between
 * `fromBlock` and `toBlock` (from `Permit` and `Approval` events) whose
 * stored allowance is still non-zero.
 */
export async function scanPermit2Allowances(
  client: PublicClient,
  permit2: `0x${string}`,
  owner: `0x${string}`,
  opts: AltanaListPermit2AllowancesOpts,
): Promise<AltanaPermit2Allowance[]> {
  const chunk = opts.chunkSize ?? DEFAULT_PERMIT2_LOG_CHUNK;
  if (chunk <= 0n) {
    throw new Error(
      `listPermit2Allowances: chunkSize must be positive, got ${chunk}`,
    );
  }
  const toBlock =
    opts.toBlock ?? (await client.getBlockNumber({ cacheTime: 0 }));
  const token = opts.tokens?.map((t) => getAddress(t));
  const pairs = new Map<string, AltanaPermit2Pair>();
  for (let from = opts.fromBlock; from <= toBlock; from += chunk) {
    const to = from + chunk - 1n < toBlock ? from + chunk - 1n : toBlock;
    for (const event of [PERMIT_EVENT, APPROVAL_EVENT]) {
      const logs = await client.getLogs({
        address: permit2,
        event,
        args: { owner, ...(token ? { token } : {}) },
        fromBlock: from,
        toBlock: to,
        strict: true,
      });
      for (const log of logs) {
        const pair = {
          token: getAddress(log.args.token),
          spender: getAddress(log.args.spender),
        };
        pairs.set(`${pair.token}:${pair.spender}`, pair);
      }
    }
  }
  const live: AltanaPermit2Allowance[] = [];
  for (const pair of pairs.values()) {
    const [amount, expiration, nonce] = await client.readContract({
      address: permit2,
      abi: PERMIT2_ALLOWANCE_ABI,
      functionName: "allowance",
      args: [owner, pair.token, pair.spender],
    });
    if (amount > 0n) {
      live.push({ ...pair, amount, expiration, nonce });
    }
  }
  return live;
}

/**
 * Whether `wallet` approved `checker` for the session `keyHash` (any session
 * when `keyHash` is `undefined`) between `fromBlock` and `toBlock`. A cleanup
 * scan whose range does not show that approval started too late or read an
 * RPC that served no logs, so finding no allowances there proves nothing.
 */
export async function hasCheckerApproval(
  client: PublicClient,
  wallet: `0x${string}`,
  keyHash: `0x${string}` | undefined,
  checker: `0x${string}`,
  opts: Pick<
    AltanaListPermit2AllowancesOpts,
    "fromBlock" | "toBlock" | "chunkSize"
  >,
): Promise<boolean> {
  const chunk = opts.chunkSize ?? DEFAULT_PERMIT2_LOG_CHUNK;
  if (chunk <= 0n) {
    throw new Error(`chunkSize must be positive, got ${chunk}`);
  }
  const toBlock =
    opts.toBlock ?? (await client.getBlockNumber({ cacheTime: 0 }));
  for (let from = opts.fromBlock; from <= toBlock; from += chunk) {
    const to = from + chunk - 1n < toBlock ? from + chunk - 1n : toBlock;
    const logs = await client.getLogs({
      address: wallet,
      event: SIGNATURE_CHECKER_APPROVAL_EVENT,
      args: keyHash === undefined ? { checker } : { keyHash, checker },
      fromBlock: from,
      toBlock: to,
      strict: true,
    });
    if (logs.some((log) => log.args.isApproved)) {
      return true;
    }
  }
  return false;
}

/** Root of an empty storage trie (`eth_getProof`'s `storageHash`). */
const EMPTY_STORAGE_ROOT =
  "0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421";

/**
 * Whether `address` had been used as an account by `blockNumber`: it had
 * code or storage. A delegation that was cleared again keeps its storage
 * (an Altana account's relay nonces never go back to zero), so this also
 * sees an earlier delegation. Needs that block's state and `eth_getProof`.
 */
export async function usedAsAccountAt(
  client: PublicClient,
  address: `0x${string}`,
  blockNumber: bigint,
): Promise<boolean> {
  const [code, proof] = await Promise.all([
    client.getCode({ address, blockNumber }),
    client.getProof({ address, storageKeys: [], blockNumber }),
  ]);
  const hasCode = code !== undefined && code !== "0x";
  const storageRoot = proof.storageHash.toLowerCase();
  // a node may report a zero hash for an account that does not exist yet
  const hasStorage =
    storageRoot !== EMPTY_STORAGE_ROOT && !/^0x0*$/u.test(storageRoot);
  return hasCode || hasStorage;
}

/**
 * The first block at which `wallet` was used as an account
 * ({@link usedAsAccountAt}): its first EIP-7702 delegation. No account
 * event, so no signature-checker approval, can precede it. Binary search
 * over historical state, so `client` must serve old state and
 * `eth_getProof`.
 */
export async function findDelegationBlock(
  client: PublicClient,
  wallet: `0x${string}`,
): Promise<bigint> {
  const head = await client.getBlockNumber({ cacheTime: 0 });
  if (!(await usedAsAccountAt(client, wallet, head))) {
    throw new Error(
      `findDelegationBlock: ${wallet} has no code or storage at the latest block ${head}, so it is not a delegated account`,
    );
  }
  if (await usedAsAccountAt(client, wallet, 0n)) {
    return 0n;
  }
  let lo = 0n;
  let hi = head;
  while (hi - lo > 1n) {
    const mid = (lo + hi) / 2n;
    if (await usedAsAccountAt(client, wallet, mid)) {
      hi = mid;
    } else {
      lo = mid;
    }
  }
  return hi;
}

/**
 * One atomic batch: Permit2 `lockdown(pairs)` (omitted when `pairs` is
 * empty), then `approve(Permit2, 0)` on each of `zeroOuterFor`.
 */
export function permit2LockdownCalls(
  permit2: `0x${string}`,
  pairs: readonly AltanaPermit2Pair[],
  zeroOuterFor: readonly `0x${string}`[],
): AltanaCall[] {
  const approvals = pairs.map((p) => ({
    token: getAddress(p.token),
    spender: getAddress(p.spender),
  }));
  const calls: AltanaCall[] =
    approvals.length === 0
      ? []
      : [
          {
            to: permit2,
            value: 0n,
            data: encodeFunctionData({
              abi: PERMIT2_ALLOWANCE_ABI,
              functionName: "lockdown",
              args: [approvals],
            }),
          },
        ];
  for (const token of new Set(zeroOuterFor.map((t) => getAddress(t)))) {
    calls.push(outerPermit2ApprovalCall(permit2, token, 0n));
  }
  return calls;
}

/** `token.approve(Permit2, amount)`: the outer approval Permit2 pulls through. */
export function outerPermit2ApprovalCall(
  permit2: `0x${string}`,
  token: `0x${string}`,
  amount: bigint,
): AltanaCall {
  return {
    to: getAddress(token),
    value: 0n,
    data: encodeFunctionData({
      abi: erc20Abi,
      functionName: "approve",
      args: [permit2, amount],
    }),
  };
}

/** Split live allowances into the ones to lock down and the ones kept. */
export function planPermit2Cleanup(
  allowances: readonly AltanaPermit2Allowance[],
  opts: Pick<AltanaPermit2CleanupOpts, "keepSpenders">,
): { lock: AltanaPermit2Allowance[]; kept: AltanaPermit2Allowance[] } {
  const keep = new Set((opts.keepSpenders ?? []).map((s) => getAddress(s)));
  return {
    lock: allowances.filter((a) => !keep.has(getAddress(a.spender))),
    kept: allowances.filter((a) => keep.has(getAddress(a.spender))),
  };
}
