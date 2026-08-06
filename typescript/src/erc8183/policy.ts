/**
 * Thin client around `OptimisticPolicy` (ERC-8183 reference policy).
 *
 * Surface:
 *
 * - `dispute(jobId)`    — client-only, within dispute window.
 * - `voteReject(jobId)` — whitelisted voter, post-dispute.
 * - Read helpers for window state, quorum, voter status, etc.
 *
 * Note: the contract's "silence approves" design means voters can ONLY
 * reject. There is no `voteApprove` on-chain; jobs without dispute
 * auto-approve when `submittedAt + disputeWindow` elapses.
 *
 * Port of `python/bnbagent/erc8183/policy.py`.
 */

import { type Hex, type PublicClient, getAddress, hexToString } from "viem";
import { optimisticPolicyAbi } from "../abis/optimisticPolicy.js";
import { ContractBase, type DecodedEventLog } from "../core/contractBase.js";
import type { Paymaster } from "../core/paymaster.js";
import { describeError } from "../core/txSender.js";
import { RpcRangeLimitError } from "../errors.js";
import {
  ERC8183_DISPUTE,
  ERC8183_VOTE_REJECT,
  type TxResult,
} from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import type { Verdict } from "./types.js";

/** Options accepted by the {@link PolicyClient} constructor (beyond the wallet). */
export interface PolicyClientOpts {
  paymaster?: Paymaster | null;
  receiptTimeout?: number | null;
}

/** Options accepted by {@link PolicyClient.getDeliverableUrl}. */
export interface GetDeliverableUrlOpts {
  /** Block the `JobSubmitted`/`onSubmitted` call landed in, if known — narrows
   * the log scan to a tight `+-10` block window instead of a 1000-block
   * fallback. */
  hintBlock?: bigint;
}

const TIGHT_WINDOW_BLOCKS = 10n;
const FALLBACK_WINDOW_BLOCKS = 1_000n;

/**
 * Low-level client for `OptimisticPolicy`.
 *
 * `dispute`/`voteReject` go through `ContractBase`'s intent execution seam
 * (`executeIntent`). The owner-only admin ops (`addVoter`/`removeVoter`/
 * `setQuorum`) are deliberately NOT migrated to that seam — they go through
 * `sendTx` directly, mirroring the Python reference.
 */
export class PolicyClient extends ContractBase {
  constructor(
    client: PublicClient,
    contractAddress: string,
    walletProvider?: WalletProvider | null,
    opts?: PolicyClientOpts,
  ) {
    super({
      client,
      address: getAddress(contractAddress),
      abi: optimisticPolicyAbi,
      walletProvider: walletProvider ?? null,
      paymaster: opts?.paymaster ?? null,
      receiptTimeout: opts?.receiptTimeout ?? null,
    });
  }

  // ----------------------------------------------------------------- writes

