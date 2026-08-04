/**
 * `TWAKProvider` — a self-broadcasting wallet backed by the `twak` CLI.
 *
 * The Trust Wallet Agent Kit (`twak`) CLI owns the full build + sign +
 * broadcast lifecycle and exposes only high-level *intent* commands
 * (`erc8004 register`, `erc8183 create-job`, …) plus message signing. It
 * therefore integrates at the **intent layer**: `TWAKProvider` is both a
 * {@link WalletProvider} (it has an address and can sign messages) and an
 * {@link IntentExecutor} (it is its own execution backend). Port of
 * `python/bnbagent/wallets/twak_provider.py` against the same twak
 * v0.20.0 minimum; the capability reference is `docs/twak.md`.
 *
 * Key custody lives entirely inside twak (its keystore + OS keychain).
 * The agent's on-chain identity is the twak wallet address, read from the
 * CLI. Prerequisites (one-time, the caller's responsibility): the CLI
 * installed, API credentials (`twak init` / `TWAK_ACCESS_ID` +
 * `TWAK_HMAC_SECRET`), a wallet, and a reachable password
 * (`TWAK_WALLET_PASSWORD` or the OS keychain) — twak resolves all of
 * these itself; this provider never passes secrets on argv.
 *
 * Node-native niceties over the Python port:
 * - twak is an npm package, so the default binary resolution prefers the
 *   project-local `node_modules/.bin/twak` (add `@trustwallet/cli` as a
 *   dependency — no global install needed) before falling back to PATH.
 * - Every operation shells out **asynchronously** (`execFile`), so a
 *   multi-second broadcast never blocks the event loop. The one sync
 *   surface is the base-class `address` getter / `exists()` probe, which
 *   runs a single cached `wallet address` lookup on first access.
 *
 * Gas: twak sponsors mainnet broadcasts automatically; when the execution
 * context carries a paymaster, its URL is forwarded to every write as
 * `--paymaster-url` (twak >= v0.20.0, REQ-2) so sponsored `bsctestnet`
 * writes work — without one, testnet self-pays.
 */

