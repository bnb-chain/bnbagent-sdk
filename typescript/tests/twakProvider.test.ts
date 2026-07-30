/**
 * TWAKProvider (`src/wallets/twak/`): capability surface, exact CLI argv
 * per intent (incl. `--paymaster-url` forwarding, twak v0.20.0 REQ-2),
 * envelope-parse hardening, the sign-message recovery self-check, the
 * delegated x402 payer prechecks, and the ERC8183Config `walletKind:
 * "twak"` wiring. Port of `python/tests/test_twak_provider.py`; the twak
 * CLI is faked at the `_setTwakExecForTests` subprocess seam.
 */

import { hashMessage } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NETWORKS } from "../src/config.js";
import { Paymaster } from "../src/core/paymaster.js";
import { ERC8183Config } from "../src/erc8183/config.js";
import {
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../src/errors.js";
import {
  BROADCAST_SELF,
  CALLS_ARBITRARY,
  INTENTS_ERC8004,
  INTENTS_ERC8183,
  SIGN_MESSAGE,
  SIGN_TRANSACTION,
  SIGN_TYPED_DATA,
  X402_PAY,
} from "../src/wallets/capabilities.js";
import {
  UnsupportedWalletOperation,
  WalletIdentityMismatch,
} from "../src/wallets/errors.js";
import type { ExecutionContext } from "../src/wallets/intents.js";
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
} from "../src/wallets/intents.js";
import {
  TWAKProvider,
  _setTwakExecForTests,
} from "../src/wallets/twak/provider.js";
import {
  X402AmountExceededError,
  X402BudgetExhaustedError,
  X402PolicyError,
  X402RecipientMismatchError,
} from "../src/x402/errors.js";

const PK: `0x${string}` = `0x${"a1".repeat(32)}`;
const ACCOUNT = privateKeyToAccount(PK);
const WALLET = ACCOUNT.address;
const PROVIDER_ADDR = `0x${"11".repeat(20)}`;
const EVALUATOR_ADDR = `0x${"22".repeat(20)}`;
const POLICY_ADDR = `0x${"44".repeat(20)}`;
const ZERO_ADDRESS = `0x${"00".repeat(20)}`;
const PM_URL = "https://bsc-megafuel-testnet.nodereal.io";

const TX_OUT = { success: true, hash: "0xfeed", chain: "bsc" };
const STATUS_OK = { agentWallet: "configured" };
const ADDRESS_OUT = { success: true, address: WALLET, chain: "bsc" };

interface FakeResult {
  code?: number;
  stdout?: string;
  stderr?: string;
}

/**
 * Install a fake subprocess routing twak argv → envelope; returns the
 * recorded call list (each entry is the full args array, incl. --json).
 */
function installRouter(
  route: (args: string[]) => Record<string, unknown> | FakeResult,
): string[][] {
  const calls: string[][] = [];
  const respond = (
    args: string[],
  ): { code: number; stdout: string; stderr: string } => {
    calls.push(args);
    const out = route(args);
    if ("code" in out || "stdout" in out || "stderr" in out) {
      const raw = out as FakeResult;
      return {
        code: raw.code ?? 0,
        stdout: raw.stdout ?? "",
        stderr: raw.stderr ?? "",
      };
    }
    return { code: 0, stdout: JSON.stringify(out), stderr: "" };
  };
  _setTwakExecForTests(
    async (_bin, args) => respond(args),
    (_bin, args) => respond(args),
  );
  return calls;
}

/** Standard router: status + address probes succeed, writes return `out`. */
function standardRouter(out: Record<string, unknown> = TX_OUT): string[][] {
  return installRouter((args) => {
    if (args[0] === "wallet" && args[1] === "status") {
      return STATUS_OK;
    }
    if (args[0] === "wallet" && args[1] === "address") {
      return ADDRESS_OUT;
    }
    return out;
  });
}

