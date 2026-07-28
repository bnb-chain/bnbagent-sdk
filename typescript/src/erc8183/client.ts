/**
 * `ERC8183Client` — single-entry facade over the ERC-8183 contract stack.
 *
 * ERC-8183 is a three-layer protocol:
 *
 * - `AgenticCommerceUpgradeable` — ERC-8183 kernel (escrow).
 * - `EvaluatorRouterUpgradeable` — routing layer acting as `job.evaluator`
 *   and `job.hook` for every routed job.
 * - `OptimisticPolicy`           — UMA-style silence-approves policy with
 *   a whitelisted-voter reject quorum.
 *
 * `ERC8183Client` composes three thin sub-clients (`commerce` / `router` /
 * `policy`) and a minimal ERC-20 helper. Most callers only use the
 * top-level methods; advanced users can reach the sub-clients via
 * properties.
 *
 * Design notes
 * ------------
 * - Async, built via the static {@link ERC8183Client.create} factory — the
 *   defense-in-depth chain-id assertion at startup requires an RPC
 *   round-trip, which a synchronous constructor cannot perform.
 * - Signing is wallet-provider only — raw private keys never cross this API.
 * - Network configuration goes through a single `network` argument that
 *   accepts either a preset name (`"bsc-testnet"`) or a `NetworkConfig`
 *   object for custom deployments (local forks, private RPCs, etc.).
 * - Payment token address is NOT a configuration input — it is immutable
 *   on the kernel and fetched lazily via `commerce.paymentToken()`.
 * - `fund` uses a **floor-based** approval strategy (see {@link fund}'s
 *   docstring). Default floor is `100 * 10**decimals`, which assumes a
 *   stablecoin payment token.
 *
 * Port of `python/bnbagent/erc8183/client.py`.
 */

import type { PublicClient } from "viem";
import { stringToHex } from "viem";
import { type NetworkConfig, resolveNetwork } from "../config.js";
import { canonicalJson } from "../core/canonicalJson.js";
import { createPublicClientFor } from "../core/clients.js";
import { READ_ONLY_MESSAGE } from "../core/contractBase.js";
import { Paymaster } from "../core/paymaster.js";
import { describeError } from "../core/txSender.js";
import { MinimalERC20Client } from "../erc20/client.js";
import type { TxResult } from "../wallets/intents.js";
import type { WalletProvider } from "../wallets/walletProvider.js";
import { CommerceClient, type CreateJobResult } from "./commerce.js";
import { PolicyClient } from "./policy.js";
import { RouterClient } from "./router.js";
import {
  type Job,
  type JobStatus,
  type Verdict,
  ZERO_ADDRESS,
  ZERO_REASON,
} from "./types.js";

/**
 * Default floor for auto-approval in {@link ERC8183Client.fund}, expressed
 * in whole token units. Multiplied by `10 ** tokenDecimals()` at call time.
 * Assumes a stablecoin payment token; non-stable deployments should pass
 * `approveFloor: 0n` (exact) or a custom floor.
 */
export const DEFAULT_APPROVE_FLOOR_UNITS = 100n;

/**
 * Chain IDs where MegaFuel sponsors ERC-8183 writes, so `ERC8183Client`
 * wires a paymaster into the write path. bsc-testnet (97) is sponsorable —
 * though its preset now defaults to self-pay, so wiring needs an explicit
 * `usePaymaster` opt-in (BUG-022);
 * bsc-mainnet (56) is **never** sponsored for ERC-8183 — its writes self-pay,
 * and we don't even probe `isSponsorable` there (the hot production path).
 * If mainnet sponsorship ever lands, add 56 here — that single edit flips it.
 * (ERC-8004 sponsorship is independent and handled in erc8004/agent.ts.)
 */
export const ERC8183_PAYMASTER_CHAIN_IDS: ReadonlySet<number> = new Set([97]);

const REQUIRED_NETWORK_FIELDS: ReadonlyArray<
  readonly [string, keyof NetworkConfig]
> = [
  ["commerce_contract", "commerceContract"],
  ["router_contract", "routerContract"],
  ["policy_contract", "policyContract"],
];