import { execFileSync } from "node:child_process";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { hashMessage, recoverMessageAddress } from "viem";
import { NETWORKS, type NetworkConfig } from "../../config.js";
import {
  type RelayVerifierOpts,
  type SecondaryConfirmation,
  confirmTxUnseen,
} from "../../core/relayVerifier.js";
import {
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../../errors.js";
import {
  BROADCAST_SELF,
  INTENTS_ERC8004,
  INTENTS_ERC8183,
  X402_PAY,
} from "../capabilities.js";
import {
  UnsupportedWalletOperation,
  WalletIdentityMismatch,
} from "../errors.js";
import {
  ERC8004_REGISTER,
  ERC8004_SET_AGENT_URI,
  ERC8004_SET_METADATA,
  ERC8183_CLAIM_REFUND,
  ERC8183_COMPLETE,
  ERC8183_CREATE_JOB,
  ERC8183_DISPUTE,
  ERC8183_FUND,
  ERC8183_MARK_EXPIRED,
  ERC8183_REGISTER_JOB,
  ERC8183_REJECT,
  ERC8183_SETTLE,
  ERC8183_SET_BUDGET,
  ERC8183_SET_PROVIDER,
  ERC8183_SUBMIT,
  ERC8183_VOTE_REJECT,
  type ExecutionContext,
  type Intent,
  type IntentExecutor,
  type TxResult,
} from "../intents.js";
import type { SignatureResult } from "../walletProvider.js";
import { WalletProvider } from "../walletProvider.js";
import { TwakX402Payer, type TwakX402PayerOptions } from "./x402.js";

/** Default per-CLI-invocation timeout. */
export const DEFAULT_TWAK_TIMEOUT_MS = 120_000;

/** twak v0.20.0's default public-receipt wait before it emits NETWORK_ERROR. */
const TWAK_RECEIPT_TIMEOUT_SECONDS = 60;

/** twak chain keys for BNB Smart Chain (the CLI rejects `bsc-testnet`). */
const DEFAULT_CHAIN = "bsc";
const ALLOWED_CHAINS = new Set(["bsc", "bsctestnet"]);

/**
 * `wallet sign-message --chain` is a *key-family* selector, not a network
 * selector: it accepts `bsc` but rejects `bsctestnet` (S-10,
 * field-verified). EIP-191 carries no chain information and the wallet
 * address is identical on both BNB networks, so this pin is permanently
 * correct regardless of upstream.
 */
const SIGN_MESSAGE_CHAIN = "bsc";

/**
 * SDK network preset name → twak CLI chain key. The single source of this
 * mapping — use it instead of hand-rolling the `bsc-testnet` →
 * `bsctestnet` translation.
 */
export const TWAK_CHAIN_FOR_NETWORK: Readonly<Record<string, string>> = {
  "bsc-mainnet": "bsc",
  "bsc-testnet": "bsctestnet",
};

const ZERO_ADDRESS = `0x${"00".repeat(20)}`;
const ZERO_REASON = `0x${"00".repeat(32)}`;

type CanonicalContractKey =
  | "registryContract"
  | "commerceContract"
  | "routerContract"
  | "policyContract";

const NETWORK_FOR_TWAK_CHAIN = {
  bsc: "bsc-mainnet",
  bsctestnet: "bsc-testnet",
} as const;

const CONTRACT_KEY_BY_INTENT: Readonly<Record<string, CanonicalContractKey>> = {
  [ERC8004_REGISTER]: "registryContract",
  [ERC8004_SET_METADATA]: "registryContract",
  [ERC8004_SET_AGENT_URI]: "registryContract",
  [ERC8183_CREATE_JOB]: "commerceContract",
  [ERC8183_SET_PROVIDER]: "commerceContract",
  [ERC8183_SET_BUDGET]: "commerceContract",
  [ERC8183_FUND]: "commerceContract",
  [ERC8183_SUBMIT]: "commerceContract",
  [ERC8183_COMPLETE]: "commerceContract",
  [ERC8183_REJECT]: "commerceContract",
  [ERC8183_CLAIM_REFUND]: "commerceContract",
  [ERC8183_REGISTER_JOB]: "routerContract",
  [ERC8183_SETTLE]: "routerContract",
  [ERC8183_MARK_EXPIRED]: "routerContract",
  [ERC8183_DISPUTE]: "policyContract",
  [ERC8183_VOTE_REJECT]: "policyContract",
};

// Appended to command-failure errors: the failure is most often a missing
// one-time setup step, so point the caller at the fix without claiming the
// exact cause (twak's own message is preserved ahead of this).
const SETUP_HINT =
  "If this is a setup issue, ensure twak is configured: " +
  "(1) credentials via `twak init --api-key <id> --api-secret <secret>` " +
  "or the TWAK_ACCESS_ID / TWAK_HMAC_SECRET env vars; " +
  "(2) a wallet via `twak wallet create --password <pw>`; " +
  "(3) the password reachable via TWAK_WALLET_PASSWORD or " +
  "`twak wallet keychain save`.";

const INSTALL_HINT =
  "Install it with `npm install @trustwallet/cli` (a project dependency — " +
  "the provider resolves node_modules/.bin/twak automatically) or " +
  "`npm install -g @trustwallet/cli`, then configure it (see the " +
  "TWAKProvider prerequisites).";

/** One finished CLI invocation, success or not. */
interface TwakExecResult {
  code: number;
  stdout: string;
  stderr: string;
}

interface TwakReceiptTimeout {
  txHash: `0x${string}`;
  chain: string;
}

function parseReceiptTimeout(
  result: TwakExecResult,
): TwakReceiptTimeout | null {
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (
    payload.errorCode !== "NETWORK_ERROR" ||
    typeof payload.error !== "string"
  ) {
    return null;
  }
  const match =
    /^Timed out waiting for receipt (0x[0-9a-fA-F]{64}) on (\S+)$/.exec(
      payload.error.trim(),
    );
  return match
    ? {
        txHash: match[1] as `0x${string}`,
        chain: match[2] as string,
      }
    : null;
}

type TwakExec = (
  bin: string,
  args: string[],
  opts: { timeoutMs: number; env: NodeJS.ProcessEnv | undefined },
) => Promise<TwakExecResult>;

type TwakExecSync = (
  bin: string,
  args: string[],
  opts: { timeoutMs: number; env: NodeJS.ProcessEnv | undefined },
) => TwakExecResult;

/** Node's execFile error shape (exit info rides on the thrown error). */
interface ExecError {
  code?: unknown;
  status?: number | null;
  killed?: boolean;
  signal?: string | null;
  stdout?: string | Buffer;
  stderr?: string | Buffer;
}

function isEnoent(error: unknown): boolean {
  return (error as ExecError | null)?.code === "ENOENT";
}

function text(value: string | Buffer | undefined): string {
  return value === undefined ? "" : value.toString();
}

const realExec: TwakExec = (bin, args, opts) =>
  new Promise((resolve, reject) => {
    execFile(
      bin,
      args,
      { timeout: opts.timeoutMs, env: opts.env, maxBuffer: 16 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error && (isEnoent(error) || (error as ExecError).killed)) {
          reject(error);
          return;
        }
        const status = error ? ((error as ExecError).status ?? 1) : 0;
        resolve({
          code: typeof status === "number" ? status : 1,
          stdout: text(stdout),
          stderr: text(stderr),
        });
      },
    );
  });

const realExecSync: TwakExecSync = (bin, args, opts) => {
  try {
    const stdout = execFileSync(bin, args, {
      timeout: opts.timeoutMs,
      env: opts.env,
      maxBuffer: 16 * 1024 * 1024,
    });
    return { code: 0, stdout: text(stdout), stderr: "" };
  } catch (error) {
    if (isEnoent(error) || (error as ExecError).killed) {
      throw error;
    }
    const e = error as ExecError;
    return {
      code: e.status ?? 1,
      stdout: text(e.stdout as string | Buffer | undefined),
      stderr: text(e.stderr as string | Buffer | undefined),
    };
  }
};

let execImpl: TwakExec = realExec;
let execSyncImpl: TwakExecSync = realExecSync;

/**
 * Swap the subprocess layer (or restore it with `null`s). Test hook only —
 * lets the full envelope/argv logic run without a real twak binary.
 */
export function _setTwakExecForTests(
  exec: TwakExec | null,
  execSync?: TwakExecSync | null,
): void {
  execImpl = exec ?? realExec;
  execSyncImpl =
    execSync ??
    (exec
      ? () => {
          throw new Error("sync twak exec not stubbed for this test");
        }
      : realExecSync);
}

/**
 * The default twak binary: the project-local `node_modules/.bin/twak`
 * when present (twak installed as a dependency — the recommended Node
 * setup), otherwise `"twak"` from PATH.
 */
export function resolveTwakBin(cwd: string = process.cwd()): string {
  const local = join(cwd, "node_modules", ".bin", "twak");
  return existsSync(local) ? local : "twak";
}

