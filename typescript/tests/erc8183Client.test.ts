/**
 * Ports the `ERC8183Client` facade slice of `python/tests/test_erc8183_client.py`.
 *
 * `ERC8183Client.create()` is async and needs an RPC round trip (chain-id
 * assertion), so this suite mocks `../src/core/clients.js`'s
 * `createPublicClientFor` to hand back a `mockPublicClient` transport
 * instead of dialing a real RPC — the same technique `erc8004Agent.test.ts`
 * uses for viem's `createPublicClient`. `../src/core/paymaster.js`'s
 * `Paymaster` is likewise mocked so paymaster wiring can be asserted by
 * construction args instead of poking at private client internals.
 *
 * Write-path assertions reuse `erc8183Intents.test.ts`'s pattern: a
 * `StubWallet` whose `makeExecutor` always returns the same
 * `RecordingExecutor`, so every write across `commerce`/`router`/`policy`
 * lands in one flat, inspectable `intents` array — the delegation table
 * (`settle -> router`, `dispute -> policy`, ...) reduces to checking which
 * `ERC8183_*` intent name shows up.
 *
 * Read-path assertions (paymentToken caching, the createJob dispute-window
 * guard, fund's allowance/decimals reads) drive a real `eth_call` handler
 * that decodes calldata against the four ABIs in play (commerce, router,
 * policy, erc20) and returns per-test stubbed results.
 */

import {
  type ContractFunctionName,
  type Hex,
  type TransactionRequestLegacy,
  decodeFunctionData,
  encodeFunctionResult,
  getAddress,
  hexToString,
} from "viem";
import { describe, expect, it, vi } from "vitest";
import { agenticCommerceAbi } from "../src/abis/agenticCommerce.js";
import { erc20Abi } from "../src/abis/erc20.js";
import { evaluatorRouterAbi } from "../src/abis/evaluatorRouter.js";
import { optimisticPolicyAbi } from "../src/abis/optimisticPolicy.js";
import type { NetworkConfig } from "../src/config.js";
import { NETWORKS } from "../src/config.js";
import { ZERO_ADDRESS } from "../src/erc8183/types.js";
import { JobPaymentTokenMismatchError } from "../src/errors.js";
import { AssetId, getAsset } from "../src/networks/assets.js";
import type {
  ExecutionContext,
  Intent,
  IntentExecutor,
  TxResult,
} from "../src/wallets/intents.js";
import {
  ERC8183_CLAIM_REFUND,
  ERC8183_CREATE_JOB,
  ERC8183_CREATE_JOB_WITH_TOKEN,
  ERC8183_DISPUTE,
  ERC8183_FUND,
  ERC8183_REJECT,
  ERC8183_SETTLE,
  ERC8183_SUBMIT,
  ERC8183_VOTE_REJECT,
} from "../src/wallets/intents.js";
import type { SignedTx } from "../src/wallets/walletProvider.js";
import { WalletProvider } from "../src/wallets/walletProvider.js";
import {
  FAKE_TX_HASH,
  type MockHandler,
  mockPublicClient,
} from "./helpers/mockTransport.js";

const FAKE_COMMERCE = getAddress(`0x${"aa".repeat(20)}`);
const FAKE_ROUTER = getAddress(`0x${"bb".repeat(20)}`);
const FAKE_POLICY = getAddress(`0x${"cc".repeat(20)}`);
const FAKE_TOKEN = getAddress(`0x${"dd".repeat(20)}`);
const CUSTOM_TOKEN_INPUT = "0x1234567890abcdef1234567890abcdef12345678";
const CUSTOM_TOKEN = getAddress(CUSTOM_TOKEN_INPUT);
const WALLET_ADDRESS = getAddress(`0x${"99".repeat(20)}`);

const { createPublicClientForMock, PaymasterMock } = vi.hoisted(() => ({
  createPublicClientForMock: vi.fn(),
  PaymasterMock: vi.fn().mockImplementation((paymasterUrl: string) => ({
    paymasterUrl,
  })),
}));

vi.mock("../src/core/clients.js", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../src/core/clients.js")>();
  return { ...actual, createPublicClientFor: createPublicClientForMock };
});

vi.mock("../src/core/paymaster.js", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../src/core/paymaster.js")>();
  return { ...actual, Paymaster: PaymasterMock };
});

const { ERC8183Client, ERC8183_PAYMASTER_CHAIN_IDS } = await import(
  "../src/erc8183/client.js"
);

function fakeNetwork(overrides: Partial<NetworkConfig> = {}): NetworkConfig {
  return {
    name: "test-net",
    chainId: 97,
    rpcUrl: "https://fake-rpc.example.com",
    usePaymaster: true,
    paymasterUrl: "https://paymaster.example.com",
    registryContract: ZERO_ADDRESS,
    commerceContract: FAKE_COMMERCE,
    routerContract: FAKE_ROUTER,
    policyContract: FAKE_POLICY,
    ...overrides,
  };
}

function toChainIdHex(chainId: number): `0x${string}` {
  return `0x${chainId.toString(16)}`;
}

/** Decode `eth_call` calldata against whichever of the four ABIs in play
 * recognizes it, and return the stubbed result for that function name.
 * Falls back to `"0x"` (a clean non-revert) for anything not stubbed —
 * notably the preflight `eth_call` a self-pay `approve()` write triggers,
 * which this suite doesn't care about decoding. */
