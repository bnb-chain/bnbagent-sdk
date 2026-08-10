/**
 * `TurnkeyWalletProvider` conformance (`src/wallets/turnkey/provider.ts`):
 * capability surface, synchronous address, lazy account init (concurrency +
 * retry), the EIP712Domain injection trap regression, policy-before-billing
 * ordering, legacy/1559 transaction round-trips, `fromEnv`, vendor error
 * mapping, and the missing-peer install guidance.
 *
 * `@turnkey/sdk-server` / `@turnkey/viem` are mocked at the module level
 * (the provider only ever reaches them through the lazy loader), with
 * `createAccount` returning a real viem `privateKeyToAccount` wrapped to
 * RECORD the exact call arguments — signatures stay real and recoverable,
 * so domain binding is provable with `recoverTypedDataAddress`.
 */

import {
  type TypedDataDomain,
  getAddress,
  getTypesForEIP712Domain,
  hashMessage,
  hashTypedData,
  keccak256,
  parseTransaction,
  recoverTypedDataAddress,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PolicyViolation } from "../src/signing/errors.js";
import { SigningPolicy } from "../src/signing/policy.js";
import {
  CALLS_ARBITRARY,
  PAYMASTER_SPONSOR,
  SIGN_MESSAGE,
  SIGN_TRANSACTION,
  SIGN_TYPED_DATA,
} from "../src/wallets/capabilities.js";
import { WalletIdentityMismatch } from "../src/wallets/errors.js";

const tkMocks = vi.hoisted(() => ({
  // Every `new Turnkey(config)` records its config here.
  ctorConfigs: [] as Record<string, unknown>[],
  apiClientCalls: { count: 0 },
  // Every `createAccount(input)` records its input here.
  createAccountCalls: [] as Record<string, unknown>[],
  // Test-swappable behavior; `null` = default faithful account.
  createAccountImpl: {
    fn: null as null | ((input: unknown) => Promise<unknown>),
  },
  // Recorded arguments of account.sign* calls (the enclave payload).
  recorded: {
    messages: [] as unknown[],
    typedData: [] as Record<string, unknown>[],
    transactions: [] as unknown[],
  },
  // Test-injected failure thrown by the account's sign methods.
  signFailure: { error: null as unknown },
  // Counts module-factory executions (module load observability).
  factoryRuns: { count: 0 },
}));

// The Turnkey-hosted key the mock "enclave" signs with. `SIGN_WITH` is its
// address, so the provider's identity check passes by default.
const TEST_PK: `0x${string}` = `0x${"c3".repeat(32)}`;
const SIGN_WITH = privateKeyToAccount(TEST_PK).address;

vi.mock("@turnkey/sdk-server", () => {
  tkMocks.factoryRuns.count += 1;
  class Turnkey {
    readonly config: Record<string, unknown>;
    constructor(config: Record<string, unknown>) {
      this.config = config;
      tkMocks.ctorConfigs.push(config);
    }
    apiClient(): object {
      tkMocks.apiClientCalls.count += 1;
      return { __tag: "turnkey-api-client", config: this.config };
    }
  }
  return { Turnkey };
});

vi.mock("@turnkey/viem", () => {
  tkMocks.factoryRuns.count += 1;
  return {
    createAccount: async (input: Record<string, unknown>) => {
      tkMocks.createAccountCalls.push(input);
      if (tkMocks.createAccountImpl.fn) {
        return tkMocks.createAccountImpl.fn(input);
      }
      const backing = privateKeyToAccount(TEST_PK);
      const failing = () => {
        if (tkMocks.signFailure.error !== null) {
          throw tkMocks.signFailure.error;
        }
      };
      return {
        ...backing,
        signMessage: (args: unknown) => {
          failing();
          tkMocks.recorded.messages.push(args);
          return backing.signMessage(
            args as Parameters<typeof backing.signMessage>[0],
          );
        },
        signTypedData: (args: Record<string, unknown>) => {
          failing();
          tkMocks.recorded.typedData.push(args);
          return backing.signTypedData(
            args as Parameters<typeof backing.signTypedData>[0],
          );
        },
        signTransaction: (args: unknown) => {
          failing();
          tkMocks.recorded.transactions.push(args);
          return backing.signTransaction(
            args as Parameters<typeof backing.signTransaction>[0],
          );
        },
      };
    },
  };
});

