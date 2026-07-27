/**
 * Intent — the SDK's high-level execution seam.
 *
 * An {@link Intent} describes a single high-level on-chain operation
 * (register an agent, set metadata, fund a job, ...). It is deliberately
 * *dual-representation* so that different execution backends can consume
 * whichever form they understand:
 *
 * - **Semantic form** (`name` + `kwargs`): the operation expressed as a
 *   namespaced identifier and its high-level arguments, e.g.
 *   `"erc8004.register"` with `{ agentUri: ..., metadata: [...] }`.
 *   Consumed by backends that rebuild the call themselves — for example a
 *   CLI- or REST-backed wallet that owns build + sign + broadcast and only
 *   speaks high-level commands.
 * - **Mechanical form** (`call`): a pre-encoded contract call ready to be
 *   built, signed and broadcast. Consumed by the local signing executor,
 *   which stays protocol-agnostic — it never needs to know what `name`
 *   means.
 *
 * The call site (a contract client that already holds the ABI) produces both
 * forms cheaply, so the asymmetry between backends is absorbed there rather
 * than leaking protocol knowledge into any executor.
 *
 * Port of `python/bnbagent/wallets/intents.py`.
 */

import type { Abi, PublicClient, TransactionReceipt } from "viem";
import type { Paymaster } from "../core/paymaster.js";

// ── Intent name constants ("<module>.<operation>") ──
// Used by semantic executors to recognise an operation. The local executor
// ignores these and works purely off `Intent.call`.
export const ERC8004_REGISTER = "erc8004.register";
export const ERC8004_SET_METADATA = "erc8004.set_metadata";
export const ERC8004_SET_AGENT_URI = "erc8004.set_agent_uri";

export const ERC8183_CREATE_JOB = "erc8183.create_job";
export const ERC8183_SET_PROVIDER = "erc8183.set_provider";
export const ERC8183_SET_BUDGET = "erc8183.set_budget";
export const ERC8183_FUND = "erc8183.fund";
export const ERC8183_SUBMIT = "erc8183.submit";
export const ERC8183_COMPLETE = "erc8183.complete";
export const ERC8183_REJECT = "erc8183.reject";
export const ERC8183_CLAIM_REFUND = "erc8183.claim_refund";
export const ERC8183_REGISTER_JOB = "erc8183.register_job";
export const ERC8183_SETTLE = "erc8183.settle";
export const ERC8183_MARK_EXPIRED = "erc8183.mark_expired";
export const ERC8183_DISPUTE = "erc8183.dispute";
export const ERC8183_VOTE_REJECT = "erc8183.vote_reject";

/**
 * A pre-encoded contract call — the mechanical form an {@link Intent} can
 * carry. Consumed by the local build/sign/broadcast executor, which stays
 * protocol-agnostic.
 */
export interface ContractCall {
  address: `0x${string}`;
  abi: Abi;
  functionName: string;
  args: readonly unknown[];
}

/**
 * A single high-level on-chain operation.
 *
 * - `name` / `kwargs`: namespaced operation identifier and its high-level
 *   arguments. Used by semantic executors; ignored by the local executor.
 * - `call`: the mechanical form. Used by the local build/sign/broadcast
 *   executor; ignored by semantic executors.
 * - `value`: native-token value (wei) to send with the call.
 * - `gas`: optional explicit gas limit. `null`/absent means the executor
 *   estimates it.
 * - `description`: human-readable label used in logs.
 */
export interface Intent {
  name?: string;
  kwargs?: Record<string, unknown>;
  call?: ContractCall | null;
  value?: bigint;
  gas?: bigint | null;
  description?: string;
}

/**
 * Runtime context a wallet needs to build its executor.
 *
 * A pure-signing wallet has no chain connection of its own, so to broadcast
 * it must be handed one. This carries that connection (and an optional
 * paymaster) to {@link WalletProvider.makeExecutor}. A self-broadcasting
 * wallet may still use the public client to reconcile its backend's result.
 */
export interface ExecutionContext {
  client: PublicClient;
  paymaster?: Paymaster | null;
  receiptTimeout?: number | null;
  /**
   * Seconds a relay-returned hash may stay unseen before the sponsored wait
   * aborts as unverified (and the self-pay fallback engages). Absent uses the
   * SDK default. Self-broadcasting wallets ignore it.
   */
  relayUnseenTimeout?: number | null;
}

/**
 * Canonical result of executing an {@link Intent}.
 *
 * `receipt` may be `null` for backends that do not surface a full receipt.
 * Implementations may add operation-specific fields (e.g. `agentId`).
 */
export interface TxResult {
  transactionHash: `0x${string}`;
  status: number;
  receipt: TransactionReceipt | null;
  [k: string]: unknown;
}

/**
 * Executes an {@link Intent} end-to-end.
 *
 * This is the SDK's primary execution seam. The local signing path
 * (build + sign via a {@link WalletProvider} + broadcast) and any
 * self-broadcasting backend (custodial / CLI-backed wallet, account
 * abstraction bundler, ...) are peer implementations of this interface,
 * selected at construction time by the caller.
 */
export interface IntentExecutor {
  execute(intent: Intent): Promise<TxResult>;
}