function combinedReadHandler(results: Record<string, unknown>): MockHandler {
  return (params) => {
    const [{ data }] = params as [{ data: Hex }];
    for (const abi of [
      agenticCommerceAbi,
      evaluatorRouterAbi,
      optimisticPolicyAbi,
      erc20Abi,
    ]) {
      let decoded: { functionName: ContractFunctionName<typeof abi> };
      try {
        decoded = decodeFunctionData({ abi, data }) as {
          functionName: ContractFunctionName<typeof abi>;
        };
      } catch {
        continue;
      }
      if (!(decoded.functionName in results)) {
        continue;
      }
      const configured = results[decoded.functionName];
      const result =
        typeof configured === "function"
          ? configured({
              args: decodeFunctionData({ abi, data }).args,
              to: getAddress((params as [{ to: `0x${string}` }])[0].to),
            })
          : configured;
      return encodeFunctionResult({
        abi,
        functionName: decoded.functionName,
        // biome-ignore lint/suspicious/noExplicitAny: result shape varies per stubbed function
        result: result as any,
      });
    }
    return "0x";
  };
}

function defaultResults(): Record<string, unknown> {
  return {
    paymentToken: FAKE_TOKEN,
    jobPaymentToken: FAKE_TOKEN,
    isPaymentTokenSupported: false,
    decimals: 18,
    symbol: "USDT",
    balanceOf: 0n,
    allowance: 0n,
    disputeWindow: 7n * 86_400n,
    check: [1, `0x${"00".repeat(32)}`],
    disputeQuorumSnapshot: 3,
    inflightJobCount: 2n,
  };
}

/** Stub IntentExecutor: records every intent, returns a canonical result. */
class RecordingExecutor implements IntentExecutor {
  intents: Intent[] = [];
  constructor(
    private result: TxResult = {
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt: null,
    },
  ) {}

  async execute(intent: Intent): Promise<TxResult> {
    this.intents.push(intent);
    return { ...this.result };
  }
}

/** A minimal wallet whose makeExecutor is fully controlled by the test.
 * `signedTxs` records every self-pay transaction handed to
 * `signTransaction` — the only self-pay path any of these tests exercise
 * is the ERC-20 `approve()` inside `fund()` (every other write goes
 * through `executeIntent`/`RecordingExecutor`), so this doubles as "was
 * approve() called, and with what calldata". */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  makeExecutorImpl: ((context: ExecutionContext) => IntentExecutor) | null =
    null;
  signedTxs: TransactionRequestLegacy[] = [];

  get address(): `0x${string}` {
    return WALLET_ADDRESS;
  }

  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    this.signedTxs.push(tx);
    return {
      rawTransaction: "0xdeadbeef",
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }

  override makeExecutor(context: ExecutionContext): IntentExecutor {
    if (!this.makeExecutorImpl) {
      throw new Error("StubWallet.makeExecutorImpl not configured");
    }
    return this.makeExecutorImpl(context);
  }
}

async function buildClient(opts: {
  results?: Record<string, unknown>;
  walletProvider?: WalletProvider | null;
  network?: NetworkConfig;
}) {
  const results = opts.results ?? defaultResults();
  const network = opts.network ?? fakeNetwork();
  const mock = mockPublicClient({
    eth_chainId: () => toChainIdHex(network.chainId),
    eth_call: combinedReadHandler(results),
  });
  createPublicClientForMock.mockReset().mockReturnValue(mock.client);
  const client = await ERC8183Client.create({
    walletProvider: opts.walletProvider ?? null,
    network,
  });
  return { client, mock, results };
}

describe("ERC8183Client.create", () => {
  it("builds a read-only client when walletProvider is omitted", async () => {
    const { client } = await buildClient({});
    expect(client.address).toBeNull();
  });

  it.each([0n, 5_000n])(
    "fund(%s) on a read-only client fails before any post-construction RPC",
    async (amount) => {
      const { client, mock } = await buildClient({});
      const rpcCount = mock.calls.length;
      await expect(client.fund(1n, amount)).rejects.toThrow(
        /wallet_provider is required for write operations \(client is read-only\)/,
      );
      expect(mock.calls).toHaveLength(rpcCount);
    },
  );

  it("rejects a network missing a required ERC-8183 contract address", async () => {
    await expect(
      buildClient({ network: fakeNetwork({ commerceContract: "" }) }),
    ).rejects.toThrow(/commerce_contract/);
  });

  it("hard-fails on a chain_id mismatch (defense-in-depth)", async () => {
    const network = fakeNetwork({ chainId: 97 });
    const mock = mockPublicClient({
      eth_chainId: () => toChainIdHex(1), // ethereum mainnet, not 97
      eth_call: combinedReadHandler(defaultResults()),
    });
    createPublicClientForMock.mockReset().mockReturnValue(mock.client);
    await expect(ERC8183Client.create({ network })).rejects.toThrow(
      /chain_id mismatch/,
    );
  });

  it("exposes commerce/router/policy sub-clients, address, and network", async () => {
    const wallet = new StubWallet();
    const { client } = await buildClient({ walletProvider: wallet });
    expect(client.commerce.address).toBe(FAKE_COMMERCE);
    expect(client.router.address).toBe(FAKE_ROUTER);
    expect(client.policy.address).toBe(FAKE_POLICY);
    expect(client.address).toBe(WALLET_ADDRESS);
    expect(client.network.name).toBe("test-net");
  });

  it("applies ERC8183_*_ADDRESS env overrides to a string preset (QA stack)", async () => {
    // Digit-only addresses are checksum-invariant, so they compare verbatim.
    const overrides = {
      ERC8183_COMMERCE_ADDRESS: `0x${"44".repeat(20)}`,
      ERC8183_ROUTER_ADDRESS: `0x${"55".repeat(20)}`,
      ERC8183_POLICY_ADDRESS: `0x${"66".repeat(20)}`,
    };
    Object.assign(process.env, overrides);
    try {
      const mock = mockPublicClient({
        eth_chainId: () => toChainIdHex(97),
        eth_call: combinedReadHandler(defaultResults()),
      });
      createPublicClientForMock.mockReset().mockReturnValue(mock.client);
      const client = await ERC8183Client.create({ network: "bsc-testnet" });
      expect(client.network.commerceContract).toBe(
        overrides.ERC8183_COMMERCE_ADDRESS,
      );
      expect(client.network.routerContract).toBe(
        overrides.ERC8183_ROUTER_ADDRESS,
      );
      expect(client.network.policyContract).toBe(
        overrides.ERC8183_POLICY_ADDRESS,
      );
      expect(client.commerce.address).toBe(overrides.ERC8183_COMMERCE_ADDRESS);
      expect(client.router.address).toBe(overrides.ERC8183_ROUTER_ADDRESS);
      expect(client.policy.address).toBe(overrides.ERC8183_POLICY_ADDRESS);
    } finally {
      for (const key of Object.keys(overrides)) delete process.env[key];
    }
  });

  it("ignores ERC8183_* env when network is a concrete NetworkConfig (caller takes full control)", async () => {
    const overrides = { ERC8183_COMMERCE_ADDRESS: `0x${"44".repeat(20)}` };
    Object.assign(process.env, overrides);
    try {
      const { client } = await buildClient({});
      expect(client.network.commerceContract).toBe(FAKE_COMMERCE);
    } finally {
      for (const key of Object.keys(overrides)) delete process.env[key];
    }
  });
});