const { TURNKEY_API_BASE_URL_DEFAULT, TurnkeyWalletProvider } = await import(
  "../src/wallets/turnkey/provider.js"
);
const {
  TURNKEY_SDK_SERVER_PACKAGE,
  TURNKEY_VIEM_PACKAGE,
  loadTurnkeySdk,
  setTurnkeySdkImporter,
} = await import("../src/wallets/turnkey/sdkLoader.js");

const BASE_OPTS = {
  organizationId: "org-123",
  signWith: SIGN_WITH,
  apiPublicKey: "02".repeat(33),
  apiPrivateKey: "aa".repeat(32),
};

// A domain NOT in knownPaymentTokens — strictDefault must refuse it.
const TEST_DOMAIN: TypedDataDomain = {
  name: "TestToken",
  version: "1",
  chainId: 97,
  verifyingContract: getAddress(`0x${"22".repeat(20)}`),
};

// Canonical EIP-3009 shape (mirrors EIP3009_CANONICAL_FIELDS) with a
// validity window inside strictDefault's 600s cap.
function eip3009Fixture() {
  const nowSec = Math.floor(Date.now() / 1000);
  return {
    types: {
      TransferWithAuthorization: [
        { name: "from", type: "address" },
        { name: "to", type: "address" },
        { name: "value", type: "uint256" },
        { name: "validAfter", type: "uint256" },
        { name: "validBefore", type: "uint256" },
        { name: "nonce", type: "bytes32" },
      ],
    },
    message: {
      from: SIGN_WITH,
      to: getAddress(`0x${"33".repeat(20)}`),
      value: 1n,
      validAfter: BigInt(nowSec - 10),
      validBefore: BigInt(nowSec + 580),
      nonce: `0x${"44".repeat(32)}`,
    },
  };
}

/** Policy that allowlists TEST_DOMAIN on top of strict defaults. */
function extendedPolicy(): SigningPolicy {
  return SigningPolicy.strictDefault().extend({
    domainAllowlist: [
      [97, TEST_DOMAIN.verifyingContract as `0x${string}`],
    ] as const,
  });
}

const TURNKEY_ENV_KEYS = [
  "TURNKEY_API_PUBLIC_KEY",
  "TURNKEY_API_PRIVATE_KEY",
  "TURNKEY_ORG_ID",
  "TURNKEY_SIGN_WITH",
  "TURNKEY_API_BASE_URL",
] as const;
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of TURNKEY_ENV_KEYS) {
    savedEnv[key] = process.env[key];
    delete process.env[key];
  }
  setTurnkeySdkImporter(null);
  tkMocks.ctorConfigs.length = 0;
  tkMocks.apiClientCalls.count = 0;
  tkMocks.createAccountCalls.length = 0;
  tkMocks.createAccountImpl.fn = null;
  tkMocks.recorded.messages.length = 0;
  tkMocks.recorded.typedData.length = 0;
  tkMocks.recorded.transactions.length = 0;
  tkMocks.signFailure.error = null;
});

afterEach(() => {
  for (const key of TURNKEY_ENV_KEYS) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
  setTurnkeySdkImporter(null);
});

