/**
 * Confirming twak's `x402 request` pre-signing prompt instead of `--yes`.
 * Port of the prompt handling in `python/bnbagent/wallets/twak_provider.py`.
 *
 * twak v0.20.0 re-fetches the 402 challenge before paying. With `--yes`
 * it signs whatever that re-fetched challenge asks for (up to
 * `--max-payment`); without it, it prints the final amount, token
 * contract, network and recipient to stderr (dist/index.js
 * makeReadlineConfirm) and signs only after reading `y` on stdin.
 */

import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";

/**
 * The payment terms a caller has already validated for one x402 request.
 * `asset`, `network` and `payTo` must match twak's prompt exactly; `amount`
 * is the most twak may sign (a lower final amount is confirmed).
 */
export interface TwakPaymentTerms {
  amount: bigint;
  asset: string;
  network: string;
  payTo: string;
}

/**
 * twak was about to sign terms other than the validated ones. The provider
 * answered `n`, so no payment authorization was signed or sent. `field` is
 * `"amount"`, `"asset"`, `"network"`, `"payTo"` or `"prompt"`
 * (unrecognised prompt).
 */
export class TwakPaymentTermsMismatchError extends Error {
  constructor(
    readonly field: "amount" | "asset" | "network" | "payTo" | "prompt",
    readonly expected: string,
    readonly actual: string,
  ) {
    const relation = field === "amount" ? "exceeds" : "does not match";
    super(
      `twak's payment ${field} ${JSON.stringify(actual)} ${relation} the validated ${JSON.stringify(expected)}; payment declined before signing`,
    );
    this.name = "TwakPaymentTermsMismatchError";
  }
}

/**
 * A paid x402 request failed before twak could send a payment: before the
 * provider confirmed twak's prompt, or twak reported the payment cancelled
 * / Permit2 approval required. `error` is the underlying failure.
 */
export class TwakPaymentNotSentError extends Error {
  constructor(readonly error: Error) {
    super(error.message, { cause: error });
    this.name = "TwakPaymentNotSentError";
  }
}

/**
 * A paid x402 request failed after the provider confirmed the payment.
 * twak may already have sent the signed authorization, so the payment can
 * still settle. Reconcile with the merchant before retrying.
 */
export class TwakPaymentStateUnknownError extends Error {
  constructor(message: string, opts?: { cause?: unknown }) {
    super(message, opts);
    this.name = "TwakPaymentStateUnknownError";
  }
}

/** twak error codes that mean the confirmed payment was still not signed. */
export const NOT_SENT_ERROR_CODES: ReadonlySet<string> = new Set([
  "PAYMENT_CANCELLED",
  "PERMIT2_APPROVAL_REQUIRED",
]);

export const MAY_HAVE_PAID =
  "the payment was confirmed and may have been sent, so the session budget stays reserved; reconcile with the merchant before retrying";

/**
 * An `x402 request` exit-0 result: the paid endpoint's body verbatim. It
 * may be any JSON and may carry its own `error` / `success` fields; they
 * belong to the endpoint, not to twak, whose own failures exit non-zero.
 */
export function paidBody(stdout: string): unknown {
  if (!stdout.trim()) {
    return null;
  }
  try {
    return JSON.parse(stdout) as unknown;
  } catch {
    return stdout;
  }
}

/** `errorCode` of a twak error envelope, if any. */
export function errorCode(stdout: string): string | undefined {
  try {
    const payload = JSON.parse(stdout) as unknown;
    const code =
      payload !== null && typeof payload === "object"
        ? (payload as Record<string, unknown>).errorCode
        : undefined;
    return typeof code === "string" ? code : undefined;
  } catch {
    return undefined;
  }
}

export const SIGN_PROMPT_TAIL = "Confirm? [y/N] ";
// The token name is server-controlled, so it may not contain newlines and
// every marker must appear exactly once; anything else is declined.
const SIGN_PROMPT_RE =
  /\nSign payment authorization: (?<amount>\d+) \(atomic\) (?<token>[^\n]*) on (?<network>[^\s()]+) \((?<chain>[^\s()]+)\)\nToken contract: (?<asset>0x[0-9a-fA-F]{40})\nRecipient:\s+(?<payTo>0x[0-9a-fA-F]{40})\nConfirm\? \[y\/N\] $/u;
const SIGN_PROMPT_MARKERS = [
  "Sign payment authorization:",
  "Token contract:",
  "Recipient:",
];