describe("ERC8183Client: paymaster wiring", () => {
  it("wires a paymaster on chain 97", async () => {
    PaymasterMock.mockClear();
    await buildClient({ network: fakeNetwork({ chainId: 97 }) });
    expect(PaymasterMock).toHaveBeenCalledWith(
      "https://paymaster.example.com",
      false,
    );
  });

  it("never wires a paymaster on bsc-mainnet (56), even with usePaymaster+paymasterUrl set", async () => {
    PaymasterMock.mockClear();
    await buildClient({
      network: fakeNetwork({ chainId: 56, name: "main-net" }),
    });
    expect(PaymasterMock).not.toHaveBeenCalled();
  });

  it("ERC8183_PAYMASTER_CHAIN_IDS contains only 97", () => {
    expect([...ERC8183_PAYMASTER_CHAIN_IDS]).toEqual([97]);
  });
});

describe("ERC8183Client: token cache", () => {
  it("caches paymentToken() after the first call", async () => {
    const { client, mock } = await buildClient({});
    await expect(client.paymentToken()).resolves.toBe(FAKE_TOKEN);
    await expect(client.paymentToken()).resolves.toBe(FAKE_TOKEN);
    const paymentTokenCalls = mock.calls.filter((c) => {
      if (c.method !== "eth_call") return false;
      try {
        const [{ data }] = c.params as [{ data: Hex }];
        return (
          decodeFunctionData({ abi: agenticCommerceAbi, data }).functionName ===
          "paymentToken"
        );
      } catch {
        return false;
      }
    });
    expect(paymentTokenCalls).toHaveLength(1);
  });

  it("tokenDecimals/tokenSymbol resolve via the ERC-20 client", async () => {
    const { client } = await buildClient({});
    await expect(client.tokenDecimals()).resolves.toBe(18);
    await expect(client.tokenSymbol()).resolves.toBe("USDT");
  });

  it("keeps per-token clients and metadata caches separate for 6/18 decimals", async () => {
    const usdc = getAsset(97, AssetId.TEST_USDC).address;
    const usdt = getAsset(97, AssetId.TEST_USDT).address;
    const results = defaultResults();
    results.decimals = ({ to }: { to: `0x${string}` }) =>
      to === usdc ? 6 : 18;
    results.symbol = ({ to }: { to: `0x${string}` }) =>
      to === usdc ? "USDC" : "USDT";
    const { client, mock } = await buildClient({ results });

    const usdcMetadata = await client.tokenMetadata(usdc.toLowerCase());
    expect(await client.tokenMetadata(usdc)).toBe(usdcMetadata);
    const usdtMetadata = await client.tokenMetadata(AssetId.TEST_USDT);

    expect(usdcMetadata).toEqual({
      address: usdc,
      decimals: 6,
      symbol: "USDC",
    });
    expect(usdtMetadata).toEqual({
      address: usdt,
      decimals: 18,
      symbol: "USDT",
    });
    const metadataCalls = mock.calls.filter((call) => {
      if (call.method !== "eth_call") return false;
      try {
        const [{ data }] = call.params as [{ data: Hex }];
        const name = decodeFunctionData({ abi: erc20Abi, data }).functionName;
        return name === "decimals" || name === "symbol";
      } catch {
        return false;
      }
    });
    expect(metadataCalls).toHaveLength(4);
  });

  it("isolates decimals and symbol caches when one metadata field fails", async () => {
    const results = defaultResults();
    let symbolCalls = 0;
    results.decimals = 6;
    results.symbol = () => {
      symbolCalls += 1;
      if (symbolCalls === 1) throw new Error("temporary symbol failure");
      return "USDC";
    };
    const { client } = await buildClient({ results });

    await expect(client.tokenDecimals(CUSTOM_TOKEN)).resolves.toBe(6);
    await expect(client.tokenMetadata(CUSTOM_TOKEN_INPUT)).rejects.toThrow(
      /temporary symbol failure/,
    );
    await expect(client.tokenMetadata(CUSTOM_TOKEN)).resolves.toEqual({
      address: CUSTOM_TOKEN,
      decimals: 6,
      symbol: "USDC",
    });
    expect(symbolCalls).toBe(2);
  });

  it("keeps a cached symbol usable when decimals metadata fails", async () => {
    const results = defaultResults();
    let decimalsCalls = 0;
    results.symbol = "CUSTOM";
    results.decimals = () => {
      decimalsCalls += 1;
      throw new Error("decimals unavailable");
    };
    const { client } = await buildClient({ results });

    await expect(client.tokenSymbol(CUSTOM_TOKEN_INPUT)).resolves.toBe(
      "CUSTOM",
    );
    await expect(client.tokenMetadata(CUSTOM_TOKEN)).rejects.toThrow(
      /decimals unavailable/,
    );
    await expect(client.tokenSymbol(CUSTOM_TOKEN)).resolves.toBe("CUSTOM");
    expect(decimalsCalls).toBe(1);
  });

  it("keeps legacy helpers on Commerce.paymentToken", async () => {
    const results = defaultResults();
    results.balanceOf = 9n;
    results.allowance = 8n;
    const wallet = new StubWallet();
    wallet.makeExecutorImpl = () => new RecordingExecutor();
    const { client } = await buildClient({ walletProvider: wallet, results });

    await expect(client.paymentToken()).resolves.toBe(FAKE_TOKEN);
    await expect(client.tokenDecimals()).resolves.toBe(18);
    await expect(client.tokenSymbol()).resolves.toBe("USDT");
    await expect(client.tokenBalance()).resolves.toBe(9n);
    await expect(
      client.tokenAllowance(WALLET_ADDRESS, FAKE_COMMERCE),
    ).resolves.toBe(8n);
    await client.approvePaymentToken(FAKE_COMMERCE, 7n);
    expect(wallet.signedTxs.at(-1)?.to).toBe(FAKE_TOKEN);
  });

  it("selected-token balance/allowance/approve helpers target that token", async () => {
    const results = defaultResults();
    results.balanceOf = 55n;
    results.allowance = 44n;
    const wallet = new StubWallet();
    wallet.makeExecutorImpl = () => new RecordingExecutor();
    const { client } = await buildClient({ walletProvider: wallet, results });

    await expect(
      client.tokenBalanceFor(CUSTOM_TOKEN_INPUT, WALLET_ADDRESS),
    ).resolves.toBe(55n);
    await expect(
      client.tokenAllowanceFor(CUSTOM_TOKEN, WALLET_ADDRESS, FAKE_COMMERCE),
    ).resolves.toBe(44n);
    await client.approveToken(CUSTOM_TOKEN_INPUT, FAKE_COMMERCE, 33n);
    expect(wallet.signedTxs.at(-1)?.to).toBe(CUSTOM_TOKEN);
  });

  it("exposes job token and support reads for canonical IDs and addresses", async () => {
    const usdc = getAsset(97, AssetId.TEST_USDC).address;
    const results = defaultResults();
    results.jobPaymentToken = CUSTOM_TOKEN;
    results.isPaymentTokenSupported = false;
    const { client } = await buildClient({ results });

    await expect(client.jobPaymentToken(7n)).resolves.toBe(CUSTOM_TOKEN);
    await expect(
      client.isPaymentTokenSupported(AssetId.TEST_USDC),
    ).resolves.toBe(false);
    await expect(
      client.isPaymentTokenSupported(usdc.toLowerCase()),
    ).resolves.toBe(false);
  });
});