/** Options accepted by {@link ERC8183Client.create}. */
export interface ERC8183ClientCreateOpts {
  /** `WalletProvider` that performs all signing. `null`/omitted builds a
   * read-only client (reads work; writes raise via `executeIntent`). */
  walletProvider?: WalletProvider | null;
  /** Either a preset name (`"bsc-testnet"` / `"bsc-mainnet"`) or a
   * `NetworkConfig` instance for custom deployments. */
  network?: string | NetworkConfig;
  /** Enables extra debug logging (forwarded to the paymaster). */
  debug?: boolean;
}

/** Options accepted by {@link ERC8183Client.createJob}. */
export interface CreateJobFacadeOpts {
  provider?: string;
  expiredAt: bigint;
  description?: string;
  hook?: string;
  /** Bypass the dispute-window foot-gun guard (e.g. tests that intentionally
   * exercise the `SubmissionTooLate` revert path). */
  skipExpiryCheck?: boolean;
}

/** Options accepted by {@link ERC8183Client.fund}. */
export interface FundOpts {
  approveFloor?: bigint;
}

/** `optParams` accepted by {@link ERC8183Client.submit}. */
export interface SubmitOptParams {
  deliverable_url: string;
  [key: string]: unknown;
}

/** Options accepted by {@link ERC8183Client.getDeliverableUrl}. */
export interface GetDeliverableUrlFacadeOpts {
  hintBlock?: bigint;
}

/** Signed quote time window used to locate its economic acceptance event. */
export interface GetJobFundedBlockOpts {
  negotiatedAt: number;
  quoteExpiresAt: number;
}

/**
 * High-level facade over Commerce + Router + Policy.
 *
 * Construct via the async {@link ERC8183Client.create} factory.
 */
export class ERC8183Client {
  readonly commerce: CommerceClient;
  readonly router: RouterClient;
  readonly policy: PolicyClient;
  readonly address: `0x${string}` | null;
  readonly network: NetworkConfig;

  private readonly client: PublicClient;
  private readonly walletProvider: WalletProvider | null;
  private readonly debug: boolean;

  // Cached payment-token state (populated lazily).
  private paymentTokenAddress: `0x${string}` | null = null;
  private paymentTokenDecimals: number | null = null;
  private paymentTokenSymbol: string | null = null;
  private erc20: MinimalERC20Client | null = null;

  private constructor(opts: {
    client: PublicClient;
    network: NetworkConfig;
    walletProvider: WalletProvider | null;
    debug: boolean;
    commerce: CommerceClient;
    router: RouterClient;
    policy: PolicyClient;
  }) {
    this.client = opts.client;
    this.network = opts.network;
    this.walletProvider = opts.walletProvider;
    this.debug = opts.debug;
    this.commerce = opts.commerce;
    this.router = opts.router;
    this.policy = opts.policy;
    // wallet_provider is optional: a read-only client (null) serves all
    // reads; write operations raise via executeIntent ("client is
    // read-only").
    this.address = opts.walletProvider?.address ?? null;
  }