afterEach(() => {
  _setTwakExecForTests(null);
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

// ── construction & capability surface ──

describe("TWAKProvider — construction and capabilities", () => {
  it("is side-effect-free at construction (no CLI call)", () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    expect(twak.kind).toBe("twak");
    expect(TWAKProvider.kind).toBe("twak");
    expect(calls).toHaveLength(0);
  });

  it("rejects non-BSC chains and the spec's bsc-testnet spelling", () => {
    expect(() => new TWAKProvider({ chain: "ethereum" })).toThrow(
      /BNB Smart Chain only/,
    );
    expect(() => new TWAKProvider({ chain: "bsc-testnet" })).toThrow(
      /bsctestnet/,
    );
    expect(new TWAKProvider({ chain: "bsctestnet" }).chain).toBe("bsctestnet");
  });

  it("declares {sign.message, broadcast.self, intents.*, x402.pay} — no raw signing, no arbitrary calls", () => {
    const twak = new TWAKProvider();
    expect(twak.capabilities()).toEqual(
      new Set([
        SIGN_MESSAGE,
        BROADCAST_SELF,
        INTENTS_ERC8004,
        INTENTS_ERC8183,
        X402_PAY,
      ]),
    );
    for (const absent of [SIGN_TRANSACTION, SIGN_TYPED_DATA, CALLS_ARBITRARY]) {
      expect(twak.supports(absent)).toBe(false);
    }
  });

  it("sets fundBundlesApproval to the literal true (the ERC8183Client gate is ===)", () => {
    expect(new TWAKProvider().fundBundlesApproval).toBe(true);
  });
});

// ── address, identity pin, existence ──

describe("TWAKProvider — address and identity", () => {
  it("resolves and caches the address via the sync CLI path", () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    expect(twak.address).toBe(WALLET);
    const afterFirst = calls.length;
    expect(twak.address).toBe(WALLET);
    expect(calls.length).toBe(afterFirst); // cached — no further CLI calls
  });

  it("throws WalletIdentityMismatch when the pinned address drifts, without caching", () => {
    standardRouter();
    const twak = new TWAKProvider({ expectedAddress: PROVIDER_ADDR });
    expect(() => twak.address).toThrow(WalletIdentityMismatch);
    expect(() => twak.address).toThrow(WalletIdentityMismatch); // re-checks, still blocked
  });

  it("autoCreate=false refuses to mint a new identity when no wallet exists (INV-4)", async () => {
    installRouter((args) => {
      if (args[0] === "wallet" && args[1] === "status") {
        return { agentWallet: "not configured" };
      }
      return TX_OUT;
    });
    const twak = new TWAKProvider({ autoCreate: false });
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/materializeTwakHome/);
  });

  it("maps twak's password-on-argv requirement at auto-create to actionable guidance", async () => {
    installRouter((args) => {
      if (args[0] === "wallet" && args[1] === "status") {
        return { agentWallet: "not configured" };
      }
      if (args[0] === "wallet" && args[1] === "create") {
        return {
          code: 1,
          stderr:
            "error: required option '--password <password>' not specified",
        };
      }
      return TX_OUT;
    });
    const twak = new TWAKProvider();
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/refuses to do \(secrets on argv/);
  });
});

// ── intent dispatch: exact argv ──

function ctx(
  paymaster?: Paymaster,
  client: ExecutionContext["client"] = {} as ExecutionContext["client"],
): ExecutionContext {
  return {
    client,
    ...(paymaster ? { paymaster } : {}),
  };
}