describe("ERC8183Client.createJob", () => {
  const FAR_FUTURE = BigInt(Math.floor(Date.now() / 1000) + 30 * 86_400);

  function wiredClient() {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor({
      transactionHash: FAKE_TX_HASH,
      status: 1,
      receipt: null,
      jobId: 1n,
    });
    wallet.makeExecutorImpl = () => executor;
    return { wallet, executor };
  }

  it("defaults evaluator and hook to the router address", async () => {
    const { wallet, executor } = wiredClient();
    const { client } = await buildClient({ walletProvider: wallet });
    await client.createJob({
      expiredAt: FAR_FUTURE,
      description: "d",
      skipExpiryCheck: true,
    });
    expect(executor.intents).toHaveLength(1);
    const [intent] = executor.intents;
    expect(intent.name).toBe(ERC8183_CREATE_JOB);
    expect(intent.kwargs?.evaluator).toBe(FAKE_ROUTER);
    expect(intent.kwargs?.hook).toBe(FAKE_ROUTER);
    expect(intent.kwargs?.provider).toBe(ZERO_ADDRESS);
  });

  it("allows overriding hook", async () => {
    const { wallet, executor } = wiredClient();
    const { client } = await buildClient({ walletProvider: wallet });
    const customHook = getAddress(`0x${"11".repeat(20)}`);
    await client.createJob({
      expiredAt: FAR_FUTURE,
      description: "d",
      hook: customHook,
      skipExpiryCheck: true,
    });
    expect(executor.intents[0]?.kwargs?.hook).toBe(customHook);
    expect(executor.intents[0]?.kwargs?.evaluator).toBe(FAKE_ROUTER);
  });

  it("rejects expiredAt within the dispute window", async () => {
    const { wallet, executor } = wiredClient();
    const results = defaultResults();
    results.disputeWindow = 7n * 86_400n;
    const { client } = await buildClient({ walletProvider: wallet, results });
    const tooClose = BigInt(Math.floor(Date.now() / 1000) + 86_400); // 24h, inside 7d window
    await expect(
      client.createJob({ expiredAt: tooClose, description: "d" }),
    ).rejects.toThrow(/dispute_window/);
    expect(executor.intents).toHaveLength(0);
  });

  it("accepts expiredAt beyond the dispute window", async () => {
    const { wallet, executor } = wiredClient();
    const results = defaultResults();
    results.disputeWindow = 7n * 86_400n;
    const { client } = await buildClient({ walletProvider: wallet, results });
    const farEnough = BigInt(Math.floor(Date.now() / 1000) + 8 * 86_400 + 60);
    await client.createJob({ expiredAt: farEnough, description: "d" });
    expect(executor.intents).toHaveLength(1);
  });

  it("skipExpiryCheck bypasses the guard even for an expiredAt inside the window", async () => {
    const { wallet, executor } = wiredClient();
    const results = defaultResults();
    results.disputeWindow = 7n * 86_400n;
    const { client } = await buildClient({ walletProvider: wallet, results });
    const soon = BigInt(Math.floor(Date.now() / 1000) + 60);
    await client.createJob({
      expiredAt: soon,
      description: "d",
      skipExpiryCheck: true,
    });
    expect(executor.intents).toHaveLength(1);
  });

  it("createJobWithToken resolves current-chain canonical ID and emits the new intent", async () => {
    const { wallet, executor } = wiredClient();
    const { client } = await buildClient({ walletProvider: wallet });
    const token = getAsset(97, AssetId.TEST_USDC).address;
    await client.createJobWithToken({
      asset: AssetId.TEST_USDC,
      expiredAt: FAR_FUTURE,
      description: "six decimals",
      skipExpiryCheck: true,
    });
    expect(executor.intents[0]?.name).toBe(ERC8183_CREATE_JOB_WITH_TOKEN);
    expect(executor.intents[0]?.kwargs).toMatchObject({
      provider: ZERO_ADDRESS,
      evaluator: FAKE_ROUTER,
      hook: FAKE_ROUTER,
      token,
    });
  });

  it("accepts a lowercase current-chain catalog address", async () => {
    const { wallet, executor } = wiredClient();
    const { client } = await buildClient({ walletProvider: wallet });
    const token = getAsset(97, AssetId.TEST_USDT).address;
    await client.createJobWithToken({
      asset: token.toLowerCase(),
      expiredAt: FAR_FUTURE,
      skipExpiryCheck: true,
    });
    expect(executor.intents[0]?.kwargs?.token).toBe(token);
  });

  it.each(["USDC", "USDT", AssetId.BINANCE_PEG_USDC])(
    "rejects alias/cross-chain asset %s for a known chain",
    async (asset) => {
      const { wallet, executor } = wiredClient();
      const { client } = await buildClient({ walletProvider: wallet });
      await expect(
        client.createJobWithToken({
          asset,
          expiredAt: FAR_FUTURE,
          skipExpiryCheck: true,
        }),
      ).rejects.toThrow();
      expect(executor.intents).toHaveLength(0);
    },
  );

  it("rejects a non-catalog address on a known chain", async () => {
    const { wallet, executor } = wiredClient();
    const { client } = await buildClient({ walletProvider: wallet });
    await expect(
      client.createJobWithToken({
        asset: CUSTOM_TOKEN,
        expiredAt: FAR_FUTURE,
        skipExpiryCheck: true,
      }),
    ).rejects.toThrow(/not registered/);
    expect(executor.intents).toHaveLength(0);
  });

  it("custom chain accepts only a direct address", async () => {
    const { wallet, executor } = wiredClient();
    const network = fakeNetwork({ chainId: 12_345, name: "custom" });
    const { client } = await buildClient({ walletProvider: wallet, network });
    await client.createJobWithToken({
      asset: CUSTOM_TOKEN_INPUT,
      expiredAt: FAR_FUTURE,
      skipExpiryCheck: true,
    });
    expect(executor.intents[0]?.kwargs?.token).toBe(CUSTOM_TOKEN);

    await expect(
      client.createJobWithToken({
        asset: AssetId.TEST_USDC,
        expiredAt: FAR_FUTURE,
        skipExpiryCheck: true,
      }),
    ).rejects.toThrow(/chain_id=12345/);
  });
});