  /**
   * Create an `ERC8183Client`.
   *
   * Connects to the network's RPC and asserts its `chain_id` matches the
   * resolved network config (defense-in-depth against a misconfigured or
   * maliciously redirected RPC URL) before returning.
   *
   * @throws {Error} if the network is missing any of the three ERC-8183
   *   contract addresses, the RPC is unreachable, or the RPC's chain_id
   *   does not match the expected network.
   */
  static async create(
    opts: ERC8183ClientCreateOpts = {},
  ): Promise<ERC8183Client> {
    const walletProvider = opts.walletProvider ?? null;
    const network = opts.network ?? "bsc-testnet";
    const debug = opts.debug ?? false;

    const nc = resolveNetwork(network);
    for (const [fieldName, key] of REQUIRED_NETWORK_FIELDS) {
      if (!nc[key]) {
        throw new Error(
          `network '${nc.name}' is missing ${fieldName}; pass a NetworkConfig with all three ERC-8183 addresses set.`,
        );
      }
    }

    const client = createPublicClientFor(nc.rpcUrl);

    // Defense-in-depth: refuse to operate when the RPC serves a different
    // chain than the NetworkConfig claims. Prevents wrong-chain signing
    // when RPC_URL is misconfigured or maliciously redirected.
    let actualChainId: number;
    try {
      actualChainId = await client.getChainId();
    } catch (error) {
      throw new Error(
        `Failed to connect to RPC: ${nc.rpcUrl} (${describeError(error)})`,
        { cause: error },
      );
    }
    if (actualChainId !== nc.chainId) {
      throw new Error(
        `RPC chain_id mismatch for network '${nc.name}': ` +
          `expected ${nc.chainId}, got ${actualChainId}. ` +
          `The RPC at ${nc.rpcUrl} is serving a different chain.`,
      );
    }

    // Gas sponsorship: wire a paymaster into the write path only when the
    // network opts in (usePaymaster) AND MegaFuel sponsors ERC-8183 on that
    // chain (testnet only; mainnet never — see ERC8183_PAYMASTER_CHAIN_IDS).
    // The executor still gates
    // each write on isSponsorable and self-pays when it cannot sponsor, so
    // this only decides whether to *attempt* sponsorship at all.
    const paymaster = ERC8183Client.buildPaymaster(nc, debug);

    const commerce = new CommerceClient(
      client,
      nc.commerceContract,
      walletProvider,
      { paymaster },
    );
    const router = new RouterClient(client, nc.routerContract, walletProvider, {
      paymaster,
    });
    const policy = new PolicyClient(client, nc.policyContract, walletProvider, {
      paymaster,
    });

    return new ERC8183Client({
      client,
      network: nc,
      walletProvider,
      debug,
      commerce,
      router,
      policy,
    });
  }

  /**
   * Return a `Paymaster` for ERC-8183 writes, or `null` to self-pay.
   *
   * Built only when the network enables a paymaster AND its chain is one
   * MegaFuel sponsors ERC-8183 on ({@link ERC8183_PAYMASTER_CHAIN_IDS}). On
   * every other network — notably bsc-mainnet — this returns `null` so
   * writes self-pay without ever probing `isSponsorable`.
   *
   * Note: the ERC-20 `approve` inside {@link fund} runs through the ERC-20
   * client's own self-pay path and is not sponsored here.
   */
  private static buildPaymaster(
    nc: NetworkConfig,
    debug: boolean,
  ): Paymaster | null {
    if (
      nc.usePaymaster &&
      nc.paymasterUrl &&
      ERC8183_PAYMASTER_CHAIN_IDS.has(nc.chainId)
    ) {
      return new Paymaster(nc.paymasterUrl, debug);
    }
    return null;
  }

  // ------------------------------------------------------------ token cache

  /** Payment token address (cached forever). Fetched from `commerce.paymentToken`. */
  async paymentToken(): Promise<`0x${string}`> {
    if (this.paymentTokenAddress === null) {
      this.paymentTokenAddress = await this.commerce.paymentToken();
    }
    return this.paymentTokenAddress;
  }

  private async erc20Client(): Promise<MinimalERC20Client> {
    if (!this.erc20) {
      const token = await this.paymentToken();
      this.erc20 = new MinimalERC20Client(
        this.client,
        token,
        this.walletProvider,
      );
    }
    return this.erc20;
  }

  async tokenDecimals(): Promise<number> {
    if (this.paymentTokenDecimals === null) {
      this.paymentTokenDecimals = await (await this.erc20Client()).decimals();
    }
    return this.paymentTokenDecimals;
  }

  async tokenSymbol(): Promise<string> {
    if (this.paymentTokenSymbol === null) {
      this.paymentTokenSymbol = await (await this.erc20Client()).symbol();
    }
    return this.paymentTokenSymbol;
  }

  async tokenBalance(address?: string): Promise<bigint> {
    const erc20 = await this.erc20Client();
    return erc20.balanceOf(address ?? this.address ?? "");
  }

  async tokenAllowance(owner: string, spender: string): Promise<bigint> {
    const erc20 = await this.erc20Client();
    return erc20.allowance(owner, spender);
  }

