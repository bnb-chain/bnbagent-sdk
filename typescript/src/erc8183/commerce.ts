/**
 * Thin client around `AgenticCommerceUpgradeable` (the ERC-8183 kernel).
 *
 * This client is **low-level**: each method maps 1:1 to a Solidity function.
 * Approval management and batching are intentionally left to the facade
 * (`ERC8183Client`) — `CommerceClient` only speaks raw kernel.
 *
 * Port of `python/bnbagent/erc8183/commerce.py`; see that module and
 * `python/tests/test_erc8183_intents.py` for the authoritative semantics
 * this file mirrors.
 */

import {
  type Hex,
  type PublicClient,
  type TransactionReceipt,
  decodeEventLog,
  getAddress,
  size,
} from "viem";
import { agenticCommerceAbi } from "../abis/agenticCommerce.js";
import { ContractBase } from "../core/contractBase.js";
import { multicallRead } from "../core/multicall.js";
import type { Paymaster } from "../core/paymaster.js";
import {
  ERC8183_CLAIM_REFUND,
  ERC8183_COMPLETE,
  ERC8183_CREATE_JOB,
  ERC8183_FUND,
  ERC8183_REJECT,
  ERC8183_SET_BUDGET,
  ERC8183_SET_PROVIDER,
  ERC8183_SUBMIT,
  type Intent,
  type TxResult,
} from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import {
  type Job,
  type JobStatus,
  ZERO_ADDRESS,
  ZERO_REASON,
} from "./types.js";

/** Options accepted by the {@link CommerceClient} constructor (beyond the wallet). */
export interface CommerceClientOpts {
  paymaster?: Paymaster | null;
  receiptTimeout?: number | null;
}

/** Arguments accepted by {@link CommerceClient.createJob}. */
export interface CreateJobOpts {
  provider: string;
  evaluator: string;
  expiredAt: bigint;
  description: string;
  hook?: string;
}

/** Result of {@link CommerceClient.createJob} — `jobId` may be `null` if it
 * could not be recovered from either the executor result or the receipt. */
export interface CreateJobResult extends TxResult {
  jobId: bigint | null;
}

/** Raw tuple shape `getJob`/multicall decode to (viem returns named-tuple
 * structs as objects since every ABI component here is named). */
interface RawJob {
  id: bigint;
  client: `0x${string}`;
  provider: `0x${string}`;
  evaluator: `0x${string}`;
  description: string;
  budget: bigint;
  expiredAt: bigint;
  status: number;
  hook: `0x${string}`;
  submittedAt: bigint;
  deliverable: `0x${string}`;
}

/**
 * Decode the tuple returned by `getJob` into a {@link Job}.
 *
 * Tuple layout (post-audit ABI): `(id, client, provider, evaluator,
 * description, budget, expiredAt, status, hook, submittedAt, deliverable)`
 * — index 9 = `submittedAt`, 10 = `deliverable`. viem decodes this
 * named-tuple struct as an object keyed by those same field names (rather
 * than a positional array), so field access below is by name; the index
 * commentary documents the on-chain layout this mirrors
 * (`python/bnbagent/erc8183/commerce.py::_decode_job`).
 */
function decodeJob(raw: RawJob): Job {
  return {
    id: raw.id,
    client: getAddress(raw.client),
    provider: getAddress(raw.provider),
    evaluator: getAddress(raw.evaluator),
    description: raw.description,
    budget: raw.budget,
    expiredAt: raw.expiredAt,
    status: raw.status as JobStatus,
    hook: getAddress(raw.hook),
    submittedAt: raw.submittedAt,
    deliverable: raw.deliverable,
  };
}

function assertBytes32(value: Hex, label: string): void {
  if (size(value) !== 32) {
    throw new Error(`${label} must be exactly 32 bytes`);
  }
}