describe("ERC8183Client.registerJob", () => {
  it("binds the configured policy by default", async () => {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client } = await buildClient({ walletProvider: wallet });
    await client.registerJob(1n);
    expect(executor.intents[0]?.kwargs).toEqual({
      jobId: 1n,
      policy: FAKE_POLICY,
    });
  });

  it("allows a policy override", async () => {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client } = await buildClient({ walletProvider: wallet });
    const otherPolicy = getAddress(`0x${"ee".repeat(20)}`);
    await client.registerJob(1n, otherPolicy);
    expect(executor.intents[0]?.kwargs).toEqual({
      jobId: 1n,
      policy: otherPolicy,
    });
  });
});

describe("ERC8183Client.fund: job-token approval strategy", () => {
  async function primedClient(opts: {
    allowance?: bigint;
    decimals?: number;
    walletProvider?: StubWallet;
    jobToken?: `0x${string}`;
  }) {
    const wallet = opts.walletProvider ?? new StubWallet();
    if (!wallet.makeExecutorImpl) {
      const executor = new RecordingExecutor();
      wallet.makeExecutorImpl = () => executor;
    }
    const results = defaultResults();
    results.allowance = opts.allowance ?? 0n;
    results.decimals = opts.decimals ?? 18;
    results.jobPaymentToken = opts.jobToken ?? FAKE_TOKEN;
    const { client, mock } = await buildClient({
      walletProvider: wallet,
      results,
    });
    return { client, wallet, mock, results };
  }

  /** Every entry in `wallet.signedTxs` in this describe block IS an
   * approve() call: fund() itself is an intent-seam write recorded by
   * RecordingExecutor, never a self-pay tx. */
  function decodedApprove(wallet: StubWallet, index = 0) {
    const tx = wallet.signedTxs[index];
    if (!tx) throw new Error(`no signed tx at index ${index}`);
    return decodeFunctionData({ abi: erc20Abi, data: tx.data as Hex });
  }

  function allowanceCallCount(
    mock: Awaited<ReturnType<typeof buildClient>>["mock"],
  ): number {
    return mock.calls.filter((c) => {
      if (c.method !== "eth_call") return false;
      try {
        const [{ data }] = c.params as [{ data: Hex }];
        return (
          decodeFunctionData({ abi: erc20Abi, data }).functionName ===
          "allowance"
        );
      } catch {
        return false;
      }
    }).length;
  }

  function commerceCallCount(
    mock: Awaited<ReturnType<typeof buildClient>>["mock"],
    functionName: string,
  ): number {
    return mock.calls.filter((call) => {
      if (call.method !== "eth_call") return false;
      try {
        const [{ data }] = call.params as [{ data: Hex }];
        return (
          decodeFunctionData({ abi: agenticCommerceAbi, data }).functionName ===
          functionName
        );
      } catch {
        return false;
      }
    }).length;
  }

  it("skips approve when allowance is already sufficient", async () => {
    const { client, wallet } = await primedClient({
      allowance: 10_000n,
      decimals: 18,
    });
    const result = await client.fund(1n, 5_000n);
    expect(result.status).toBe(1);
    expect(wallet.signedTxs).toHaveLength(0);
  });

  it("approves the exact amount by default", async () => {
    const { client, wallet } = await primedClient({
      allowance: 0n,
      decimals: 6,
    });
    await client.fund(1n, 1n * 10n ** 6n);
    expect(wallet.signedTxs).toHaveLength(1);
    const decoded = decodedApprove(wallet);
    expect(decoded.functionName).toBe("approve");
    expect(decoded.args).toEqual([FAKE_COMMERCE, 1n * 10n ** 6n]);
  });

  it("approves a large amount exactly by default", async () => {
    const { client, wallet } = await primedClient({
      allowance: 0n,
      decimals: 6,
    });
    const big = 500n * 10n ** 6n;
    await client.fund(1n, big);
    expect(decodedApprove(wallet).args).toEqual([FAKE_COMMERCE, big]);
  });

  it("approveFloor=0 means exact amount", async () => {
    const { client, wallet } = await primedClient({
      allowance: 0n,
      decimals: 6,
    });
    await client.fund(1n, 5n, { approveFloor: 0n });
    expect(decodedApprove(wallet).args).toEqual([FAKE_COMMERCE, 5n]);
  });

  it("honors a custom approveFloor", async () => {
    const { client, wallet } = await primedClient({
      allowance: 0n,
      decimals: 6,
    });
    await client.fund(1n, 5n, { approveFloor: 1_000n });
    expect(decodedApprove(wallet).args).toEqual([FAKE_COMMERCE, 1_000n]);
  });

  it("rejects a negative approveFloor", async () => {
    const { client, wallet, mock } = await primedClient({ allowance: 0n });
    const rpcCount = mock.calls.length;
    await expect(client.fund(1n, 5n, { approveFloor: -1n })).rejects.toThrow(
      /approve_floor must be >= 0/,
    );
    expect(mock.calls).toHaveLength(rpcCount);
    expect(wallet.signedTxs).toHaveLength(0);
  });

  it("rejects a negative amount before wallet or RPC validation", async () => {
    const { client, wallet, mock } = await primedClient({ allowance: 0n });
    const rpcCount = mock.calls.length;
    (
      client as unknown as {
        walletProvider: WalletProvider | null;
        address: `0x${string}` | null;
      }
    ).walletProvider = null;
    (client as unknown as { address: `0x${string}` | null }).address = null;
    await expect(client.fund(1n, -1n)).rejects.toThrow(/amount must be >= 0/);
    expect(mock.calls).toHaveLength(rpcCount);
    expect(wallet.signedTxs).toHaveLength(0);
  });

  it("negative amount wins over a negative approveFloor", async () => {
    const { client, mock } = await primedClient({ allowance: 0n });
    const rpcCount = mock.calls.length;
    await expect(client.fund(1n, -1n, { approveFloor: -2n })).rejects.toThrow(
      /amount must be >= 0/,
    );
    expect(mock.calls).toHaveLength(rpcCount);
  });

  it.each([
    [5, undefined, /amount must be a bigint/],
    [5n, 1, /approve_floor must be a bigint/],
  ] as const)(
    "rejects non-bigint amount/floor without implicit conversion",
    async (amount, approveFloor, message) => {
      const { client, mock } = await primedClient({ allowance: 0n });
      const rpcCount = mock.calls.length;
      await expect(
        client.fund(1n, amount as unknown as bigint, {
          approveFloor: approveFloor as unknown as bigint | undefined,
        }),
      ).rejects.toThrow(message);
      expect(mock.calls).toHaveLength(rpcCount);
    },
  );

  it.each([
    ["wallet", 0n],
    ["wallet", 5n],
    ["address", 0n],
    ["address", 5n],
  ] as const)(
    "fails for a missing %s with amount %s before job-token RPC",
    async (missing, amount) => {
      const { client, mock } = await primedClient({ allowance: 0n });
      const rpcCount = mock.calls.length;
      if (missing === "wallet") {
        (
          client as unknown as { walletProvider: WalletProvider | null }
        ).walletProvider = null;
      } else {
        (client as unknown as { address: `0x${string}` | null }).address = null;
      }
      await expect(client.fund(1n, amount)).rejects.toThrow(
        /wallet_provider is required for write operations \(client is read-only\)/,
      );
      expect(mock.calls).toHaveLength(rpcCount);
    },
  );

  it("uses the non-default job token for allowance and approve", async () => {
    const { client, wallet, mock } = await primedClient({
      allowance: 0n,
      jobToken: CUSTOM_TOKEN,
    });
    await client.fund(2n, 99n);
    expect(commerceCallCount(mock, "jobPaymentToken")).toBe(1);
    expect(allowanceCallCount(mock)).toBe(1);
    expect(wallet.signedTxs).toHaveLength(1);
    expect(wallet.signedTxs[0]?.to).toBe(CUSTOM_TOKEN);
    expect(decodedApprove(wallet).args).toEqual([FAKE_COMMERCE, 99n]);
    expect(commerceCallCount(mock, "paymentToken")).toBe(0);
  });

  it("throws a typed mismatch before ERC-20 or fund actions", async () => {
    const { client, wallet, mock } = await primedClient({
      allowance: 0n,
      jobToken: FAKE_TOKEN,
    });
    let caught: unknown;
    try {
      await client.fund(7n, 5n, { expectedToken: CUSTOM_TOKEN_INPUT });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(JobPaymentTokenMismatchError);
    expect(caught).toMatchObject({
      jobId: 7n,
      expectedToken: CUSTOM_TOKEN,
      actualToken: FAKE_TOKEN,
    });
    expect(allowanceCallCount(mock)).toBe(0);
    expect(wallet.signedTxs).toHaveLength(0);
  });

  it("direct-address expectedToken only checksums and does not require catalog membership", async () => {
    const { client, mock } = await primedClient({
      allowance: 10n,
      jobToken: CUSTOM_TOKEN,
    });
    await client.fund(8n, 5n, { expectedToken: CUSTOM_TOKEN_INPUT });
    expect(commerceCallCount(mock, "jobPaymentToken")).toBe(1);
    expect(allowanceCallCount(mock)).toBe(1);
  });

  it("resolves a canonical expected token for the current chain", async () => {
    const usdc = getAsset(97, AssetId.TEST_USDC).address;
    const { client, mock } = await primedClient({
      allowance: 10n,
      jobToken: usdc,
    });
    await client.fund(3n, 5n, { expectedToken: AssetId.TEST_USDC });
    expect(commerceCallCount(mock, "jobPaymentToken")).toBe(1);
    expect(allowanceCallCount(mock)).toBe(1);
  });

  it("zero amount validates the job token but never reads allowance or approves", async () => {
    const { client, wallet, mock } = await primedClient({ allowance: 0n });
    await client.fund(1n, 0n, { expectedToken: FAKE_TOKEN.toLowerCase() });
    expect(commerceCallCount(mock, "jobPaymentToken")).toBe(1);
    expect(allowanceCallCount(mock)).toBe(0);
    expect(wallet.signedTxs).toHaveLength(0);
  });

  it("a wallet with fundBundlesApproval === true (literal) skips allowance management entirely", async () => {
    class BundlingWallet extends StubWallet {
      override readonly fundBundlesApproval = true;
    }
    const wallet = new BundlingWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client, mock } = await primedClient({
      allowance: 0n,
      walletProvider: wallet,
    });
    await client.fund(1n, 5_000n);
    expect(commerceCallCount(mock, "jobPaymentToken")).toBe(1);
    expect(allowanceCallCount(mock)).toBe(0);
    expect(wallet.signedTxs).toHaveLength(0);
    const fundIntents = executor.intents.filter((i) => i.name === ERC8183_FUND);
    expect(fundIntents).toHaveLength(1);
  });

  it("a truthy-but-not-true fundBundlesApproval does NOT skip the allowance path", async () => {
    const wallet = new StubWallet();
    // Simulate a non-boolean truthy value the way a loose mock object
    // might produce — the guard must be `=== true`, not merely truthy.
    (
      wallet as unknown as { fundBundlesApproval: unknown }
    ).fundBundlesApproval = 1;
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client, mock } = await primedClient({
      allowance: 10_000n,
      walletProvider: wallet,
    });
    await client.fund(1n, 5_000n);
    // Allowance was consulted (not skipped) even though the value itself
    // was truthy-but-not-`true` ...
    expect(allowanceCallCount(mock)).toBe(1);
    // ... and since it was sufficient, no approve was needed either way.
    expect(wallet.signedTxs).toHaveLength(0);
    const fundIntents = executor.intents.filter((i) => i.name === ERC8183_FUND);
    expect(fundIntents).toHaveLength(1);
  });
});