/** Options accepted by the {@link TWAKProvider} constructor. */
export interface TWAKProviderOptions {
  /**
   * twak chain key — `"bsc"` (mainnet, default) or `"bsctestnet"`.
   * ERC-8004/8183 are deployed on both. (The spec's `bsc-testnet`
   * spelling is rejected by the real CLI — see
   * {@link TWAK_CHAIN_FOR_NETWORK}.)
   */
  chain?: string;
  /** Path to (or name of) the twak executable. Default: {@link resolveTwakBin}. */
  twakBin?: string;
  /** Per-command timeout in milliseconds. Default 120 000. */
  timeoutMs?: number;
  /**
   * When set, every twak subprocess runs with `HOME=<home>` so twak
   * resolves its state under `<home>/.twak` instead of the OS user's home
   * (Node's `os.homedir()` reads `$HOME` — field-verified). Solves
   * read-only code mounts (materialize into a writable dir), multi-agent
   * isolation on one OS user, and test isolation. See
   * `materializeTwakHome` for feeding it from a secret bundle.
   */
  home?: string;
  /**
   * Pin the wallet's on-chain identity: the first successful address
   * lookup is compared case-insensitively and a mismatch throws
   * {@link WalletIdentityMismatch} before any state-changing operation
   * (INV-4).
   */
  expectedAddress?: string;
  /**
   * Secondary-confirmation seam for sponsored receipt timeouts; tests
   * replace the real multi-RPC probe. Default: {@link confirmTxUnseen}.
   */
  confirmTxUnseen?: (
    hash: `0x${string}`,
    opts: RelayVerifierOpts,
  ) => Promise<SecondaryConfirmation>;
  /**
   * `true` (default, dev-machine parity with `EVMWalletProvider`): a
   * missing wallet is created lazily on the first operation. Deployments
   * must pass `false`: the wallet may only come from materialization
   * (`materializeTwakHome`, fed from the `TWAK_WALLET_JSON` bundle key)
   * and a missing wallet throws instead of silently minting a new
   * on-chain identity (INV-4).
   */
  autoCreate?: boolean;
}

type IntentHandler = (
  provider: TWAKProvider,
  kwargs: Record<string, unknown>,
) => Promise<TxResult>;

/**
 * Wallet + execution backend delegating to the `twak` CLI (see the module
 * docstring). Capabilities: `sign.message` (auto-derived from the
 * override), `broadcast.self`, `intents.erc8004`, `intents.erc8183`,
 * `x402.pay` (via the delegated {@link TwakX402Payer}). No
 * `sign.transaction` / `sign.typed_data` — twak exposes no raw-tx or
 * generic EIP-712 primitive (design decision P0); use the EVM wallet for
 * those.
 */
export class TWAKProvider extends WalletProvider implements IntentExecutor {
  static override readonly kind = "twak";

  // twak's `erc8183 fund` does approve + deposit itself, so the SDK
  // facade skips its own allowance top-up for this wallet (literal
  // boolean — the ERC8183Client gate is `=== true`).
  override readonly fundBundlesApproval = true;

  protected override readonly extraCapabilities: ReadonlySet<string> = new Set([
    BROADCAST_SELF,
    INTENTS_ERC8004,
    INTENTS_ERC8183,
    X402_PAY,
  ]);