describe("TWAKProvider — erc8183 intent argv", () => {
  const cases: [string, string, Record<string, unknown>, string[]][] = [
    [
      "set_provider omits empty optParams",
      ERC8183_SET_PROVIDER,
      { jobId: 137n, provider: PROVIDER_ADDR, optParams: "0x" },
      ["set-provider", "137", "--provider", PROVIDER_ADDR],
    ],
    [
      "set_provider passes optParams through raw",
      ERC8183_SET_PROVIDER,
      { jobId: 137n, provider: PROVIDER_ADDR, optParams: "0x0102" },
      [
        "set-provider",
        "137",
        "--provider",
        PROVIDER_ADDR,
        "--opt-params",
        "0x0102",
      ],
    ],
    [
      "set_budget",
      ERC8183_SET_BUDGET,
      { jobId: 137n, amount: 10n ** 18n, optParams: "0x" },
      ["set-budget", "137", "--amount", String(10n ** 18n)],
    ],
    [
      "submit passes the 32-byte deliverable as 0x hex",
      ERC8183_SUBMIT,
      { jobId: 137n, deliverable: `0x${"ab".repeat(32)}`, optParams: "0x" },
      ["submit", "137", "--deliverable", `0x${"ab".repeat(32)}`],
    ],
    [
      "complete omits the zero reason",
      ERC8183_COMPLETE,
      { jobId: 137n, reason: `0x${"00".repeat(32)}`, optParams: "0x" },
      ["complete", "137"],
    ],
    [
      "reject passes a non-zero reason",
      ERC8183_REJECT,
      { jobId: 137n, reason: `0x${"12".repeat(32)}`, optParams: "0x" },
      ["reject", "137", "--reason", `0x${"12".repeat(32)}`],
    ],
    [
      "claim_refund",
      ERC8183_CLAIM_REFUND,
      { jobId: 137n },
      ["claim-refund", "137"],
    ],
    [
      "register_job",
      ERC8183_REGISTER_JOB,
      { jobId: 137n, policy: POLICY_ADDR },
      ["register-job", "137", "--policy", POLICY_ADDR],
    ],
    [
      "settle omits empty evidence",
      ERC8183_SETTLE,
      { jobId: 137n, evidence: "0x" },
      ["settle", "137"],
    ],
    [
      "settle passes evidence",
      ERC8183_SETTLE,
      { jobId: 137n, evidence: "0xcafe" },
      ["settle", "137", "--evidence", "0xcafe"],
    ],
    [
      "mark_expired",
      ERC8183_MARK_EXPIRED,
      { jobId: 137n },
      ["mark-expired", "137"],
    ],
    ["dispute", ERC8183_DISPUTE, { jobId: 137n }, ["dispute", "137"]],
    [
      "vote_reject",
      ERC8183_VOTE_REJECT,
      { jobId: 137n },
      ["vote-reject", "137"],
    ],
  ];

  it.each(cases)("%s", async (_label, name, kwargs, expectedArgv) => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    const result = await twak.execute({ name, kwargs });
    expect(result).toEqual({
      transactionHash: "0xfeed",
      status: 1,
      receipt: null,
    });
    expect(calls[0].slice(0, 2)).toEqual(["wallet", "status"]);
    expect(calls[1]).toEqual([
      "erc8183",
      ...expectedArgv,
      "--chain",
      "bsc",
      "--json",
    ]);
    expect(calls).toHaveLength(2);
  });

  it("fund pins --expected-budget and surfaces approveHash + bsctestnet chain", async () => {
    const calls = installRouter((args) =>
      args[0] === "wallet"
        ? args[1] === "status"
          ? STATUS_OK
          : ADDRESS_OUT
        : { success: true, hash: "0xfeed", approveHash: "0xa11" },
    );
    const twak = new TWAKProvider({ chain: "bsctestnet" });
    const result = await twak.execute({
      name: ERC8183_FUND,
      kwargs: { jobId: 137n, expectedBudget: 5_000n, optParams: "0x" },
    });
    expect(result.approveHash).toBe("0xa11");
    expect(calls[1]).toEqual([
      "erc8183",
      "fund",
      "137",
      "--expected-budget",
      "5000",
      "--chain",
      "bsctestnet",
      "--json",
    ]);
  });

  it("create_job omits the zero hook and normalizes jobId to bigint", async () => {
    const calls = installRouter((args) =>
      args[0] === "wallet"
        ? STATUS_OK
        : { success: true, hash: "0xjob", jobId: "138" },
    );
    const twak = new TWAKProvider();
    const result = await twak.execute({
      name: ERC8183_CREATE_JOB,
      kwargs: {
        provider: PROVIDER_ADDR,
        evaluator: EVALUATOR_ADDR,
        expiredAt: 4102444800n,
        description: "job",
        hook: ZERO_ADDRESS,
      },
    });
    expect(result.jobId).toBe(138n);
    expect(calls[1]).toEqual([
      "erc8183",
      "create-job",
      "--provider",
      PROVIDER_ADDR,
      "--evaluator",
      EVALUATOR_ADDR,
      "--expires-at",
      "4102444800",
      "--description",
      "job",
      "--chain",
      "bsc",
      "--json",
    ]);
  });

  it("register emits repeatable --metadata flags and normalizes agentId to number", async () => {
    const calls = installRouter((args) =>
      args[0] === "wallet"
        ? STATUS_OK
        : { success: true, hash: "0xreg", agentId: "42", owner: WALLET },
    );
    const twak = new TWAKProvider();
    const result = await twak.execute({
      name: ERC8004_REGISTER,
      kwargs: {
        agentUri: "https://a.example/card.json",
        metadata: [{ key: "built_with", value: "bnbagent" }],
      },
    });
    expect(result.agentId).toBe(42);
    expect(calls[1]).toEqual([
      "erc8004",
      "register",
      "--uri",
      "https://a.example/card.json",
      "--metadata",
      "built_with=bnbagent",
      "--chain",
      "bsc",
      "--json",
    ]);
  });

  it("rejects unknown intents without any CLI call, listing the menu", async () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    await expect(
      twak.execute({ name: "erc20.transfer", kwargs: {} }),
    ).rejects.toThrow(UnsupportedWalletOperation);
    await expect(
      twak.execute({ name: "erc20.transfer", kwargs: {} }),
    ).rejects.toThrow(/fixed command menu/);
    expect(calls).toHaveLength(0);
  });

  const customErc8183ContractCases = [
    [ERC8183_CREATE_JOB, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_SET_PROVIDER, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_SET_BUDGET, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_FUND, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_SUBMIT, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_COMPLETE, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_REJECT, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_CLAIM_REFUND, NETWORKS["bsc-mainnet"].commerceContract],
    [ERC8183_REGISTER_JOB, NETWORKS["bsc-mainnet"].routerContract],
    [ERC8183_SETTLE, NETWORKS["bsc-mainnet"].routerContract],
    [ERC8183_MARK_EXPIRED, NETWORKS["bsc-mainnet"].routerContract],
    [ERC8183_DISPUTE, NETWORKS["bsc-mainnet"].policyContract],
    [ERC8183_VOTE_REJECT, NETWORKS["bsc-mainnet"].policyContract],
  ] as const;

  it.each(customErc8183ContractCases)(
    "rejects %s on a custom contract before any CLI call",
    async (name, canonicalTarget) => {
      const calls = standardRouter();
      const twak = new TWAKProvider();
      await expect(
        twak.execute({
          name,
          call: {
            address: `0x${"99".repeat(20)}`,
            abi: [],
            functionName: "unused",
            args: [],
          },
        }),
      ).rejects.toThrow(
        new RegExp(
          `custom contract.*${canonicalTarget.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
          "i",
        ),
      );
      expect(calls).toHaveLength(0);
    },
  );

  it.each([ERC8004_REGISTER, ERC8004_SET_METADATA, ERC8004_SET_AGENT_URI])(
    "rejects %s when its target does not match the effective registry",
    async (name) => {
      const calls = standardRouter();
      const customRegistry = `0x${"99".repeat(20)}` as `0x${string}`;
      await expect(
        new TWAKProvider().execute({
          name,
          call: {
            address: customRegistry,
            abi: [],
            functionName: "unused",
            args: [],
          },
        }),
      ).rejects.toThrow(/ERC8004_REGISTRY_ADDRESS/);
      expect(calls).toHaveLength(0);
    },
  );

  it("allows a custom ERC-8004 registry when the env override matches", async () => {
    const customRegistry = `0x${"99".repeat(20)}` as `0x${string}`;
    vi.stubEnv("ERC8004_REGISTRY_ADDRESS", customRegistry);
    const calls = installRouter((args) =>
      args[0] === "wallet"
        ? STATUS_OK
        : { success: true, hash: "0xreg", agentId: "42", owner: WALLET },
    );
    await new TWAKProvider().execute({
      name: ERC8004_REGISTER,
      kwargs: { agentUri: "https://a.example/card.json" },
      call: {
        address: customRegistry,
        abi: [],
        functionName: "register",
        args: [],
      },
    });
    expect(calls[1]?.slice(0, 2)).toEqual(["erc8004", "register"]);
  });

  it("rejects a canonical ERC-8004 intent when an env override would redirect it", async () => {
    vi.stubEnv("ERC8004_REGISTRY_ADDRESS", `0x${"99".repeat(20)}`);
    const calls = standardRouter();
    await expect(
      new TWAKProvider().execute({
        name: ERC8004_REGISTER,
        call: {
          address: NETWORKS["bsc-mainnet"].registryContract as `0x${string}`,
          abi: [],
          functionName: "register",
          args: [],
        },
      }),
    ).rejects.toThrow(/SDK intent targets/);
    expect(calls).toHaveLength(0);
  });

  it("allows a canonical contract target", async () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    await twak.execute({
      name: ERC8183_DISPUTE,
      kwargs: { jobId: 137n },
      call: {
        address: NETWORKS["bsc-mainnet"].policyContract as `0x${string}`,
        abi: [],
        functionName: "dispute",
        args: [137n],
      },
    });
    expect(calls).toHaveLength(2);
  });

  it("uses the testnet canonical target when configured for bsctestnet", async () => {
    const calls = standardRouter();
    const twak = new TWAKProvider({ chain: "bsctestnet" });
    await twak.execute({
      name: ERC8183_DISPUTE,
      kwargs: { jobId: 137n },
      call: {
        address: NETWORKS["bsc-testnet"].policyContract as `0x${string}`,
        abi: [],
        functionName: "dispute",
        args: [137n],
      },
    });
    expect(calls).toHaveLength(2);
  });
});

// ── paymaster forwarding (twak v0.20.0, REQ-2) ──

describe("TWAKProvider — --paymaster-url forwarding", () => {
  it("makeExecutor returns the provider itself and captures the paymaster URL for writes", async () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    const executor = twak.makeExecutor(ctx(new Paymaster(PM_URL)));
    expect(executor).toBe(twak);
    await twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 137n } });
    // The read probe never carries the flag; the write does, before --chain.
    expect(calls[0].slice(0, 2)).toEqual(["wallet", "status"]);
    expect(calls[0]).not.toContain("--paymaster-url");
    expect(calls[1]).toEqual([
      "erc8183",
      "dispute",
      "137",
      "--paymaster-url",
      PM_URL,
      "--chain",
      "bsc",
      "--json",
    ]);
  });

  it("no paymaster in the context → no flag; the latest makeExecutor context wins", async () => {
    const calls = standardRouter();
    const twak = new TWAKProvider();
    twak.makeExecutor(ctx(new Paymaster(PM_URL)));
    twak.makeExecutor(ctx());
    await twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 137n } });
    expect(calls[1]).toEqual([
      "erc8183",
      "dispute",
      "137",
      "--chain",
      "bsc",
      "--json",
    ]);
  });

  it("warns and ignores a paymaster without a URL", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const calls = standardRouter();
    const twak = new TWAKProvider();
    twak.makeExecutor({
      client: {} as ExecutionContext["client"],
      paymaster: {} as Paymaster,
    });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("--paymaster-url"),
    );
    await twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 137n } });
    expect(calls[1]).not.toContain("--paymaster-url");
  });
});

describe("TWAKProvider — sponsored receipt-timeout classification", () => {
  const hash = `0x${"ab".repeat(32)}` as const;
  const timeoutResult = {
    code: 1,
    stdout: JSON.stringify({
      error: `Timed out waiting for receipt ${hash} on bsctestnet`,
      errorCode: "NETWORK_ERROR",
    }),
  };

  it("marks a relay hash unseen by the public RPC as unverified", async () => {
    installRouter((args) => (args[0] === "wallet" ? STATUS_OK : timeoutResult));
    const getTransaction = vi.fn().mockRejectedValue(new Error("not found"));
    const twak = new TWAKProvider({ chain: "bsctestnet" });
    twak.makeExecutor(
      ctx(new Paymaster(PM_URL), {
        getTransaction,
      } as unknown as ExecutionContext["client"]),
    );

    let caught: unknown;
    try {
      await twak.execute({
        name: ERC8183_DISPUTE,
        kwargs: { jobId: 1n },
      });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(RelaySubmissionUnverifiedError);
    expect(caught).toMatchObject({ txHash: hash });
    expect(String(caught)).toContain("Do not retry blindly");
    expect(getTransaction).toHaveBeenCalledWith({ hash });
  });

  it("marks a public-chain-visible hash as pending", async () => {
    installRouter((args) => (args[0] === "wallet" ? STATUS_OK : timeoutResult));
    const getTransaction = vi.fn().mockResolvedValue({ hash });
    const twak = new TWAKProvider({ chain: "bsctestnet" });
    twak.makeExecutor(
      ctx(new Paymaster(PM_URL), {
        getTransaction,
      } as unknown as ExecutionContext["client"]),
    );

    let caught: unknown;
    try {
      await twak.execute({
        name: ERC8183_DISPUTE,
        kwargs: { jobId: 1n },
      });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(TransactionPendingError);
    expect(caught).toMatchObject({ txHash: hash });
    expect(String(caught)).toContain("Do not retry");
    expect(getTransaction).toHaveBeenCalledWith({ hash });
  });
});

// ── envelope-parse hardening ──

describe("TWAKProvider — CLI envelope quirks", () => {
  it("error field with zero exit is a failure", async () => {
    installRouter((args) =>
      args[0] === "wallet" ? STATUS_OK : { error: "boom", success: true },
    );
    const twak = new TWAKProvider();
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/twak command failed/);
  });

  it("success:false with zero exit is a failure", async () => {
    installRouter((args) =>
      args[0] === "wallet" ? STATUS_OK : { success: false },
    );
    const twak = new TWAKProvider();
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/twak command failed/);
  });

  it("non-zero exit with an explicit success envelope is trusted (x402 quote quirk)", async () => {
    installRouter(() => ({
      code: 1,
      stdout: JSON.stringify({ success: true, accepts: [] }),
    }));
    const twak = new TWAKProvider();
    const data = await twak.x402Quote("https://api.example/paid");
    expect(data).toEqual({ success: true, accepts: [] });
  });

  it("unknown option maps to the >= v0.20.0 upgrade hint", async () => {
    installRouter((args) =>
      args[0] === "wallet"
        ? STATUS_OK
        : { code: 1, stderr: "error: unknown option '--paymaster-url'" },
    );
    const twak = new TWAKProvider();
    twak.makeExecutor(ctx(new Paymaster(PM_URL)));
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/upgrade twak to >= v0\.20\.0/);
  });

  it("a write without a tx hash is an error, not a fabricated result", async () => {
    installRouter((args) =>
      args[0] === "wallet" ? STATUS_OK : { success: true, chain: "bsc" },
    );
    const twak = new TWAKProvider();
    await expect(
      twak.execute({ name: ERC8183_DISPUTE, kwargs: { jobId: 1n } }),
    ).rejects.toThrow(/no transaction hash/);
  });
});

// ── sign-message recovery self-check ──

describe("TWAKProvider — signMessage", () => {
  it("normalizes, digests and self-checks a genuine twak signature", async () => {
    const message = "negotiation: job 137 accepted";
    const signature = await ACCOUNT.signMessage({ message });
    const calls = installRouter((args) => {
      if (args[0] === "wallet" && args[1] === "status") {
        return STATUS_OK;
      }
      if (args[0] === "wallet" && args[1] === "address") {
        return ADDRESS_OUT;
      }
      return { success: true, signature: signature.slice(2) }; // no 0x from CLI
    });
    const twak = new TWAKProvider();
    const result = await twak.signMessage(message);
    expect(result.signature).toBe(signature);
    expect(result.messageHash).toBe(hashMessage(message));
    expect(result.v).toBeGreaterThanOrEqual(27n);
    // sign-message pins --chain bsc even on testnet providers (S-10)
    const signCall = calls.find((c) => c[1] === "sign-message");
    expect(signCall).toContain("bsc");
  });

  it("refuses a signature that recovers to a different key, naming the divergence", async () => {
    const stranger = privateKeyToAccount(`0x${"b2".repeat(32)}`);
    const signature = await stranger.signMessage({ message: "msg" });
    installRouter((args) => {
      if (args[0] === "wallet" && args[1] === "status") {
        return STATUS_OK;
      }
      if (args[0] === "wallet" && args[1] === "address") {
        return ADDRESS_OUT;
      }
      return { success: true, signature };
    });
    const twak = new TWAKProvider();
    await expect(twak.signMessage("msg")).rejects.toThrow(/self-check failed/);
  });
});

// ── delegated x402 payer ──

const QUOTE_OUT = {
  success: true,
  resource: { url: "https://api.example/paid" },
  accepts: [
    {
      network: "eip155:56",
      scheme: "exact",
      asset: `0x${"55".repeat(20)}`,
      amount: "5000",
      payTo: `0x${"66".repeat(20)}`,
      transferMethod: "eip3009",
      maxTimeoutSeconds: 300,
      preferred: true,
    },
  ],
};
const ASSET = `0x${"55".repeat(20)}`;
const PAY_TO = `0x${"66".repeat(20)}`;

function x402Router(): string[][] {
  return installRouter((args) => {
    if (args[0] === "wallet" && args[1] === "status") {
      return STATUS_OK;
    }
    if (args[0] === "x402" && args[1] === "quote") {
      return QUOTE_OUT;
    }
    if (args[0] === "x402" && args[1] === "request") {
      return { success: true, data: "paid", txHash: "0xabc" };
    }
    return TX_OUT;
  });
}

describe("TwakX402Payer", () => {
  it("quote is read-only: no wallet-status probe (F-3)", async () => {
    const calls = x402Router();
    const payer = new TWAKProvider().makeX402Payer();
    const quote = await payer.quote("https://api.example/paid");
    expect(quote.accepts).toHaveLength(1);
    expect(quote.accepts[0].amount).toBe(5000n);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("x402");
  });

  it("request pins the prechecked route and reports the quoted terms", async () => {
    const calls = x402Router();
    const payer = new TWAKProvider().makeX402Payer();
    const result = await payer.request("https://api.example/paid", {
      maxPayment: 10_000n,
    });
    expect(result.success).toBe(true);
    expect(result.amount).toBe(5000n);
    expect(result.asset).toBe(ASSET);
    expect(result.payTo).toBe(PAY_TO);
    expect(result.transaction).toBe("0xabc");
    const request = calls.find((c) => c[0] === "x402" && c[1] === "request");
    expect(request).toEqual([
      "x402",
      "request",
      "https://api.example/paid",
      "--max-payment",
      "10000",
      "--yes",
      "--prefer-network",
      "eip155:56",
      "--prefer-asset",
      ASSET,
      "--json",
    ]);
  });

  it("enforces the per-call cap before paying", async () => {
    x402Router();
    const payer = new TWAKProvider().makeX402Payer();
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 4_999n }),
    ).rejects.toThrow(X402AmountExceededError);
  });

  it("pins payTo and asset when committed", async () => {
    x402Router();
    const provider = new TWAKProvider();
    await expect(
      provider
        .makeX402Payer({ expectedPayTo: `0x${"77".repeat(20)}` })
        .request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402RecipientMismatchError);
    await expect(
      provider
        .makeX402Payer({ expectedAsset: `0x${"88".repeat(20)}` })
        .request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402PolicyError);
  });

  it("bounds the claimed validity window", async () => {
    x402Router();
    const payer = new TWAKProvider().makeX402Payer({ maxTimeoutSeconds: 60 });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(/maxTimeoutSeconds 300 exceeds/);
  });

  it("session budget: reserves the quoted amount and rolls back on CLI failure", async () => {
    let failNext = true;
    installRouter((args) => {
      if (args[0] === "wallet") {
        return STATUS_OK;
      }
      if (args[0] === "x402" && args[1] === "quote") {
        return QUOTE_OUT;
      }
      if (failNext) {
        failNext = false;
        return { code: 1, stderr: "network down" };
      }
      return { success: true, data: "paid" };
    });
    const payer = new TWAKProvider().makeX402Payer({
      sessionBudget: { [ASSET]: 5_000n },
    });
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(/twak command failed/);
    // Rolled back: the full budget is available for the retry…
    const result = await payer.request("https://api.example/paid", {
      maxPayment: 10_000n,
    });
    expect(result.success).toBe(true);
    // …and now exhausted.
    await expect(
      payer.request("https://api.example/paid", { maxPayment: 10_000n }),
    ).rejects.toThrow(X402BudgetExhaustedError);
  });
});

// ── config wiring ──

describe("ERC8183Config — walletKind twak", () => {
  it("dispatches to TWAKProvider with the chain pinned to the network", () => {
    const config = new ERC8183Config({
      walletKind: "twak",
      network: "bsc-testnet",
    });
    expect(config.walletProvider).toBeInstanceOf(TWAKProvider);
    expect((config.walletProvider as TWAKProvider).chain).toBe("bsctestnet");
  });

  it("rejects non-BNB networks for the twak kind", () => {
    expect(
      () => new ERC8183Config({ walletKind: "twak", network: "local" }),
    ).toThrow(/BNB Smart Chain networks only/);
  });
});