/**
 * Asked after the payment prompt when a Permit2 route has no allowance and
 * `--auto-approve` is off. stdin is closed by then, so twak exits there
 * with no output, before broadcasting the approval or signing the permit.
 */
export const APPROVAL_PROMPT_TAIL =
  "Approve unlimited Permit2 allowance? [y/N] ";

/**
 * A token name can embed a fake prompt tail, so a pipe read that stops
 * inside the name would look like a complete prompt. twak writes the prompt
 * in one write and then blocks on stdin; answer only once stderr has been
 * quiet this long, so the whole block (with any duplicated markers) is
 * checked.
 */
export const PROMPT_QUIET_MS = 200;

/** The amount twak's pending sign prompt shows (call after {@link checkSignPrompt}). */
export function promptAmount(text: string): bigint {
  const amount = SIGN_PROMPT_RE.exec(text)?.groups?.amount;
  if (amount === undefined) {
    throw new Error("twak sign prompt not found");
  }
  return BigInt(amount);
}

/** `null` if twak's pending prompt shows `expected` (the amount may be lower). */
export function checkSignPrompt(
  text: string,
  expected: TwakPaymentTerms,
): TwakPaymentTermsMismatchError | null {
  const groups = SIGN_PROMPT_RE.exec(text)?.groups;
  if (
    groups === undefined ||
    SIGN_PROMPT_MARKERS.some((marker) => text.split(marker).length !== 2)
  ) {
    return new TwakPaymentTermsMismatchError(
      "prompt",
      "twak v0.20.0 confirmation prompt",
      text.slice(-300),
    );
  }
  if (BigInt(groups.amount) > expected.amount) {
    return new TwakPaymentTermsMismatchError(
      "amount",
      expected.amount.toString(),
      groups.amount,
    );
  }
  const shown = [
    ["asset", expected.asset.toLowerCase(), groups.asset.toLowerCase()],
    ["network", expected.network.toLowerCase(), groups.network.toLowerCase()],
    ["payTo", expected.payTo.toLowerCase(), groups.payTo.toLowerCase()],
  ] as const;
  for (const [field, want, got] of shown) {
    if (want !== got) {
      return new TwakPaymentTermsMismatchError(field, want, got);
    }
  }
  return null;
}

/** One finished CLI invocation, success or not. */
export interface TwakExecResult {
  code: number;
  stdout: string;
  stderr: string;
}

export interface TwakPromptExecOpts {
  timeoutMs: number;
  env: NodeJS.ProcessEnv | undefined;
  /** Called once with all stderr so far when twak waits at its sign prompt. */
  onPrompt: (stderr: string) => "y" | "n";
}

/**
 * Spawn `bin args`, answer twak's sign prompt once via `onPrompt`, then end
 * stdin: twak cannot be confirmed twice, and it exits once the request is
 * done. Rejects like `execFile` on ENOENT and with `killed: true` on
 * timeout.
 */
export function spawnWithPrompt(
  bin: string,
  args: string[],
  opts: TwakPromptExecOpts,
): Promise<TwakExecResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      env: opts.env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout: Buffer[] = [];
    const decoder = new StringDecoder("utf8");
    let stderr = "";
    let answered = false;
    let killed = false;
    let quiet: NodeJS.Timeout | undefined;
    const deadline = setTimeout(() => {
      killed = true;
      child.kill("SIGKILL");
    }, opts.timeoutMs);

    const answer = () => {
      if (
        answered ||
        child.exitCode !== null ||
        !stderr.endsWith(SIGN_PROMPT_TAIL)
      ) {
        return;
      }
      answered = true;
      child.stdin.end(`${opts.onPrompt(stderr)}\n`);
    };

    child.stdin.on("error", () => {
      // EPIPE when twak exits before reading the answer
    });
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += decoder.write(chunk);
      clearTimeout(quiet);
      if (!answered && stderr.endsWith(SIGN_PROMPT_TAIL)) {
        quiet = setTimeout(answer, PROMPT_QUIET_MS);
      }
    });
    child.on("error", (error) => {
      clearTimeout(deadline);
      clearTimeout(quiet);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(deadline);
      clearTimeout(quiet);
      if (killed) {
        reject(Object.assign(new Error("twak timed out"), { killed: true }));
        return;
      }
      resolve({
        code: code ?? 1,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: stderr + decoder.end(),
      });
    });
  });
}
