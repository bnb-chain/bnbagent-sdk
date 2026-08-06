/**
 * Shared types and constants for the ERC-8183 SDK.
 *
 * Port of `python/bnbagent/erc8183/types.py`. Mirrors the enums and reason
 * codes defined by the ERC-8183 contract stack:
 *
 * - `JobStatus` — order-dependent with `IACP.JobStatus` in the Solidity kernel.
 * - `Verdict` — order-dependent with `VERDICT_*` constants in
 *   `EvaluatorRouterUpgradeable` and `OptimisticPolicy`.
 * - `REASON_APPROVED` / `REASON_REJECTED` — `keccak256` reason codes emitted
 *   by `OptimisticPolicy`; also re-exported as hex strings for logging.
 *
 * Any change to the on-chain enum / constant layout MUST be reflected here,
 * otherwise `ERC8183Client.getJob(...).status` and verdict comparisons will
 * silently drift.
 */

import { keccakOfText } from "../core/canonicalJson.js";

/** ERC-8183 job lifecycle, matches `IACP.JobStatus`. */
export enum JobStatus {
  OPEN = 0,
  FUNDED = 1,
  SUBMITTED = 2,
  COMPLETED = 3,
  REJECTED = 4,
  EXPIRED = 5,
}

/** Policy verdict, matches `VERDICT_*` in Router + Policy. */
export enum Verdict {
  PENDING = 0,
  APPROVE = 1,
  REJECT = 2,
}

// ---------------------------------------------------------------------------
// Reason codes (bytes32 keccak256 of ASCII label)
// ---------------------------------------------------------------------------

export const REASON_APPROVED: `0x${string}` = keccakOfText(
  "OPTIMISTIC_APPROVED",
);
export const REASON_REJECTED: `0x${string}` = keccakOfText(
  "OPTIMISTIC_REJECTED",
);

export const ZERO_REASON: `0x${string}` = `0x${"00".repeat(32)}`;
export const ZERO_ADDRESS: `0x${string}` = `0x${"00".repeat(20)}`;

/** Typed view of `IACP.Job` returned by `commerce.getJob`. */
export interface Job {
  id: bigint;
  client: `0x${string}`;
  provider: `0x${string}`;
  evaluator: `0x${string}`;
  description: string;
  budget: bigint;
  expiredAt: bigint;
  status: JobStatus;
  hook: `0x${string}`;
  /**
   * `keccak256(canonical manifest JSON)` written by `submit`; 32 zero bytes
   * for jobs that have not been submitted yet (audit I05).
   */
  deliverable: `0x${string}`;
  /** On-chain `submittedAt` (unix seconds); 0 until the job is submitted. */
  submittedAt: bigint;
}