  /** Send `approve(spender, amount)` on the payment token. */
  async approvePaymentToken(
    spender: string,
    amount: bigint,
  ): Promise<TxResult> {
    const erc20 = await this.erc20Client();
    return erc20.approve(spender, amount);
  }

  // ----------------------------------------------------------------- writes

  /**
   * Create a job with the Router set as evaluator + hook.
   *
   * Parameters mirror `AgenticCommerceUpgradeable.createJob` except
   * `evaluator` / `hook` default to the Router address (the v1 deployment
   * pattern).
   *
   * Pre-flights `expiredAt` against the bound policy's `disputeWindow` to
   * catch the foot-gun where the SDK lets you fund a job that `submit()`
   * will always revert with `SubmissionTooLate()` — see
   * https://github.com/bnb-chain/bnbagent-sdk/issues/41 for details.
   *
   * Pass `skipExpiryCheck: true` to bypass the validation (e.g. for tests
   * that intentionally exercise the revert path).
   */
  async createJob(opts: CreateJobFacadeOpts): Promise<CreateJobResult> {
    const {
      provider = ZERO_ADDRESS,
      expiredAt,
      description = "",
      hook,
      skipExpiryCheck = false,
    } = opts;

    if (!skipExpiryCheck) {
      let disputeWindow: bigint | null = null;
      try {
        disputeWindow = await this.policy.disputeWindow();
      } catch (error) {
        // Don't block job creation if dispute_window can't be read (custom
        // policies, RPC hiccup, etc.) — just warn.
        console.warn(
          `[ERC8183Client] dispute_window pre-flight failed; create_job proceeding without expiry check: ${describeError(error)}`,
        );
      }

      if (disputeWindow !== null) {
        const now = BigInt(Math.floor(Date.now() / 1000));
        if (expiredAt - now <= disputeWindow) {
          const days = (Number(disputeWindow) / 86400).toFixed(1);
          throw new Error(
            `expired_at (${expiredAt}) is too close to now (${now}). OptimisticPolicy on this network has dispute_window=${disputeWindow}s (${days}d), so the submit deadline (expired_at - dispute_window = ${expiredAt - disputeWindow}) is already in the past or within seconds. provider.submit() would revert with SubmissionTooLate(). Set expired_at >= now + dispute_window + a buffer (e.g. now + ${disputeWindow + 86400n}). Pass skipExpiryCheck=true to bypass this guard.`,
          );
        }
      }
    }

    return this.commerce.createJob({
      provider,
      evaluator: this.router.address,
      expiredAt,
      description,
      hook: hook ?? this.router.address,
    });
  }

  /** Bind the configured policy (or an override) to a job on the Router. */
  async registerJob(jobId: bigint, policy?: string): Promise<TxResult> {
    return this.router.registerJob(jobId, policy ?? this.policy.address);
  }

  async setProvider(jobId: bigint, provider: string): Promise<TxResult> {
    return this.commerce.setProvider(jobId, provider);
  }

  async setBudget(jobId: bigint, amount: bigint): Promise<TxResult> {
    return this.commerce.setBudget(jobId, amount);
  }