  readonly #chain: string;
  readonly #twakBin: string;
  readonly #timeoutMs: number;
  readonly #home: string | undefined;
  readonly #expectedAddress: string | undefined;
  readonly #autoCreate: boolean;
  readonly #confirmTxUnseen: (
    hash: `0x${string}`,
    opts: RelayVerifierOpts,
  ) => Promise<SecondaryConfirmation>;
  #address: `0x${string}` | null = null;
  #ensured = false; // guards the one-shot lazy auto-create
  /**
   * Captured from the ExecutionContext (makeExecutor); forwarded to every
   * write command as `--paymaster-url` (twak v0.20.0, REQ-2).
   */
  #paymasterUrl: string | null = null;
  /** Public RPC client used only to reconcile sponsored receipt timeouts. */
  #publicClient: ExecutionContext["client"] | null = null;

  constructor(opts: TWAKProviderOptions = {}) {
    super();
    const chain = (opts.chain ?? DEFAULT_CHAIN).toLowerCase();
    if (!ALLOWED_CHAINS.has(chain)) {
      throw new Error(
        `TWAKProvider supports BNB Smart Chain only — chain must be one of ${[...ALLOWED_CHAINS].sort().join(", ")} (got ${JSON.stringify(opts.chain)}). ERC-8004/8183 are deployed on bsc (mainnet) and bsctestnet (note: twak's testnet key is 'bsctestnet', not 'bsc-testnet').`,
      );
    }
    this.#chain = chain;
    this.#twakBin = opts.twakBin ?? resolveTwakBin();
    this.#timeoutMs = opts.timeoutMs ?? DEFAULT_TWAK_TIMEOUT_MS;
    this.#home = opts.home;
    this.#expectedAddress = opts.expectedAddress;
    this.#autoCreate = opts.autoCreate ?? true;
    this.#confirmTxUnseen = opts.confirmTxUnseen ?? confirmTxUnseen;
  }

  /** The twak chain key this provider is pinned to. */
  get chain(): string {
    return this.#chain;
  }

  // ── subprocess plumbing ──

  #env(): NodeJS.ProcessEnv | undefined {
    return this.#home !== undefined
      ? { ...process.env, HOME: this.#home }
      : undefined;
  }

  /** Parse one finished invocation with the field-verified envelope quirks. */
  #interpret(cmd: string[], result: TwakExecResult): Record<string, unknown> {
    const parse = (): Record<string, unknown> | null => {
      if (!result.stdout.trim()) {
        return {};
      }
      try {
        return JSON.parse(result.stdout) as Record<string, unknown>;
      } catch {
        return null;
      }
    };
    if (result.code !== 0) {
      // Quirk (field-verified v0.18.0): `x402 quote` exits non-zero on an
      // empty `accepts` list while emitting an explicit success envelope.
      // When the envelope and the exit code disagree in the *success*
      // direction, trust the envelope.
      const data = parse() ?? {};
      if (data.success === true && !data.error) {
        return data;
      }
      throw new Error(this.#formatError(cmd, result.stdout, result.stderr));
    }
    const data = parse();
    if (data === null) {
      throw new Error(
        `twak returned non-JSON output for ${redact(cmd)}: ${JSON.stringify(result.stdout.slice(0, 500))}`,
      );
    }
    // The *absence* of `success` is never trusted as success — the real
    // CLI omits the field inconsistently across error envelopes.
    if (data.error || data.success === false) {
      throw new Error(this.#formatError(cmd, result.stdout, result.stderr));
    }
    return data;
  }

  /** Run `twak <args> --json` asynchronously and return the parsed envelope. */
  async _run(args: string[]): Promise<Record<string, unknown>> {
    const cmd = [this.#twakBin, ...args, "--json"];
    let result: TwakExecResult;
    try {
      result = await execImpl(this.#twakBin, [...args, "--json"], {
        timeoutMs: this.#timeoutMs,
        env: this.#env(),
      });
    } catch (error) {
      throw this.#spawnError(cmd, error);
    }
    const receiptTimeout =
      this.#paymasterUrl === null ? null : parseReceiptTimeout(result);
    if (receiptTimeout?.chain === this.#chain) {
      throw await this.#classifySponsoredReceiptTimeout(receiptTimeout);
    }
    return this.#interpret(cmd, result);
  }

  async #classifySponsoredReceiptTimeout(
    timeout: TwakReceiptTimeout,
  ): Promise<Error> {
    let publicTxVisible = false;
    try {
      const transaction = await this.#publicClient?.getTransaction({
        hash: timeout.txHash,
      });
      publicTxVisible = transaction !== null && transaction !== undefined;
    } catch {
      publicTxVisible = false;
    }
    let secondary: SecondaryConfirmation | "not-checked" = "not-checked";
    if (!publicTxVisible) {
      // Corroborate with independent RPC endpoints before declaring the
      // relay hash unverified — the primary RPC alone may be lagging.
      const networkName =
        NETWORK_FOR_TWAK_CHAIN[
          this.#chain as keyof typeof NETWORK_FOR_TWAK_CHAIN
        ];
      const chainId = NETWORKS[networkName]?.chainId;
      try {
        secondary = await this.#confirmTxUnseen(timeout.txHash, {
          chainId: chainId ?? 0,
        });
      } catch {
        secondary = "inconclusive";
      }
      if (secondary === "seen") {
        publicTxVisible = true;
      }
    }
    if (publicTxVisible) {
      const pending = new TransactionPendingError(
        timeout.txHash,
        TWAK_RECEIPT_TIMEOUT_SECONDS,
        `TWAK sponsored transaction ${timeout.txHash} is visible on the public chain, but TWAK timed out waiting for its receipt. Do not retry until the transaction or job state is reconciled.`,
      );
      pending.relayStatus = "receipt_timeout_but_tx_visible";
      return pending;
    }
    const unverified = new RelaySubmissionUnverifiedError(
      timeout.txHash,
      TWAK_RECEIPT_TIMEOUT_SECONDS,
      `TWAK sponsored relay returned transaction ${timeout.txHash}, but the SDK could not verify it on the public chain after TWAK timed out waiting for its receipt. Do not retry blindly; reconcile the relay transaction and wallet nonce before issuing another write.`,
    );
    if (secondary === "confirmed-unseen" || secondary === "inconclusive") {
      unverified.secondaryRpcResult = secondary;
    }
    return unverified;
  }

  /** Sync variant for the base-class `address` getter / `exists()` only. */
  #runSync(args: string[]): Record<string, unknown> {
    const cmd = [this.#twakBin, ...args, "--json"];
    let result: TwakExecResult;
    try {
      result = execSyncImpl(this.#twakBin, [...args, "--json"], {
        timeoutMs: this.#timeoutMs,
        env: this.#env(),
      });
    } catch (error) {
      throw this.#spawnError(cmd, error);
    }
    return this.#interpret(cmd, result);
  }

  #spawnError(cmd: string[], error: unknown): Error {
    if (isEnoent(error)) {
      return new Error(
        `twak CLI not found (looked for ${JSON.stringify(this.#twakBin)}). ${INSTALL_HINT}`,
        { cause: error },
      );
    }
    return new Error(
      `twak command timed out after ${this.#timeoutMs}ms: ${redact(cmd)}`,
      { cause: error },
    );
  }

  /** Build a helpful error message, surfacing any structured error. */
  #formatError(cmd: string[], stdout: string, stderr: string): string {
    let detail = stderr.trim() || stdout.trim();
    try {
      const payload = JSON.parse(stdout) as Record<string, unknown>;
      const err = payload.error;
      if (err && typeof err === "object") {
        const e = err as Record<string, unknown>;
        detail = String(e.message ?? e.name ?? e.selector ?? detail);
      } else if (typeof err === "string") {
        detail = err;
      }
    } catch {
      // keep the raw stream detail
    }
    // "unknown command/option" means the installed twak predates the
    // command surface this provider targets — point at the upgrade.
    const combined = `${stderr} ${stdout}`;
    const hint =
      combined.includes("unknown command") ||
      combined.includes("unknown option")
        ? "The installed twak CLI does not recognise this command/option — " +
          "upgrade twak to >= v0.20.0 (`npm install @trustwallet/cli`)."
        : SETUP_HINT;
    return `twak command failed (${redact(cmd)}): ${detail || "<no output>"}. ${hint}`;
  }

  // ── WalletProvider ──

  /**
   * The twak wallet address (cached after first lookup). The first access
   * runs a **synchronous** CLI lookup (base-class contract: `address` is
   * a sync getter) — any async operation before it warms the cache.
   */
  override get address(): `0x${string}` {
    if (this.#address === null) {
      this.#ensureSync();
      this.#acceptAddress(this.#runSync(this.#addressArgs()));
    }
    return this.#address as `0x${string}`;
  }

  #addressArgs(): string[] {
    return ["wallet", "address", "--chain", this.#chain];
  }

  /**
   * Validate + cache a `wallet address` envelope. The `expectedAddress`
   * identity check lives here, before the cache is populated: on a
   * mismatch nothing is cached, so every subsequent attempt re-checks and
   * re-throws instead of operating under a drifted identity (INV-4).
   */
  #acceptAddress(data: Record<string, unknown>): `0x${string}` {
    const addr = (data.address ?? data.wallet) as string | undefined;
    if (!addr) {
      throw new Error(
        `twak \`wallet address\` did not return an address: ${JSON.stringify(data)}`,
      );
    }
    if (
      this.#expectedAddress !== undefined &&
      addr.toLowerCase() !== this.#expectedAddress.toLowerCase()
    ) {
      throw new WalletIdentityMismatch({
        expected: this.#expectedAddress,
        actual: addr,
      });
    }
    this.#address = addr as `0x${string}`;
    return this.#address;
  }

  override get keyLocation(): string {
    const base = this.#home ?? "~";
    return `${base}/.twak/wallet.json (encrypted by the twak CLI) + OS keychain/TWAK_WALLET_PASSWORD`;
  }

  /**
   * True if twak reports a configured wallet (best-effort, sync — the
   * base contract). The CLI exits 0 even when no wallet is configured, so
   * the `agentWallet` field is the actual signal; any failure is treated
   * as "does not exist" rather than raising.
   */
  override exists(): boolean {
    try {
      const data = this.#runSync(["wallet", "status"]);
      return data.agentWallet === "configured";
    } catch {
      return false;
    }
  }

  /** Async twin of {@link exists} used by the operation paths. */
  async #existsAsync(): Promise<boolean> {
    try {
      const data = await this._run(["wallet", "status"]);
      return data.agentWallet === "configured";
    } catch {
      return false;
    }
  }

  #missingWalletError(): Error {
    return new Error(
      "twak wallet not found and autoCreate=false (deployment mode): " +
        "refusing to create a new on-chain identity implicitly (INV-4). " +
        "Materialize the wallet first — materializeTwakHome({ walletJson, " +
        "home }) with the TWAK_WALLET_JSON secret-bundle value — then retry.",
    );
  }

  /**
   * Attempt `twak wallet create` (no secrets on argv — the password must
   * come from TWAK_WALLET_PASSWORD / the keychain). twak versions that
   * hard-require `--password` on the command line get their commander
   * error mapped to actionable guidance instead (gaps S-8; the SDK never
   * puts secrets on argv, visible in `ps`).
   */
  #mapCreateError(error: unknown): Error {
    const detail = String(error).toLowerCase();
    if (detail.includes("required option") && detail.includes("--password")) {
      return new Error(
        "twak requires the wallet password on the command line for " +
          "`wallet create`, which this SDK refuses to do (secrets on argv " +
          "are visible to every process). Create the wallet manually once — " +
          "`twak wallet create --password <pw>` in an interactive shell — " +
          "or materialize an existing wallet.json via materializeTwakHome().",
        { cause: error },
      );
    }
    return error as Error;
  }

  /** One-shot lazy auto-create (or existence check) on the first operation. */
  async #ensure(): Promise<void> {
    if (!this.#ensured) {
      this.#ensured = true;
      if (!(await this.#existsAsync())) {
        if (!this.#autoCreate) {
          throw this.#missingWalletError();
        }
        try {
          await this._run(["wallet", "create"]);
        } catch (error) {
          throw this.#mapCreateError(error);
        }
        this.#address = null; // re-read the fresh wallet's address
      }
    }
    if (this.#expectedAddress !== undefined && this.#address === null) {
      // identity check before any state-changing operation (INV-4)
      this.#acceptAddress(await this._run(this.#addressArgs()));
    }
  }

  /** Sync twin of {@link #ensure} for the `address` getter. */
  #ensureSync(): void {
    if (!this.#ensured) {
      this.#ensured = true;
      if (!this.exists()) {
        if (!this.#autoCreate) {
          throw this.#missingWalletError();
        }
        try {
          this.#runSync(["wallet", "create"]);
        } catch (error) {
          throw this.#mapCreateError(error);
        }
        this.#address = null;
      }
    }
  }

  /**
   * Sign a message via `twak wallet sign-message` (EIP-191, text
   * semantics — twak >= v0.19.1 signs the input as text always). Three
   * adaptations over the raw CLI output (gaps S-4): `0x` normalization,
   * a client-side EIP-191 digest, and an ecrecover self-check against the
   * wallet address — we compute the digest but twak produced the
   * signature, so the recovery round-trip is the only runtime proof both
   * sides agree on the message bytes.
   */
  override async signMessage(message: string): Promise<SignatureResult> {
    await this.#ensure();
    const data = await this._run([
      "wallet",
      "sign-message",
      "--chain",
      SIGN_MESSAGE_CHAIN,
      "--message",
      message,
    ]);
    let signature = data.signature as string | undefined;
    if (!signature) {
      throw new Error(
        `twak \`sign-message\` returned no signature: ${JSON.stringify(data)}`,
      );
    }
    if (!signature.startsWith("0x")) {
      signature = `0x${signature}`;
    }
    const digest = hashMessage(message);
    let recovered: string;
    try {
      recovered = await recoverMessageAddress({
        message,
        signature: signature as `0x${string}`,
      });
    } catch (error) {
      throw new Error(
        `twak \`sign-message\` returned a malformed signature (${signature.slice(0, 20)}…): ${String(error)}`,
        { cause: error },
      );
    }
    const address = await this.#resolvedAddress();
    if (recovered.toLowerCase() !== address.toLowerCase()) {
      throw new Error(
        `twak sign-message self-check failed: signature recovers to ${recovered}, expected the wallet address ${address}. The SDK computes the EIP-191 digest client-side while twak signs out of process — a recovery mismatch means the two sides encoded the message bytes differently, and using this signature would fail verification later. Refusing to return it. Known cause: twak <= v0.19.0 hex-decodes a 0x-shaped message and signs the raw bytes (fixed in v0.19.1). Fix: upgrade twak to >= v0.20.0 (\`npm install @trustwallet/cli\`), or switch this agent to the EVM wallet (see docs/twak.md).`,
      );
    }
    return {
      messageHash: digest,
      signature: signature as `0x${string}`,
      ...splitSignature(signature),
    };
  }

  /** The cached address, resolving it asynchronously when cold. */
  async #resolvedAddress(): Promise<`0x${string}`> {
    if (this.#address === null) {
      this.#acceptAddress(await this._run(this.#addressArgs()));
    }
    return this.#address as `0x${string}`;
  }

  // signTransaction / signTypedData are deliberately NOT overridden: twak
  // exposes no raw-tx or generic EIP-712 primitive (design decision P0).
  // The base defaults raise, keeping sign.transaction / sign.typed_data
  // out of capabilities().

  // ── x402 raw transport (policy lives in TwakX402Payer) ──

  /**
   * Fetch the x402 payment challenge for `url` (read-only, no payment).
   * Deliberately NO ensure here (design F-3): a quote is a wallet-less
   * challenge fetch and must never trigger the existence check.
   */
  async x402Quote(
    url: string,
    opts?: { method?: string; body?: string },
  ): Promise<Record<string, unknown>> {
    const args = ["x402", "quote", url];
    if (opts?.method && opts.method !== "GET") {
      args.push("--method", opts.method);
    }
    if (opts?.body !== undefined) {
      args.push("--body", opts.body);
    }
    return this._run(args);
  }

  /**
   * Make a paid x402 request via `twak x402 request` (twak builds, signs
   * and settles the payment). `maxPayment` is the hard per-payment cap
   * twak itself enforces; the `prefer*` options pin the challenge route
   * (TOCTOU backstop between a prior quote and this request). On success
   * the JSON is the paid endpoint's response body verbatim, with no
   * payment-receipt metadata (gaps S-7).
   */
  async x402Request(
    url: string,
    opts: {
      maxPayment: bigint | number | string;
      method?: string;
      body?: string;
      preferNetwork?: string;
      preferMethod?: string;
      preferAsset?: string;
      autoApprove?: boolean;
    },
  ): Promise<Record<string, unknown>> {
    await this.#ensure();
    const args = [
      "x402",
      "request",
      url,
      "--max-payment",
      String(opts.maxPayment),
      "--yes",
    ];
    if (opts.method && opts.method !== "GET") {
      args.push("--method", opts.method);
    }
    if (opts.body !== undefined) {
      args.push("--body", opts.body);
    }
    if (opts.preferNetwork) {
      args.push("--prefer-network", opts.preferNetwork);
    }
    if (opts.preferMethod) {
      args.push("--prefer-method", opts.preferMethod);
    }
    if (opts.preferAsset) {
      args.push("--prefer-asset", opts.preferAsset);
    }
    if (opts.autoApprove) {
      args.push("--auto-approve");
    }
    return this._run(args);
  }

  /**
   * Delegated x402 payer: twak builds/signs/settles the payment while the
   * payer enforces the SDK-side policy (recipient/asset pinning, per-call
   * cap precheck, session budget). `payerKwargs` are forwarded verbatim.
   */
  override makeX402Payer(payerKwargs?: Record<string, unknown>): TwakX402Payer {
    return new TwakX402Payer(this, payerKwargs as TwakX402PayerOptions);
  }

  // ── IntentExecutor ──

  /**
   * This wallet broadcasts its own transactions, so it *is* its own
   * executor. It does not use the web3 `context` client to broadcast, but
   * retains it to reconcile a relay hash after twak's receipt timeout. A
   * paymaster IS honoured (twak v0.20.0, REQ-2): its URL is forwarded to
   * every write command as `--paymaster-url`. Because the executor is the
   * provider itself, the latest `makeExecutor` context wins — the supported
   * shape is one client per provider (which is how the facades construct
   * them).
   */
  override makeExecutor(context: ExecutionContext): IntentExecutor {
    const paymaster = context.paymaster;
    const url = paymaster ? paymaster.paymasterUrl : null;
    if (paymaster && !url) {
      console.warn(
        "[TWAKProvider] context.paymaster has no paymasterUrl — ignoring " +
          "it. twak sponsorship needs a URL to forward as --paymaster-url " +
          "(v0.20.0).",
      );
    }
    this.#paymasterUrl = url ?? null;
    this.#publicClient = context.client;
    return this;
  }

  /** Execute a high-level intent by delegating to the twak CLI. */
  async execute(intent: Intent): Promise<TxResult> {
    // Validate first so an unsupported intent never triggers the wallet
    // existence probe or any CLI call.
    const handler = intent.name ? INTENT_HANDLERS[intent.name] : undefined;
    if (!handler) {
      const supported = Object.keys(INTENT_HANDLERS).sort().join(", ");
      throw new UnsupportedWalletOperation(
        `intent ${JSON.stringify(intent.name ?? null)}`,
        {
          reason: `twak cannot execute arbitrary contract calls — it only speaks a fixed command menu (supported intents: ${supported})`,
          alternative: "use an EVM wallet for arbitrary contract calls",
        },
      );
    }
    const actualTarget = intent.call?.address;
    const contractKey = CONTRACT_KEY_BY_INTENT[intent.name ?? ""];
    if (actualTarget && contractKey) {
      const networkName =
        NETWORK_FOR_TWAK_CHAIN[
          this.#chain as keyof typeof NETWORK_FOR_TWAK_CHAIN
        ];
      const canonicalTarget = NETWORKS[networkName][
        contractKey
      ] as NetworkConfig[CanonicalContractKey];
      const registryOverride =
        contractKey === "registryContract"
          ? process.env.ERC8004_REGISTRY_ADDRESS?.trim()
          : undefined;
      const twakTarget = registryOverride || canonicalTarget;
      if (actualTarget.toLowerCase() !== twakTarget.toLowerCase()) {
        const targetReason =
          contractKey === "registryContract"
            ? `twak v0.20.0 will target ${twakTarget} on ${networkName} (${registryOverride ? "ERC8004_REGISTRY_ADDRESS override" : "canonical registry"}); the SDK intent targets ${actualTarget}`
            : `twak v0.20.0 targets only the canonical ${contractKey} ${canonicalTarget} on ${networkName}; silently using it would execute against the wrong deployment`;
        throw new UnsupportedWalletOperation(
          `intent ${JSON.stringify(intent.name)} on custom contract ${actualTarget}`,
          {
            reason: targetReason,
            alternative:
              contractKey === "registryContract"
                ? "set ERC8004_REGISTRY_ADDRESS to the intended registry, or make the SDK intent use the configured registry"
                : "use an EVM wallet for custom ERC-8183 contracts, or use the canonical contract stack",
          },
        );
      }
    }
    await this.#ensure();
    return handler(this, intent.kwargs ?? {});
  }

  /** `--paymaster-url <url>` when the execution context carried one (v0.20.0, REQ-2). */
  _paymasterArgs(): string[] {
    return this.#paymasterUrl ? ["--paymaster-url", this.#paymasterUrl] : [];
  }

  /** Canonical executor result envelope for a twak write command. */
  _txResult(
    data: Record<string, unknown>,
    extra?: Record<string, unknown>,
  ): TxResult {
    const hash = extractTxHash(data);
    if (!hash) {
      throw new Error(
        `twak write command returned no transaction hash: ${JSON.stringify(data).slice(0, 300)}`,
      );
    }
    return {
      transactionHash: hash,
      status: 1,
      receipt: null,
      ...extra,
    };
  }

  /** Run `twak erc8183 <command> <jobId> [extra…] --chain <chain>`. */
  async _erc8183(
    command: string,
    jobId: unknown,
    ...extra: string[]
  ): Promise<TxResult> {
    const data = await this._run([
      "erc8183",
      command,
      String(jobId),
      ...extra,
      ...this._paymasterArgs(),
      "--chain",
      this.#chain,
    ]);
    return this._txResult(data);
  }

  /** @internal shared by the erc8004 handlers. */
  async _erc8004(args: string[]): Promise<Record<string, unknown>> {
    return this._run([
      "erc8004",
      ...args,
      ...this._paymasterArgs(),
      "--chain",
      this.#chain,
    ]);
  }
}