describe("ERC8183Client: write delegation table", () => {
  async function wiredClient() {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client } = await buildClient({ walletProvider: wallet });
    return { client, executor };
  }

  it("settle delegates to router", async () => {
    const { client, executor } = await wiredClient();
    await client.settle(7n, "0x01");
    expect(executor.intents[0]?.name).toBe(ERC8183_SETTLE);
    expect(executor.intents[0]?.kwargs).toEqual({
      jobId: 7n,
      evidence: "0x01",
    });
  });

  it("dispute delegates to policy", async () => {
    const { client, executor } = await wiredClient();
    await client.dispute(7n);
    expect(executor.intents[0]?.name).toBe(ERC8183_DISPUTE);
  });

  it("voteReject delegates to policy", async () => {
    const { client, executor } = await wiredClient();
    await client.voteReject(7n);
    expect(executor.intents[0]?.name).toBe(ERC8183_VOTE_REJECT);
  });

  it("claimRefund delegates to commerce", async () => {
    const { client, executor } = await wiredClient();
    await client.claimRefund(7n);
    expect(executor.intents[0]?.name).toBe(ERC8183_CLAIM_REFUND);
  });

  it("cancelOpen delegates to commerce.reject", async () => {
    const { client, executor } = await wiredClient();
    await client.cancelOpen(7n);
    expect(executor.intents[0]?.name).toBe(ERC8183_REJECT);
  });
});