  /**
   * Fund a job, topping up the payment-token allowance if needed.
   *
   * Approval strategy (gas-aware, security-first):
   *
   * 1. If `allowance(client, commerce) >= amount` → call `fund` only.
   * 2. Otherwise approve `max(amount, floor)` where `floor` is:
   *    - `approveFloor` if provided (`0n` = exact `amount`).
   *    - Else `DEFAULT_APPROVE_FLOOR_UNITS * 10n ** tokenDecimals()` (~100 of
   *      the token, a stablecoin-friendly default).
   *
   * The floor pattern saves approve transactions for streams of
   * small-budget jobs; large-budget jobs always fall back to exact approve
   * so residual allowance is bounded.
   *
   * A self-broadcasting backend (e.g. a wallet whose own `fund` bundles
   * approve+deposit) sets `walletProvider.fundBundlesApproval` to the
   * literal `true` to skip the SDK-side allowance management entirely.
   */
  async fund(
    jobId: bigint,
    amount: bigint,
    opts?: FundOpts,
  ): Promise<TxResult> {
    // `=== true` guards against a truthy-but-not-boolean wallet stub in
    // tests (and any non-EVM wallet whose fundBundlesApproval isn't a
    // literal boolean).
    if (this.walletProvider?.fundBundlesApproval === true) {
      return this.commerce.fund(jobId, amount);
    }

    // Guard the read-only path explicitly: without a wallet, `this.address`
    // is null and the allowance read below would otherwise fail deep inside
    // viem with a cryptic `getAddress("")` error instead of this clear
    // message (the same one every write path raises).
    if (this.address === null) {
      throw new Error(READ_ONLY_MESSAGE);
    }
    const owner = this.address;
    const current = await this.tokenAllowance(owner, this.commerce.address);
    if (current < amount) {
      let floor: bigint;
      if (opts?.approveFloor === undefined) {
        floor =
          DEFAULT_APPROVE_FLOOR_UNITS *
          10n ** BigInt(await this.tokenDecimals());
      } else {
        if (opts.approveFloor < 0n) {
          throw new Error("approve_floor must be >= 0");
        }
        floor = opts.approveFloor;
      }
      const cap = amount > floor ? amount : floor;
      await this.approvePaymentToken(this.commerce.address, cap);
    }

    return this.commerce.fund(jobId, amount);
  }

  /**
   * Provider submits.
   *
   * `deliverable` is `DeliverableManifest.manifestHash()` — the keccak256
   * of the canonical manifest JSON (32 bytes). Stored on-chain as the
   * ERC-8183 `deliverable` field (bytes32).
   *
   * `optParams` is serialised to canonical JSON UTF-8 bytes and stored
   * on-chain as `optParams`. Must contain `deliverable_url` (the URL where
   * the full manifest JSON can be fetched for verification).
   */
  async submit(
    jobId: bigint,
    deliverable: `0x${string}`,
    optParams: SubmitOptParams,
  ): Promise<TxResult> {
    if (!optParams.deliverable_url) {
      throw new Error(
        "opt_params['deliverable_url'] must be a non-empty URL (storage URL or agent HTTP endpoint)",
      );
    }
    const encoded = stringToHex(canonicalJson(optParams));
    return this.commerce.submit(jobId, deliverable, encoded);
  }

  /** Client cancels a job still in Open state (no escrow moved). */
  async cancelOpen(
    jobId: bigint,
    reason: `0x${string}` = ZERO_REASON,
  ): Promise<TxResult> {
    return this.commerce.reject(jobId, reason);
  }

  async claimRefund(jobId: bigint): Promise<TxResult> {
    return this.commerce.claimRefund(jobId);
  }

  /** Permissionless: pull the policy verdict and apply it on-chain. */
  async settle(
    jobId: bigint,
    evidence: `0x${string}` = "0x",
  ): Promise<TxResult> {
    return this.router.settle(jobId, evidence);
  }

  /** Permissionless: reconcile the Router's in-flight counter for a job
   * that exited via `claimRefund` (audit L03). */
  async markExpired(jobId: bigint): Promise<TxResult> {
    return this.router.markExpired(jobId);
  }

  async dispute(jobId: bigint): Promise<TxResult> {
    return this.policy.dispute(jobId);
  }

  async voteReject(jobId: bigint): Promise<TxResult> {
    return this.policy.voteReject(jobId);
  }

  // ------------------------------------------------------------------ views

  async getJob(jobId: bigint): Promise<Job> {
    return this.commerce.getJob(jobId);
  }

  async getJobStatus(jobId: bigint): Promise<JobStatus> {
    return (await this.commerce.getJob(jobId)).status;
  }

  /** Public read client used by account-signature verification helpers. */
  get publicClient(): PublicClient {
    return this.client;
  }