describe("construction and capability surface", () => {
  it("constructs offline: no module load, no client, no account", () => {
    // Declaration-order sensitive: this is the file's first executed test,
    // so the vi.mock factories must not have run yet — construction and
    // describe() must not touch the optional packages at all.
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const description = provider.describe();
    expect(description.kind).toBe("turnkey");
    expect(tkMocks.factoryRuns.count).toBe(0);
    expect(tkMocks.createAccountCalls.length).toBe(0);
    expect(tkMocks.apiClientCalls.count).toBe(0);
  });

  it("declares the pure-signer capability set", () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    expect(provider.capabilities()).toEqual(
      new Set([
        SIGN_MESSAGE,
        SIGN_TRANSACTION,
        SIGN_TYPED_DATA,
        CALLS_ARBITRARY,
        PAYMASTER_SPONSOR,
      ]),
    );
    expect(TurnkeyWalletProvider.kind).toBe("turnkey");
  });

  it("address is synchronous and checksummed from lowercase input", () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signWith: SIGN_WITH.toLowerCase(),
    });
    expect(provider.address).toBe(SIGN_WITH);
  });

  it("describe() reports the remote key location and never leaks credentials", () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const description = provider.describe();
    expect(description.address).toBe(SIGN_WITH);
    expect(description.keyLocation).toContain("remote:turnkey");
    expect(description.keyLocation).toContain(TURNKEY_API_BASE_URL_DEFAULT);
    expect(description.exists).toBe(true);
    expect(JSON.stringify(description)).not.toContain(BASE_OPTS.apiPrivateKey);
  });

  it.each([
    ["a Turnkey wallet id (UUID)", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"],
    ["a private-key id", "pk-12345"],
    ["a 39-hex-char near-address", `0x${"ab".repeat(19)}a`],
    ["an empty string", ""],
  ])("rejects signWith that is %s", (_label, signWith) => {
    expect(() => new TurnkeyWalletProvider({ ...BASE_OPTS, signWith })).toThrow(
      signWith === ""
        ? /'signWith' is required/
        : /must be the wallet account's Ethereum address/,
    );
  });

  it.each(["organizationId", "apiPublicKey", "apiPrivateKey"] as const)(
    "requires %s to be non-empty",
    (key) => {
      expect(
        () => new TurnkeyWalletProvider({ ...BASE_OPTS, [key]: "" }),
      ).toThrow(new RegExp(`'${key}' is required`));
    },
  );
});

describe("lazy account initialization", () => {
  it("concurrent first calls share exactly one createAccount round-trip", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await Promise.all([
      provider.signMessage("one"),
      provider.signMessage("two"),
      provider.signMessage("three"),
    ]);
    expect(tkMocks.createAccountCalls.length).toBe(1);
    expect(tkMocks.apiClientCalls.count).toBe(1);
    expect(tkMocks.ctorConfigs).toEqual([
      {
        apiBaseUrl: TURNKEY_API_BASE_URL_DEFAULT,
        apiPublicKey: BASE_OPTS.apiPublicKey,
        apiPrivateKey: BASE_OPTS.apiPrivateKey,
        defaultOrganizationId: BASE_OPTS.organizationId,
      },
    ]);
  });

  it("a failed init is not cached — the next call retries and succeeds", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    tkMocks.createAccountImpl.fn = () =>
      Promise.reject(new Error("transient network failure"));
    await expect(provider.signMessage("x")).rejects.toThrow(
      /transient network failure/,
    );
    tkMocks.createAccountImpl.fn = null;
    const result = await provider.signMessage("x");
    expect(result.messageHash).toBe(hashMessage("x"));
    expect(tkMocks.createAccountCalls.length).toBe(2);
  });

  it("fails closed with WalletIdentityMismatch when the backend resolves another address", async () => {
    const other = privateKeyToAccount(`0x${"d4".repeat(32)}`);
    tkMocks.createAccountImpl.fn = async () => other;
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await expect(provider.signMessage("x")).rejects.toThrow(
      WalletIdentityMismatch,
    );
  });

  it("honors a custom apiBaseUrl", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      apiBaseUrl: "https://api.turnkey.example",
    });
    expect(provider.keyLocation).toContain("https://api.turnkey.example");
    await provider.signMessage("x");
    expect(tkMocks.ctorConfigs[0]?.apiBaseUrl).toBe(
      "https://api.turnkey.example",
    );
  });
});