// ── intent handlers (module-level table; called as handler(provider, kwargs)) ──

/** `--opt-params 0x<hex>` when the caller sent non-empty optParams. */
function optParamsArgs(kwargs: Record<string, unknown>): string[] {
  const optParams = kwargs.optParams as string | undefined;
  return optParams && optParams !== "0x" ? ["--opt-params", optParams] : [];
}

/**
 * twak emits numeric ids as JSON strings ("150" — field-verified); the
 * local executor path yields them from event logs. Normalize so both
 * backends honour the same envelope.
 */
function asBigInt(value: unknown): bigint | null {
  return value === undefined || value === null ? null : BigInt(value as string);
}

function asNumber(value: unknown): number | null {
  return value === undefined || value === null ? null : Number(value);
}

/**
 * Pull the tx hash out of a twak result envelope, tolerantly: the spec
 * says `txHash` but the real CLI returns `hash` (gaps REQ-3).
 */
function extractTxHash(data: Record<string, unknown>): `0x${string}` | null {
  const hash = data.hash ?? data.txHash ?? data.transactionHash;
  return typeof hash === "string" ? (hash as `0x${string}`) : null;
}

/** Split a 65-byte 0x signature into r / s / v (zeroed when malformed). */
function splitSignature(signature: string): {
  r: `0x${string}`;
  s: `0x${string}`;
  v: bigint;
} {
  const sig = signature.startsWith("0x") ? signature.slice(2) : signature;
  if (sig.length !== 130) {
    return { r: "0x0", s: "0x0", v: 0n };
  }
  return {
    r: `0x${sig.slice(0, 64)}`,
    s: `0x${sig.slice(64, 128)}`,
    v: BigInt(`0x${sig.slice(128, 130)}`),
  };
}