describe("ERC8183Client.submit", () => {
  it("encodes optParams as compact canonical JSON UTF-8 bytes", async () => {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client } = await buildClient({ walletProvider: wallet });
    const deliverable = `0x${"00".repeat(32)}` as const;
    await client.submit(7n, deliverable, {
      deliverable_url: "https://example.com/job.json",
    });
    expect(executor.intents[0]?.name).toBe(ERC8183_SUBMIT);
    const optParamsHex = executor.intents[0]?.kwargs?.optParams as Hex;
    expect(hexToString(optParamsHex)).toBe(
      '{"deliverable_url":"https://example.com/job.json"}',
    );
  });

  it("requires a non-empty deliverable_url", async () => {
    const wallet = new StubWallet();
    const executor = new RecordingExecutor();
    wallet.makeExecutorImpl = () => executor;
    const { client } = await buildClient({ walletProvider: wallet });
    const deliverable = `0x${"00".repeat(32)}` as const;
    await expect(
      client.submit(7n, deliverable, { deliverable_url: "" }),
    ).rejects.toThrow(/deliverable_url/);
    await expect(
      // biome-ignore lint/suspicious/noExplicitAny: exercising the runtime guard for a missing key
      client.submit(7n, deliverable, {} as any),
    ).rejects.toThrow(/deliverable_url/);
    expect(executor.intents).toHaveLength(0);
  });
});

