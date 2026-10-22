/**
 * TwakX402Payer against the real twak CLI, fully offline. Runs only when
 * TWAK_CLI_BIN points at an installed `twak`: fixtures/twak/intercept-fetch.cjs
 * is preloaded into the CLI, serves the x402 endpoint in-process and blocks
 * every other host, and the "server" changes its terms between the SDK's
 * quote and the CLI's paid request. CI runs it against the verified release
 * (typescript-ci.yml) and weekly against @latest (twak-cli-latest.yml, with
 * TWAK_CLI_ALLOW_UNVERIFIED=1) to catch a changed pre-signing prompt.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { TwakPaymentStateUnknownError } from "../src/wallets/twak/confirm.js";
import { TWAKProvider } from "../src/wallets/twak/provider.js";
import { SessionBudgetTracker } from "../src/x402/budget.js";
import {
  X402PolicyError,
  X402RecipientMismatchError,
} from "../src/x402/errors.js";

const TWAK = process.env.TWAK_CLI_BIN;
const INTERCEPT = fileURLToPath(
  new URL("./fixtures/twak/intercept-fetch.cjs", import.meta.url),
);
const U = "0xce24439f2d9c6a2289f741120fe202248b666666";
const PAY_TO = "0x4ba07885c7a05734be6ea6f46f749564e1476751";
const PASSWORD = "Offline-Harness-Passw0rd!";
const CLI_TIMEOUT_MS = 60_000;

interface Paid {
  accepted: { asset: string };
  payload: { authorization: { to: string; value: string } };
}

describe.skipIf(!TWAK)("TwakX402Payer against the real twak CLI", () => {
  let home: string;
  let log: string;

  beforeAll(() => {
    home = mkdtempSync(join(tmpdir(), "twak-real-cli-"));
    log = join(home, "x402.log");
    vi.stubEnv("NODE_OPTIONS", `--require ${INTERCEPT}`);
    vi.stubEnv("TWAK_ACCESS_ID", "offline");
    vi.stubEnv("TWAK_HMAC_SECRET", "offline");
    vi.stubEnv("TWAK_WALLET_PASSWORD", PASSWORD);
    vi.stubEnv("X402_MOCK_LOG", log);
    execFileSync(
      TWAK as string,
      ["wallet", "create", "--password", PASSWORD, "--no-keychain", "--json"],
      { env: { ...process.env, HOME: home }, timeout: CLI_TIMEOUT_MS },
    );
  }, 2 * CLI_TIMEOUT_MS);

  afterAll(() => {
    vi.unstubAllEnvs();
    if (home) {
      rmSync(home, { recursive: true, force: true });
    }
  });

  async function pay(mode: string) {
    vi.stubEnv("X402_MOCK_MODE", mode);
    writeFileSync(log, "");
    const budget = new SessionBudgetTracker({ [U]: 1_000_000n });
    const payer = new TWAKProvider({
      twakBin: TWAK as string,
      home,
      timeoutMs: CLI_TIMEOUT_MS,
      allowUnverifiedCliVersion: process.env.TWAK_CLI_ALLOW_UNVERIFIED === "1",
    }).makeX402Payer({
      expectedPayTo: PAY_TO,
      expectedAsset: U,
      sessionBudget: budget,
    });
    const outcome = await payer
      .request(`https://x402.test/${mode}`, { maxPayment: 1_000_000n })
      .then(
        (result) => ({ result, error: undefined }),
        (error: Error) => ({ result: undefined, error }),
      );
    const paid = readFileSync(log, "utf8")
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as { paid?: Paid })
      .flatMap((entry) => (entry.paid ? [entry.paid] : []));
    return { ...outcome, paid, spent: budget.spent(U) };
  }

  it(
    "pays exactly the validated terms",
    async () => {
      const { result, error, paid, spent } = await pay("honest");
      expect(error).toBeUndefined();
      expect(result?.response).toEqual({ data: "paid" });
      expect(paid).toHaveLength(1);
      expect(paid[0].payload.authorization.to.toLowerCase()).toBe(PAY_TO);
      expect(paid[0].payload.authorization.value).toBe("1000");
      expect(paid[0].accepted.asset.toLowerCase()).toBe(U);
      expect(spent).toBe(1000n);
    },
    3 * CLI_TIMEOUT_MS,
  );

  it.each([
    ["payto", X402RecipientMismatchError, /payTo/],
    // a different token whose name contains the expected address
    ["asset_fuzzy", X402PolicyError, /asset/],
    // forged prompt lines in the token name
    ["inject", X402PolicyError, /not recognised/],
    // refused by twak's own --max-payment (the quoted amount)
    ["amount_higher", Error, /exceeds --max-payment/],
    // only the transfer method changes: refused by the --prefer-method pin
    ["method_switch", Error, /preferMethod=eip3009/],
  ])(
    "declines %s before anything is signed",
    async (mode, type, message) => {
      const { error, paid, spent } = await pay(mode);
      expect(error).toBeInstanceOf(type);
      expect(error?.message).toMatch(message);
      expect(paid).toEqual([]);
      expect(spent).toBe(0n);
    },
    3 * CLI_TIMEOUT_MS,
  );

  it(
    "pays a lower final amount and budgets only what was signed",
    async () => {
      const { result, error, paid, spent } = await pay("amount_lower");
      expect(error).toBeUndefined();
      expect(result?.amount).toBe(999n);
      expect(paid).toHaveLength(1);
      expect(paid[0].payload.authorization.value).toBe("999");
      expect(spent).toBe(999n);
    },
    3 * CLI_TIMEOUT_MS,
  );

  // an unverified twak keeps the budget for a response without a prompt
  it.skipIf(process.env.TWAK_CLI_ALLOW_UNVERIFIED === "1")(
    "does not treat a response without a challenge as a payment",
    async () => {
      const { error, paid, spent } = await pay("unpaid");
      expect(error?.message).toMatch(/without asking to sign/);
      expect(paid).toEqual([]);
      expect(spent).toBe(0n);
    },
    3 * CLI_TIMEOUT_MS,
  );

  it(
    "returns a paid body that reports an error and keeps the budget spent",
    async () => {
      const { result, paid, spent } = await pay("paid_error_body");
      expect(result?.response).toEqual({
        error: "credit top-up failed",
        success: false,
      });
      expect(paid).toHaveLength(1);
      expect(spent).toBe(1000n);
    },
    3 * CLI_TIMEOUT_MS,
  );

  it(
    "keeps the budget when the endpoint fails after taking the payment",
    async () => {
      const { error, paid, spent } = await pay("paid_502");
      expect(error).toBeInstanceOf(TwakPaymentStateUnknownError);
      expect(paid).toHaveLength(1);
      expect(spent).toBe(1000n);
    },
    3 * CLI_TIMEOUT_MS,
  );
});