/** Render a command for logs (no secrets are passed as args, but be safe). */
function redact(cmd: string[]): string {
  return cmd.join(" ");
}

async function handleRegister(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  const metadata =
    (kwargs.metadata as { key: string; value: string }[] | undefined) ?? [];
  // --metadata is repeatable and atomic with the mint, so all entries
  // (including the facade-injected built_with) ride the register tx.
  const args = ["register", "--uri", String(kwargs.agentUri)];
  for (const entry of metadata) {
    args.push("--metadata", `${entry.key}=${entry.value}`);
  }
  const data = await p._erc8004(args);
  return p._txResult(data, {
    agentId: asNumber(data.agentId),
    owner: data.owner,
  });
}

async function handleSetMetadata(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  const data = await p._erc8004([
    "set-metadata",
    String(kwargs.agentId),
    "--key",
    String(kwargs.key),
    "--value",
    String(kwargs.value),
  ]);
  return p._txResult(data);
}

async function handleSetAgentUri(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  const data = await p._erc8004([
    "set-uri",
    String(kwargs.agentId),
    "--uri",
    String(kwargs.agentUri),
  ]);
  return p._txResult(data);
}

async function handleCreateJob(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  const args = [
    "erc8183",
    "create-job",
    "--provider",
    String(kwargs.provider),
    "--evaluator",
    String(kwargs.evaluator),
    "--expires-at",
    String(kwargs.expiredAt),
    "--description",
    String(kwargs.description),
  ];
  const hook = kwargs.hook as string | undefined;
  if (hook && hook.toLowerCase() !== ZERO_ADDRESS) {
    args.push("--hook", hook);
  }
  const data = await p._run([
    ...args,
    ...p._paymasterArgs(),
    "--chain",
    p.chain,
  ]);
  return p._txResult(data, { jobId: asBigInt(data.jobId) });
}