describe("signMessage (EIP-191)", () => {
  it("round-trips: digest matches hashMessage and the signature recovers", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const result = await provider.signMessage("hello turnkey");
    expect(result.messageHash).toBe(hashMessage("hello turnkey"));
    expect(result.signature).toMatch(/^0x[0-9a-f]{130}$/);
    expect([27n, 28n]).toContain(result.v);
    const reference = await privateKeyToAccount(TEST_PK).signMessage({
      message: "hello turnkey",
    });
    expect(result.signature).toBe(reference);
  });
});

describe("signTypedData (EIP-712)", () => {
  it("policy check runs BEFORE any billable call (strict default refuses unknown domain)", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const { types, message } = eip3009Fixture();
    await expect(
      provider.signTypedData(TEST_DOMAIN, types, message),
    ).rejects.toThrow(PolicyViolation);
    expect(tkMocks.createAccountCalls.length).toBe(0);
    expect(tkMocks.recorded.typedData.length).toBe(0);
  });

  it("signs after the policy is extended with the domain", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signingPolicy: extendedPolicy(),
    });
    const { types, message } = eip3009Fixture();
    const result = await provider.signTypedData(TEST_DOMAIN, types, message);
    expect(result.signature).toMatch(/^0x[0-9a-f]{130}$/);
  });

  it("injects the full EIP712Domain type into the enclave payload (the 0.14.x stripping trap)", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signingPolicy: extendedPolicy(),
    });
    const { types, message } = eip3009Fixture();
    await provider.signTypedData(TEST_DOMAIN, types, message);

    const sent = tkMocks.recorded.typedData[0] as {
      domain: TypedDataDomain;
      types: Record<string, { name: string; type: string }[]>;
      primaryType: string;
    };
    expect(sent.primaryType).toBe("TransferWithAuthorization");
    expect(sent.types.EIP712Domain).toEqual(
      getTypesForEIP712Domain({ domain: TEST_DOMAIN }),
    );
    expect(sent.domain).toEqual(TEST_DOMAIN);
  });

  it("replaces a caller-supplied EIP712Domain and signs identically with or without it", async () => {
    const { types, message } = eip3009Fixture();
    const withBogusDomainType = {
      // Deliberately wrong shape — must be replaced, not trusted.
      EIP712Domain: [{ name: "name", type: "string" }],
      ...types,
    };

    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signingPolicy: extendedPolicy(),
    });
    const withSupplied = await provider.signTypedData(
      TEST_DOMAIN,
      withBogusDomainType,
      message,
    );
    const withoutSupplied = await provider.signTypedData(
      TEST_DOMAIN,
      types,
      message,
    );
    expect(withSupplied.signature).toBe(withoutSupplied.signature);

    for (const sent of tkMocks.recorded.typedData as {
      types: Record<string, { name: string; type: string }[]>;
    }[]) {
      expect(sent.types.EIP712Domain).toEqual(
        getTypesForEIP712Domain({ domain: TEST_DOMAIN }),
      );
    }
  });

  it("binds the real domain: recoverTypedDataAddress yields the provider address", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signingPolicy: extendedPolicy(),
    });
    const { types, message } = eip3009Fixture();
    const result = await provider.signTypedData(TEST_DOMAIN, types, message);
    const recovered = await recoverTypedDataAddress({
      domain: TEST_DOMAIN,
      types,
      primaryType: "TransferWithAuthorization",
      message,
      signature: result.signature,
    });
    expect(recovered).toBe(provider.address);
    expect(result.messageHash).toBe(
      hashTypedData({
        domain: TEST_DOMAIN,
        types,
        primaryType: "TransferWithAuthorization",
        message,
      } as Parameters<typeof hashTypedData>[0]),
    );
  });

  it("rejects multi-struct types (primary-type ambiguity) before any billable call", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      signingPolicy: extendedPolicy(),
    });
    const { types, message } = eip3009Fixture();
    const multi = {
      ...types,
      Extra: [{ name: "x", type: "uint256" }],
    };
    await expect(
      provider.signTypedData(TEST_DOMAIN, multi, message),
    ).rejects.toThrow(PolicyViolation);
    expect(tkMocks.createAccountCalls.length).toBe(0);
  });
});