  /** Client raises a dispute. MUST be within dispute window. */
  async dispute(jobId: bigint): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_DISPUTE,
      kwargs: { jobId },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "dispute",
        args: [jobId],
      },
      description: "dispute job",
    });
  }

  /** Whitelisted voter casts a reject vote (one per voter per job). */
  async voteReject(jobId: bigint): Promise<TxResult> {
    return this.executeIntent({
      name: ERC8183_VOTE_REJECT,
      kwargs: { jobId },
      call: {
        address: this.address,
        abi: this.abi,
        functionName: "voteReject",
        args: [jobId],
      },
      description: "vote reject",
    });
  }

  // --------------------------------------------------------- admin writes

  /**
   * Owner-only admin op. Deliberately NOT run through the intent seam
   * (`executeIntent`) — always self-pays via `sendTx`.
   */
  async addVoter(voter: string): Promise<TxResult> {
    return this.sendTx({
      functionName: "addVoter",
      args: [getAddress(voter)],
    });
  }

  /** Owner-only admin op. Deliberately NOT run through the intent seam. */
  async removeVoter(voter: string): Promise<TxResult> {
    return this.sendTx({
      functionName: "removeVoter",
      args: [getAddress(voter)],
    });
  }

  /** Owner-only admin op. Deliberately NOT run through the intent seam. */
  async setQuorum(newQuorum: number): Promise<TxResult> {
    return this.sendTx({
      functionName: "setQuorum",
      args: [newQuorum],
    });
  }

  // ------------------------------------------------------------------ views

  /** Simulate the verdict the Router would see right now. */
  async check(jobId: bigint, evidence: Hex = "0x"): Promise<[Verdict, Hex]> {
    const [verdict, reason] = await this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "check",
        args: [jobId, evidence],
      }),
    );
    return [verdict as Verdict, reason as Hex];
  }

  async submittedAt(jobId: bigint): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "submittedAt",
        args: [jobId],
      }),
    );
  }

  async disputed(jobId: bigint): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "disputed",
        args: [jobId],
      }),
    );
  }

  async rejectVotes(jobId: bigint): Promise<number> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "rejectVotes",
        args: [jobId],
      }),
    );
  }

  async hasVoted(jobId: bigint, voter: string): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "hasVoted",
        args: [jobId, getAddress(voter)],
      }),
    );
  }

  async isVoter(voter: string): Promise<boolean> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "isVoter",
        args: [getAddress(voter)],
      }),
    );
  }

  async disputeWindow(): Promise<bigint> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "disputeWindow",
      }),
    );
  }

  async voteQuorum(): Promise<number> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "voteQuorum",
      }),
    );
  }

  /**
   * `voteQuorum` value snapshotted at `dispute()` time. Returns `0` if the
   * job has never been disputed. After `dispute`, the snapshot is the
   * threshold `check` will use, even if an admin later calls `setQuorum` —
   * protects pending disputes from retroactive admin adjustments.
   */
  async disputeQuorumSnapshot(jobId: bigint): Promise<number> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "disputeQuorumSnapshot",
        args: [jobId],
      }),
    );
  }

  async activeVoterCount(): Promise<number> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "activeVoterCount",
      }),
    );
  }

  async admin(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "admin",
      }),
    );
  }

  async commerce(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "commerce",
      }),
    );
  }

  async router(): Promise<`0x${string}`> {
    return this.callWithRetry(() =>
      this.client.readContract({
        address: this.address,
        abi: optimisticPolicyAbi,
        functionName: "router",
      }),
    );
  }

  /**
   * Return the `deliverable_url` for a submitted job.
   *
   * Reads the `JobInitialised` event emitted by `onSubmitted` and parses
   * `optParams` (JSON bytes) to extract `deliverable_url`. Returns `null`
   * if the event is not found or the field is absent. Throws
   * {@link RpcRangeLimitError} when the node rejects the log query with a
   * rate/range limit (`-32005`) — that is retryable, not proof of absence.
   *
   * Prefer calling the ERC8183 facade's `getDeliverableUrl`, which
   * auto-resolves `hintBlock` via Commerce's `JobSubmitted` event. If
   * called directly without `hintBlock`, a 1000-block fallback window is
   * used.
   */
  async getDeliverableUrl(
    jobId: bigint,
    opts?: GetDeliverableUrlOpts,
  ): Promise<string | null> {
    let currentBlock: bigint | null = null;
    try {
      currentBlock = await this.client.getBlockNumber();
    } catch {
      currentBlock = null;
    }

    let fromBlock: bigint;
    let toBlock: bigint | "latest";
    if (opts?.hintBlock !== undefined) {
      const hint = opts.hintBlock;
      fromBlock = hint > TIGHT_WINDOW_BLOCKS ? hint - TIGHT_WINDOW_BLOCKS : 0n;
      toBlock = hint + TIGHT_WINDOW_BLOCKS;
    } else if (currentBlock !== null) {
      fromBlock =
        currentBlock > FALLBACK_WINDOW_BLOCKS
          ? currentBlock - FALLBACK_WINDOW_BLOCKS
          : 0n;
      toBlock = "latest";
    } else {
      fromBlock = 0n;
      toBlock = "latest";
    }

    let logs: DecodedEventLog[];
    try {
      logs = await this.readEvents({
        eventName: "JobInitialised",
        fromBlock,
        toBlock,
        args: { jobId },
      });
    } catch (error) {
      const message = describeError(error);
      if (
        message.includes("-32005") ||
        message.toLowerCase().includes("limit exceeded")
      ) {
        // Rate/range-limited RPC is NOT "event not found" — surface a typed
        // retryable error instead of a null the caller would misread as a
        // genuinely absent deliverable.
        throw new RpcRangeLimitError(
          `JobInitialised scan for job ${jobId} hit the RPC range/rate limit; retry later`,
        );
      }
      console.warn(
        `[PolicyClient] getDeliverableUrl(${jobId}) event query failed: ${message}`,
      );
      return null;
    }

    if (logs.length === 0) {
      return null;
    }

    const raw = logs[0]?.args.optParams as Hex | undefined;
    if (!raw || raw === "0x") {
      return null;
    }
    try {
      const params = JSON.parse(hexToString(raw)) as {
        deliverable_url?: string;
      };
      return params.deliverable_url || null;
    } catch (error) {
      console.warn(
        `[PolicyClient] getDeliverableUrl(${jobId}) parse failed: ${describeError(error)}`,
      );
      return null;
    }
  }
}
