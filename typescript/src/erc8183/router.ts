/**
 * Thin client around `EvaluatorRouterUpgradeable`.
 *
 * The Router acts as `job.evaluator` and `job.hook` for every job that is
 * registered with it. Its two primary public-surface methods are:
 *
 * - `registerJob(jobId, policy)` — client binds a whitelisted policy after
 *   `createJob` and before `fund`.
 * - `settle(jobId)` — permissionless; pulls the verdict from the policy and
 *   applies it to the kernel (`complete` or `reject`).
 *
 * Port of `python/bnbagent/erc8183/router.py`.
 */

import { type Hex, type PublicClient, getAddress } from "viem";
import { evaluatorRouterAbi } from "../abis/evaluatorRouter.js";
import { ContractBase } from "../core/contractBase.js";
import type { Paymaster } from "../core/paymaster.js";
import {
  ERC8183_MARK_EXPIRED,
  ERC8183_REGISTER_JOB,
  ERC8183_SETTLE,
  type TxResult,
} from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import type { JobStatus, Verdict } from "./types.js";

/** Options accepted by the {@link RouterClient} constructor (beyond the wallet). */
export interface RouterClientOpts {
  paymaster?: Paymaster | null;
  receiptTimeout?: number | null;
}

/** A `JobRegistered` event log, flattened for callers. */
export interface JobRegisteredEvent {
  jobId: bigint;
  policy: `0x${string}`;
  client: `0x${string}`;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/** A `JobSettled` event log, flattened for callers. */
export interface JobSettledEvent {
  jobId: bigint;
  verdict: Verdict;
  reason: `0x${string}`;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/** A `JobFinalised` event log, flattened for callers. */
export interface JobFinalisedEvent {
  jobId: bigint;
  status: JobStatus;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/**
 * Low-level client for `EvaluatorRouterUpgradeable`.
 *
 * Writes go through `ContractBase`'s intent execution seam (`executeIntent`)
 * so the wallet decides how each intent is built/signed/broadcast.
 */
export class RouterClient extends ContractBase {
  constructor(
    client: PublicClient,
    contractAddress: string,
    walletProvider?: WalletProvider | null,
    opts?: RouterClientOpts,
  ) {
    super({
      client,
      address: getAddress(contractAddress),
      abi: evaluatorRouterAbi,
      walletProvider: walletProvider ?? null,
      paymaster: opts?.paymaster ?? null,
      receiptTimeout: opts?.receiptTimeout ?? null,
    });
  }

  // ----------------------------------------------------------------- writes

  /** Bind `policy` to `jobId`. Client-only, Open-only, single-shot. */
  async registerJob(jobId: bigint, policy: string): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_REGISTER_JOB,
      kwargs: { jobId, policy },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "registerJob",
        args: [jobId, getAddress(policy)],
      },
      description: "register job",
    });
  }

  /** Permissionless: pull the policy verdict and apply it to the kernel. */
  async settle(jobId: bigint, evidence: Hex = "0x"): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_SETTLE,
      kwargs: { jobId, evidence },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "settle",
        args: [jobId, evidence],
      },
      description: "settle job",
    });
  }

  /**
   * Permissionless: reconcile the in-flight counter for a job that exited
   * via `claimRefund` (which has no hook). Reverts `NotExpired` if the job
   * is still live, `WrongStatus` if it never reached an expirable state.
   */
  async markExpired(jobId: bigint): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_MARK_EXPIRED,
      kwargs: { jobId },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "markExpired",
        args: [jobId],
      },
      description: "mark expired",
    });
  }

  // ------------------------------------------------------------------ views

  async commerce(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: evaluatorRouterAbi,
        functionName: "commerce",
      }),
    );
  }

  /** Number of jobs registered but not yet finalised. */
  async inflightJobCount(): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: evaluatorRouterAbi,
        functionName: "inflightJobCount",
      }),
    );
  }

  async jobPolicy(jobId: bigint): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: evaluatorRouterAbi,
        functionName: "jobPolicy",
        args: [jobId],
      }),
    );
  }

  async policyWhitelist(policy: string): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: evaluatorRouterAbi,
        functionName: "policyWhitelist",
        args: [getAddress(policy)],
      }),
    );
  }

  async paused(): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: evaluatorRouterAbi,
        functionName: "paused",
      }),
    );
  }

  // ------------------------------------------------------------ event helpers

  async getJobRegisteredEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
    client?: string,
  ): Promise<JobRegisteredEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobRegistered",
      fromBlock,
      toBlock,
      args: client ? { client: getAddress(client) } : undefined,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      policy: log.args.policy as `0x${string}`,
      client: log.args.client as `0x${string}`,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }

  async getJobSettledEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
    verdict?: Verdict,
  ): Promise<JobSettledEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobSettled",
      fromBlock,
      toBlock,
      args: verdict !== undefined ? { verdict } : undefined,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      verdict: Number(log.args.verdict) as Verdict,
      reason: log.args.reason as `0x${string}`,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }

  /**
   * `JobFinalised(jobId, status)` — emitted whenever the in-flight counter
   * is decremented (kernel `afterAction` for complete/reject, or
   * `markExpired`). Useful for off-chain reconciliation.
   */
  async getJobFinalisedEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
    status?: JobStatus,
  ): Promise<JobFinalisedEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobFinalised",
      fromBlock,
      toBlock,
      args: status !== undefined ? { status } : undefined,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      status: Number(log.args.status) as JobStatus,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }
}