describe("signTransaction", () => {
  const legacyTx = {
    chainId: 97,
    to: getAddress(`0x${"55".repeat(20)}`),
    value: 1n,
    nonce: 0,
    gas: 21_000n,
    gasPrice: 10_000_000_000n,
  };

  it("legacy round-trip: serialized as legacy, hash and r/s/v populated", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const signed = await provider.signTransaction(legacyTx);
    const parsed = parseTransaction(signed.rawTransaction);
    expect(parsed.type).toBe("legacy");
    expect(parsed.gasPrice).toBe(legacyTx.gasPrice);
    expect(signed.hash).toBe(keccak256(signed.rawTransaction));
    // EIP-155 v for chainId 97: 2*97 + 35/36.
    expect([229n, 230n]).toContain(signed.v);
    expect(signed.r).toMatch(/^0x/);
    expect(signed.s).toMatch(/^0x/);
  });

  it("EIP-1559 round-trip: fee fields select the typed transaction", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const signed = await provider.signTransaction({
      chainId: 97,
      to: getAddress(`0x${"55".repeat(20)}`),
      value: 1n,
      nonce: 0,
      gas: 21_000n,
      maxFeePerGas: 10_000_000_000n,
      maxPriorityFeePerGas: 1_000_000_000n,
    } as unknown as typeof legacyTx);
    const parsed = parseTransaction(signed.rawTransaction);
    expect(parsed.type).toBe("eip1559");
    expect(signed.hash).toBe(keccak256(signed.rawTransaction));
    // Typed transactions carry yParity; the provider normalizes v to 27/28.
    expect([27n, 28n]).toContain(signed.v);
  });

  it("refuses a chainId mismatch before any billable call when pinned", async () => {
    const provider = new TurnkeyWalletProvider({
      ...BASE_OPTS,
      expectedChainId: 97,
    });
    await expect(
      provider.signTransaction({ ...legacyTx, chainId: 56 }),
    ).rejects.toThrow(/pinned to chainId=97/);
    expect(tkMocks.createAccountCalls.length).toBe(0);
    expect(provider.expectedChainId).toBe(97);
  });

  it("unpinned providers sign for any chainId", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const signed = await provider.signTransaction({
      ...legacyTx,
      chainId: 56,
    });
    expect(parseTransaction(signed.rawTransaction).chainId).toBe(56);
  });
});

describe("fromEnv", () => {
  const setAllEnv = () => {
    process.env.TURNKEY_API_PUBLIC_KEY = BASE_OPTS.apiPublicKey;
    process.env.TURNKEY_API_PRIVATE_KEY = BASE_OPTS.apiPrivateKey;
    process.env.TURNKEY_ORG_ID = BASE_OPTS.organizationId;
    process.env.TURNKEY_SIGN_WITH = SIGN_WITH;
  };

  it("builds a provider from the four required env vars (default base URL)", () => {
    setAllEnv();
    const provider = TurnkeyWalletProvider.fromEnv({ expectedChainId: 97 });
    expect(provider.address).toBe(SIGN_WITH);
    expect(provider.expectedChainId).toBe(97);
    expect(provider.keyLocation).toContain(TURNKEY_API_BASE_URL_DEFAULT);
  });

  it("honors TURNKEY_API_BASE_URL and a supplied signingPolicy", () => {
    setAllEnv();
    process.env.TURNKEY_API_BASE_URL = "https://api.turnkey.example";
    const policy = extendedPolicy();
    const provider = TurnkeyWalletProvider.fromEnv({ signingPolicy: policy });
    expect(provider.keyLocation).toContain("https://api.turnkey.example");
    expect(provider.signingPolicy).toBe(policy);
  });

  it("names ALL missing env vars in one error", () => {
    process.env.TURNKEY_API_PUBLIC_KEY = BASE_OPTS.apiPublicKey;
    expect(() => TurnkeyWalletProvider.fromEnv()).toThrow(
      /missing required env vars: TURNKEY_API_PRIVATE_KEY, TURNKEY_ORG_ID, TURNKEY_SIGN_WITH/,
    );
  });
});