/** A `JobFunded` event log, flattened for callers. */
export interface JobFundedEvent {
  jobId: bigint;
  client: `0x${string}`;
  provider: `0x${string}`;
  amount: bigint;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/** A `JobCreated` event log, flattened for callers. */
export interface JobCreatedEvent {
  jobId: bigint;
  client: `0x${string}`;
  provider: `0x${string}`;
  evaluator: `0x${string}`;
  expiredAt: bigint;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/** A `JobSubmitted` event log, flattened for callers. */
export interface JobSubmittedEvent {
  jobId: bigint;
  provider: `0x${string}`;
  deliverable: `0x${string}`;
  blockNumber: bigint | null;
  transactionHash: `0x${string}` | null;
}

/**
 * Low-level client for the `AgenticCommerceUpgradeable` kernel.
 *
 * Writes go through `ContractBase`'s intent execution seam (`executeIntent`)
 * so the wallet decides how each intent is built/signed/broadcast.
 */
export class CommerceClient extends ContractBase {
  constructor(
    client: PublicClient,
    contractAddress: string,
    walletProvider?: WalletProvider | null,
    opts?: CommerceClientOpts,
  ) {
    super({
      client,
      address: getAddress(contractAddress),
      abi: agenticCommerceAbi,
      walletProvider: walletProvider ?? null,
      paymaster: opts?.paymaster ?? null,
      receiptTimeout: opts?.receiptTimeout ?? null,
    });
  }

  // ----------------------------------------------------------------- writes

  /** Try to recover the `jobId` assigned by `createJob` from the
   * `JobCreated` event in the transaction's receipt logs.
   *
   * Mirrors `self.contract.events.JobCreated().process_receipt(receipt)` in
   * `python/bnbagent/erc8183/commerce.py`, which is bound to this contract
   * instance and therefore only ever decodes logs (a) emitted by this
   * contract and (b) matching the `JobCreated` signature. viem's
   * `decodeEventLog` has neither binding: it happily decodes any log whose
   * topic0 matches *some* event in the ABI regardless of which contract
   * emitted it, and — since `eventName` is only a type hint, not a runtime
   * filter — it will decode e.g. a `JobFunded` log (which also carries a
   * `jobId`) as itself and hand back matching args instead of throwing. So
   * we filter by `log.address` first to avoid a same-topic0 log from an
   * unrelated contract, and check `decoded.eventName` to avoid mistaking a
   * different event (from this contract) that happens to share a `jobId`
   * field for `JobCreated`. */
  private parseJobCreatedId(logs: TransactionReceipt["logs"]): bigint | null {
    const address = this.address.toLowerCase();
    for (const log of logs) {
      if (log.address.toLowerCase() !== address) {
        continue;
      }
      try {
        const decoded = decodeEventLog({
          abi: agenticCommerceAbi,
          eventName: "JobCreated",
          data: log.data,
          topics: log.topics,
        });
        if (decoded.eventName !== "JobCreated") {
          continue;
        }
        const jobId = (decoded.args as { jobId?: bigint }).jobId;
        if (jobId !== undefined) {
          return jobId;
        }
      } catch {
        // Not a JobCreated log (unrecognized topic0) — try the next one.
      }
    }
    return null;
  }

  /** Create a new job (`Open` state). */
  async createJob(opts: CreateJobOpts): Promise<CreateJobResult> {
    const hook = opts.hook ?? ZERO_ADDRESS;
    const intent: Intent = {
      name: ERC8183_CREATE_JOB,
      kwargs: {
        provider: opts.provider,
        evaluator: opts.evaluator,
        expiredAt: opts.expiredAt,
        description: opts.description,
        hook,
      },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "createJob",
        args: [
          getAddress(opts.provider),
          getAddress(opts.evaluator),
          opts.expiredAt,
          opts.description,
          getAddress(hook),
        ],
      },
      description: "create job",
    };
    const result = await this.executeIntent(intent);

    // Semantic backends surface jobId directly; the local path parses it
    // from the JobCreated event in the receipt.
    let jobId = (result.jobId as bigint | null | undefined) ?? null;
    if (jobId == null && result.receipt) {
      jobId = this.parseJobCreatedId(result.receipt.logs);
    }
    return { ...result, jobId };
  }