  /**
   * Find the block where `JobFunded` economically committed to a quote.
   *
   * Maps the signed quote's timestamp window to block numbers, then makes one
   * indexed log query over that small range. Returns `null` when the job was
   * not funded during the signed window; callers must fail closed.
   */
  async getJobFundedBlock(
    jobId: bigint,
    window: GetJobFundedBlockOpts,
  ): Promise<bigint | null> {
    const current = await this.client.getBlockNumber();
    if (
      !Number.isSafeInteger(window.negotiatedAt) ||
      !Number.isSafeInteger(window.quoteExpiresAt) ||
      window.negotiatedAt < 0 ||
      window.quoteExpiresAt <= window.negotiatedAt
    ) {
      throw new Error("invalid signed quote time window");
    }
    const head = await this.client.getBlock({ blockNumber: current });
    if (head.timestamp < BigInt(window.negotiatedAt)) {
      return null;
    }
    const fromBlock = await this.firstBlockAtOrAfter(
      BigInt(window.negotiatedAt),
      current,
    );
    const toBlock =
      head.timestamp < BigInt(window.quoteExpiresAt)
        ? current
        : await this.firstBlockAtOrAfter(
            BigInt(window.quoteExpiresAt),
            current,
          );
    const events = await this.commerce.getJobFundedEvents(
      fromBlock,
      toBlock,
      undefined,
      jobId,
    );
    return events[0]?.blockNumber ?? null;
  }

  /** Lowest block whose timestamp is greater than or equal to `timestamp`. */
  private async firstBlockAtOrAfter(
    timestamp: bigint,
    head: bigint,
  ): Promise<bigint> {
    let low = 0n;
    let high = head;
    while (low < high) {
      const mid = (low + high) / 2n;
      const block = await this.client.getBlock({ blockNumber: mid });
      if (block.timestamp < timestamp) {
        low = mid + 1n;
      } else {
        high = mid;
      }
    }
    return low;
  }

  /**
   * Return the `deliverable_url` for a submitted job.
   *
   * Reads the `JobInitialised` event emitted by the policy and parses
   * `optParams` JSON to extract `deliverable_url`. Returns `null` if the
   * event is not found or the job has not been submitted yet.
   *
   * When `hintBlock` is not provided the method self-resolves it by
   * querying Commerce's `JobSubmitted` event first (tight 5-block window
   * around current head, walking back in 1000-block steps until found).
   * This avoids wide log scans that exceed NodeReal's block-range limit.
   */
  async getDeliverableUrl(
    jobId: bigint,
    opts?: GetDeliverableUrlFacadeOpts,
  ): Promise<string | null> {
    const hintBlock =
      opts?.hintBlock ?? (await this.resolveSubmitBlock(jobId)) ?? undefined;
    return this.policy.getDeliverableUrl(jobId, { hintBlock });
  }

  /**
   * Find the block where `JobSubmitted` was emitted for `jobId`.
   *
   * Walks backwards from the current head in `step`-block windows so each
   * individual RPC call stays within NodeReal's 5000-block limit. Returns
   * the block number, or `null` if not found within `lookback`.
   */
  private async resolveSubmitBlock(
    jobId: bigint,
    lookback = 50_000n,
    step = 1_000n,
  ): Promise<bigint | null> {
    let current: bigint;
    try {
      current = await this.client.getBlockNumber();
    } catch {
      return null;
    }

    const floorBound = current > lookback ? current - lookback : 0n;
    for (let end = current; end >= floorBound; end -= step) {
      const start = end - step + 1n > 0n ? end - step + 1n : 0n;
      try {
        const events = await this.commerce.getJobSubmittedEvents(
          start,
          end,
          jobId,
        );
        const blockNumber = events[0]?.blockNumber;
        if (blockNumber !== undefined && blockNumber !== null) {
          return blockNumber;
        }
      } catch {
        // RPC hiccup on this window — try the next one.
      }
    }
    return null;
  }

  /** Simulate the verdict the Router would see right now. */
  async getVerdict(
    jobId: bigint,
    evidence: `0x${string}` = "0x",
  ): Promise<[Verdict, `0x${string}`]> {
    return this.policy.check(jobId, evidence);
  }

  /** Number of jobs the Router currently considers in-flight (audit L03). */
  async inflightJobCount(): Promise<bigint> {
    return this.router.inflightJobCount();
  }

  /** Quorum threshold snapshotted at `dispute()` time (audit L08). */
  async disputeQuorumSnapshot(jobId: bigint): Promise<bigint> {
    return BigInt(await this.policy.disputeQuorumSnapshot(jobId));
  }
}