async function handleSetProvider(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183(
    "set-provider",
    kwargs.jobId,
    "--provider",
    String(kwargs.provider),
    ...optParamsArgs(kwargs),
  );
}

async function handleSetBudget(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183(
    "set-budget",
    kwargs.jobId,
    "--amount",
    String(kwargs.amount),
    ...optParamsArgs(kwargs),
  );
}

async function handleFund(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  // --expected-budget pins the amount atomically — the contract reverts
  // with BudgetMismatch() if the on-chain budget differs (gaps S-2).
  const data = await p._run([
    "erc8183",
    "fund",
    String(kwargs.jobId),
    "--expected-budget",
    String(kwargs.expectedBudget),
    ...optParamsArgs(kwargs),
    ...p._paymasterArgs(),
    "--chain",
    p.chain,
  ]);
  const extra: Record<string, unknown> = {};
  if (data.approveHash) {
    extra.approveHash = data.approveHash;
  }
  return p._txResult(data, extra);
}

async function handleSubmit(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  // optParams pass through raw (REQ-1), so the deliverable_url JSON the
  // SDK facade encodes there reaches the policy's JobInitialised event —
  // the seller role works end-to-end.
  return p._erc8183(
    "submit",
    kwargs.jobId,
    "--deliverable",
    String(kwargs.deliverable),
    ...optParamsArgs(kwargs),
  );
}