  async setProvider(
    jobId: bigint,
    provider: string,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_SET_PROVIDER,
      kwargs: { jobId, provider, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "setProvider",
        args: [jobId, getAddress(provider), optParams],
      },
      description: "set provider",
    });
  }

  async setBudget(
    jobId: bigint,
    amount: bigint,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_SET_BUDGET,
      kwargs: { jobId, amount, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "setBudget",
        args: [jobId, amount, optParams],
      },
      description: "set budget",
    });
  }

  /** Deposit escrow. Caller MUST have approved `expectedBudget` first. */
  async fund(
    jobId: bigint,
    expectedBudget: bigint,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_FUND,
      kwargs: { jobId, expectedBudget, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "fund",
        args: [jobId, expectedBudget, optParams],
      },
      description: "fund job",
    });
  }

  async submit(
    jobId: bigint,
    deliverable: Hex,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    assertBytes32(deliverable, "deliverable");
    return this.executeIntent({
      name: ERC8183_SUBMIT,
      kwargs: { jobId, deliverable, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "submit",
        args: [jobId, deliverable, optParams],
      },
      description: "submit deliverable",
    });
  }

  /** Evaluator-only. Routed jobs are completed via `RouterClient.settle`. */
  async complete(
    jobId: bigint,
    reason: Hex = ZERO_REASON,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    assertBytes32(reason, "reason");
    return this.executeIntent({
      name: ERC8183_COMPLETE,
      kwargs: { jobId, reason, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "complete",
        args: [jobId, reason, optParams],
      },
      description: "complete job",
    });
  }

  /** Client (while Open) or evaluator (while Funded/Submitted). */
  async reject(
    jobId: bigint,
    reason: Hex = ZERO_REASON,
    optParams: Hex = "0x",
  ): Promise<TxResult> {
    assertBytes32(reason, "reason");
    return this.executeIntent({
      name: ERC8183_REJECT,
      kwargs: { jobId, reason, optParams },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "reject",
        args: [jobId, reason, optParams],
      },
      description: "reject job",
    });
  }

  /** Permissionless refund path after `expiredAt`. Not pausable, no hook. */
  async claimRefund(jobId: bigint): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_CLAIM_REFUND,
      kwargs: { jobId },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "claimRefund",
        args: [jobId],
      },
      description: "claim refund",
    });
  }

  // ------------------------------------------------------------------ views

  async getJob(jobId: bigint): Promise<Job> {
    const raw = await this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "getJob",
        args: [jobId],
      }),
    );
    return decodeJob(raw as unknown as RawJob);
  }

  async jobCounter(): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "jobCounter",
      }),
    );
  }

  async paymentToken(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "paymentToken",
      }),
    );
  }

  async platformFeeBp(): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "platformFeeBP",
      }),
    );
  }

  async platformTreasury(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "platformTreasury",
      }),
    );
  }

  async jobHasBudget(jobId: bigint): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: agenticCommerceAbi,
        functionName: "jobHasBudget",
        args: [jobId],
      }),
    );
  }

  /** Batch read via Multicall3 — optional convenience for indexers. */
  async getJobsBatch(jobIds: readonly bigint[]): Promise<(Job | null)[]> {
    if (jobIds.length === 0) {
      return [];
    }
    const rawResults = await multicallRead(this.client, {
      address: this.address,
      abi: this.abi,
      functionName: "getJob",
      callArgsList: jobIds.map((jobId) => [jobId] as const),
    });
    return rawResults.map(([success, decoded]) => {
      if (!success || !decoded) {
        return null;
      }
      try {
        return decodeJob(decoded as unknown as RawJob);
      } catch {
        return null;
      }
    });
  }

  // --------------------------------------------------------- event helpers

  async getJobFundedEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
    provider?: string,
  ): Promise<JobFundedEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobFunded",
      fromBlock,
      toBlock,
      args: provider ? { provider: getAddress(provider) } : undefined,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      client: log.args.client as `0x${string}`,
      provider: log.args.provider as `0x${string}`,
      amount: log.args.amount as bigint,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }

  async getJobCreatedEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
  ): Promise<JobCreatedEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobCreated",
      fromBlock,
      toBlock,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      client: log.args.client as `0x${string}`,
      provider: log.args.provider as `0x${string}`,
      evaluator: log.args.evaluator as `0x${string}`,
      expiredAt: log.args.expiredAt as bigint,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }

  /**
   * `JobSubmitted(jobId, provider, deliverable)` — used by
   * `ERC8183Client.getDeliverableUrl` to self-resolve the block a job was
   * submitted in when no `hintBlock` is supplied (see that method's
   * `_resolveSubmitBlock` walk-back).
   */
  async getJobSubmittedEvents(
    fromBlock: bigint,
    toBlock: bigint | "latest" = "latest",
    jobId?: bigint,
  ): Promise<JobSubmittedEvent[]> {
    const logs = await this.readEvents({
      eventName: "JobSubmitted",
      fromBlock,
      toBlock,
      args: jobId !== undefined ? { jobId } : undefined,
    });
    return logs.map((log) => ({
      jobId: log.args.jobId as bigint,
      provider: log.args.provider as `0x${string}`,
      deliverable: log.args.deliverable as `0x${string}`,
      blockNumber: log.blockNumber,
      transactionHash: log.transactionHash,
    }));
  }
}