describe("vendor error mapping", () => {
  it("rewrites rate-limit failures with the 1 RPS hint", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await provider.signMessage("warm up");
    tkMocks.signFailure.error = Object.assign(
      new Error("Turnkey error 8: RATE_LIMIT_EXCEEDED"),
      { status: 429 },
    );
    await expect(provider.signMessage("x")).rejects.toThrow(
      /1 request\/second/,
    );
  });

  it("rewrites quota exhaustion with the billing hint and preserves the cause", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await provider.signMessage("warm up");
    const original = new Error("SIGNING_QUOTA_EXCEEDED for organization");
    tkMocks.signFailure.error = original;
    const failure = await provider.signMessage("x").catch((e: unknown) => e);
    expect((failure as Error).message).toMatch(/25 billed signatures\/month/);
    expect((failure as Error).cause).toBe(original);
  });

  it("rewrites policy denials with the non-root ALLOW-policy hint", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await provider.signMessage("warm up");
    tkMocks.signFailure.error = new Error(
      "Turnkey error 7: policy engine rejected the activity (POLICY_REJECTED)",
    );
    await expect(provider.signMessage("x")).rejects.toThrow(
      /explicit ALLOW policy/,
    );
  });

  it("passes unrecognized errors through untouched", async () => {
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    await provider.signMessage("warm up");
    const original = new Error("something else entirely");
    tkMocks.signFailure.error = original;
    const failure = await provider.signMessage("x").catch((e: unknown) => e);
    expect(failure).toBe(original);
  });
});

describe("sdk loader", () => {
  it("maps a module-not-found rejection to the pnpm add guidance", async () => {
    const notFound = Object.assign(
      new Error(`Cannot find package '${TURNKEY_SDK_SERVER_PACKAGE}'`),
      { code: "ERR_MODULE_NOT_FOUND" },
    );
    setTurnkeySdkImporter(() => Promise.reject(notFound));
    const failure = await loadTurnkeySdk().catch((e: unknown) => e);
    expect((failure as Error).message).toContain(
      `pnpm add ${TURNKEY_SDK_SERVER_PACKAGE} ${TURNKEY_VIEM_PACKAGE}`,
    );
    expect((failure as Error).cause).toBe(notFound);
  });

  it("passes unrelated importer failures through", async () => {
    const boom = new Error("permission denied");
    setTurnkeySdkImporter(() => Promise.reject(boom));
    await expect(loadTurnkeySdk()).rejects.toBe(boom);
  });

  it("does not cache failures — each call retries the importer", async () => {
    let calls = 0;
    setTurnkeySdkImporter((pkg) => {
      calls += 1;
      return Promise.reject(
        Object.assign(new Error(`Cannot find package '${pkg}'`), {
          code: "ERR_MODULE_NOT_FOUND",
        }),
      );
    });
    await expect(loadTurnkeySdk()).rejects.toThrow(/pnpm add/);
    await expect(loadTurnkeySdk()).rejects.toThrow(/pnpm add/);
    // Two packages per attempt × two attempts.
    expect(calls).toBe(4);
  });

  it("setTurnkeySdkImporter(null) restores the default module source", async () => {
    setTurnkeySdkImporter(() => Promise.reject(new Error("armed")));
    await expect(loadTurnkeySdk()).rejects.toThrow(/armed/);
    setTurnkeySdkImporter(null);
    const provider = new TurnkeyWalletProvider(BASE_OPTS);
    const result = await provider.signMessage("recovered");
    expect(result.messageHash).toBe(hashMessage("recovered"));
  });
});