describe("ERC8183Client: views", () => {
  const RAW_JOB = {
    id: 1n,
    client: WALLET_ADDRESS,
    provider: FAKE_TOKEN,
    evaluator: FAKE_ROUTER,
    description: "d",
    budget: 100n,
    expiredAt: 999n,
    status: 1,
    hook: ZERO_ADDRESS,
    submittedAt: 5n,
    deliverable: `0x${"aa".repeat(32)}` as const,
  };

  it("getJob decodes the tuple; getJobStatus extracts .status", async () => {
    const results = defaultResults();
    results.getJob = RAW_JOB;
    const { client } = await buildClient({ results });
    await expect(client.getJob(1n)).resolves.toMatchObject({
      id: 1n,
      status: 1,
    });
    await expect(client.getJobStatus(1n)).resolves.toBe(1);
  });

  it("getVerdict delegates to policy.check", async () => {
    const results = defaultResults();
    results.check = [1, `0x${"00".repeat(32)}`];
    const { client } = await buildClient({ results });
    const [verdict] = await client.getVerdict(1n);
    expect(verdict).toBe(1);
  });

  it("resolves the JobFunded block inside the signed quote time window", async () => {
    const { client, mock } = await buildClient({});
    mock.handlers.eth_blockNumber = () => "0x3e8"; // 1000
    vi.spyOn(client.publicClient, "getBlock").mockImplementation(
      async ({ blockNumber } = {}) =>
        ({ timestamp: blockNumber ?? 1000n }) as never,
    );
    const scans = vi
      .spyOn(client.commerce, "getJobFundedEvents")
      .mockImplementation(async (fromBlock, toBlock) => {
        if (fromBlock === 100n && toBlock === 200n) {
          return [{ blockNumber: 150n }] as never;
        }
        return [];
      });

    const blockNumber = await (
      client as unknown as {
        getJobFundedBlock(
          jobId: bigint,
          window: { negotiatedAt: number; quoteExpiresAt: number },
        ): Promise<bigint | null>;
      }
    ).getJobFundedBlock(7n, { negotiatedAt: 100, quoteExpiresAt: 200 });

    expect(blockNumber).toBe(150n);
    expect(scans).toHaveBeenCalledWith(100n, 200n, undefined, 7n);
  });

  it("inflightJobCount delegates to router", async () => {
    const results = defaultResults();
    results.inflightJobCount = 4n;
    const { client } = await buildClient({ results });
    await expect(client.inflightJobCount()).resolves.toBe(4n);
  });

  it("disputeQuorumSnapshot delegates to policy and coerces to bigint", async () => {
    const results = defaultResults();
    results.disputeQuorumSnapshot = 3;
    const { client } = await buildClient({ results });
    await expect(client.disputeQuorumSnapshot(1n)).resolves.toBe(3n);
  });

  it("getDeliverableUrl forwards an explicit hintBlock without the facade's own self-resolve walk-back", async () => {
    const results = defaultResults();
    const { client, mock } = await buildClient({ results });
    mock.handlers.eth_getLogs = () => [];
    const url = await client.getDeliverableUrl(1n, { hintBlock: 42n });
    expect(url).toBeNull();
    // PolicyClient.getDeliverableUrl always reads the head block once
    // internally; the facade's own resolveSubmitBlock walk-back (which
    // would add its own getBlockNumber + getLogs calls) must be skipped
    // entirely since a hintBlock was supplied — exactly one call total.
    const blockNumberCalls = mock.calls.filter(
      (c) => c.method === "eth_blockNumber",
    );
    expect(blockNumberCalls).toHaveLength(1);
  });
});