function reasonArgs(kwargs: Record<string, unknown>): string[] {
  const reason = kwargs.reason as string | undefined;
  // twak defaults --reason to zero bytes32
  return reason && reason !== ZERO_REASON ? ["--reason", reason] : [];
}

async function handleComplete(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183(
    "complete",
    kwargs.jobId,
    ...reasonArgs(kwargs),
    ...optParamsArgs(kwargs),
  );
}

async function handleReject(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183(
    "reject",
    kwargs.jobId,
    ...reasonArgs(kwargs),
    ...optParamsArgs(kwargs),
  );
}

async function handleClaimRefund(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183("claim-refund", kwargs.jobId);
}

async function handleRegisterJob(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183(
    "register-job",
    kwargs.jobId,
    "--policy",
    String(kwargs.policy),
  );
}

async function handleSettle(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  const evidence = kwargs.evidence as string | undefined;
  const extra = evidence && evidence !== "0x" ? ["--evidence", evidence] : [];
  return p._erc8183("settle", kwargs.jobId, ...extra);
}

async function handleMarkExpired(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183("mark-expired", kwargs.jobId);
}

async function handleDispute(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183("dispute", kwargs.jobId);
}

async function handleVoteReject(
  p: TWAKProvider,
  kwargs: Record<string, unknown>,
): Promise<TxResult> {
  return p._erc8183("vote-reject", kwargs.jobId);
}

/** Dispatch table: intent name → handler. */
const INTENT_HANDLERS: Record<string, IntentHandler> = {
  [ERC8004_REGISTER]: handleRegister,
  [ERC8004_SET_METADATA]: handleSetMetadata,
  [ERC8004_SET_AGENT_URI]: handleSetAgentUri,
  [ERC8183_CREATE_JOB]: handleCreateJob,
  [ERC8183_SET_PROVIDER]: handleSetProvider,
  [ERC8183_SET_BUDGET]: handleSetBudget,
  [ERC8183_FUND]: handleFund,
  [ERC8183_SUBMIT]: handleSubmit,
  [ERC8183_COMPLETE]: handleComplete,
  [ERC8183_REJECT]: handleReject,
  [ERC8183_CLAIM_REFUND]: handleClaimRefund,
  [ERC8183_REGISTER_JOB]: handleRegisterJob,
  [ERC8183_SETTLE]: handleSettle,
  [ERC8183_MARK_EXPIRED]: handleMarkExpired,
  [ERC8183_DISPUTE]: handleDispute,
  [ERC8183_VOTE_REJECT]: handleVoteReject,
};
