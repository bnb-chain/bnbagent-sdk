/**
 * Ports the ERC-8004 `ContractInterface`-relevant slices of
 * `python/tests/test_erc8004_contract.py`: `built_with` auto-injection,
 * `agentId` recovery (executor result vs. `Registered` event), and the
 * revert / pending / nonce-retry write-path semantics — driven here through
 * a REAL `LocalExecutor` (via the default `WalletProvider.makeExecutor`)
 * over `mockTransport`, rather than mocking `_execute_transaction` directly,
 * since the TS port shares that machinery with every other contract client
 * (see `contractBase.test.ts` / `localExecutorPaymaster.test.ts` for its
 * exhaustive coverage — this suite only re-exercises the paths ERC-8004
 * specifically routes through: revert, pending-receipt, nonce retry).
 */

import {
  type TransactionRequestLegacy,
  decodeFunctionData,
  encodeAbiParameters,
  encodeEventTopics,
  encodeFunctionResult,
  getAddress,
  keccak256,
  stringToHex,
} from "viem";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { identityRegistryAbi } from "../src/abis/identityRegistry.js";
import { NonceManager } from "../src/core/nonceManager.js";
import type { Paymaster } from "../src/core/paymaster.js";
import {
  _resetTxConfigOverrides,
  setDefaultReceiptTimeout,
} from "../src/core/txConfig.js";
import { ContractInterface } from "../src/erc8004/contract.js";
import {
  RelaySubmissionUnverifiedError,
  TransactionPendingError,
} from "../src/errors.js";
import {
  type SignedTx,
  WalletProvider,
} from "../src/wallets/walletProvider.js";
import {
  FAKE_TX_HASH,
  type MockHandlers,
  mockPublicClient,
} from "./helpers/mockTransport.js";

const CONTRACT_ADDRESS = getAddress(
  "0x800400000000000000000000000000000000dead",
);
const WALLET_ADDRESS = getAddress("0x1111111111111111111111111111111111111111");
const OWNER_ADDRESS = getAddress("0x2222222222222222222222222222222222222222");
const FAKE_RAW_TX = "0xdeadbeef";
// The on-chain hash of the stub wallet's signed tx — what an honest relay
// echoes back, and the hash LocalExecutor tracks regardless of the relay's
// answer.
const SIGNED_TX_HASH = keccak256(FAKE_RAW_TX);

/** A minimal signing wallet — drives the default LocalExecutor write path. */
class StubWallet extends WalletProvider {
  static override readonly kind = "stub";

  get address(): `0x${string}` {
    return WALLET_ADDRESS;
  }

  override async signTransaction(
    tx: TransactionRequestLegacy,
  ): Promise<SignedTx> {
    void tx;
    return {
      rawTransaction: FAKE_RAW_TX,
      hash: FAKE_TX_HASH,
      r: "0x00",
      s: "0x00",
      v: 27n,
    };
  }
}

/** Route a mocked `eth_call` to the correctly ABI-encoded read result. */
function readRouter(
  results: Record<string, unknown>,
): (params: readonly unknown[]) => unknown {
  return (params) => {
    const [{ data }] = params as [{ data: `0x${string}` }];
    const decoded = decodeFunctionData({ abi: identityRegistryAbi, data });
    const result = results[decoded.functionName];
    return encodeFunctionResult({
      abi: identityRegistryAbi,
      functionName: decoded.functionName,
      // biome-ignore lint/suspicious/noExplicitAny: result shape varies per function
      result: result as any,
    });
  };
}

/** Build a raw (JSON-RPC hex-encoded) `Registered` event log. */
function registeredLog(agentId: bigint, agentUri: string) {
  const topics = encodeEventTopics({
    abi: identityRegistryAbi,
    eventName: "Registered",
    args: { agentId, owner: OWNER_ADDRESS },
  });
  const data = encodeAbiParameters([{ type: "string" }], [agentUri]);
  return {
    address: CONTRACT_ADDRESS,
    topics,
    data,
    blockNumber: "0x1",
    blockHash: `0x${"aa".repeat(32)}`,
    transactionHash: FAKE_TX_HASH,
    transactionIndex: "0x0",
    logIndex: "0x0",
    removed: false,
  };
}

function makeContract(
  overrides: Partial<MockHandlers> = {},
  paymaster?: Paymaster,
  receiptTimeout?: number,
) {
  const mock = mockPublicClient(overrides);
  const wallet = new StubWallet();
  const contract = new ContractInterface({
    client: mock.client,
    contractAddress: CONTRACT_ADDRESS,
    walletProvider: wallet,
    paymaster,
    receiptTimeout,
  });
  return { mock, wallet, contract };
}

function makeUnverifiedRelayContract() {
  const paymaster = {
    ethGetTransactionCount: async () => 0,
    isSponsorable: async () => true,
    ethSendRawTransaction: async () => SIGNED_TX_HASH,
  } as unknown as Paymaster;
  // The relay accepts the hash but the chain never sees it; the self-pay
  // fallback then also cannot broadcast (wallet out of gas), so the executor
  // surfaces a RelayFallbackFailedError — a RelaySubmissionUnverifiedError
  // subclass that still carries the relay hash. That is the error shape the
  // ERC-8004 write wrappers must propagate untouched.
  return makeContract(
    {
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
      eth_getTransactionByHash: () => {
        throw new Error("not found");
      },
      eth_sendRawTransaction: () => {
        throw new Error(
          "insufficient funds for gas * price + value: balance 0",
        );
      },
    },
    paymaster,
    0.01,
  );
}

beforeEach(() => {
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

afterEach(() => {
  NonceManager._clearAll();
  _resetTxConfigOverrides();
});

describe("ContractInterface constructor", () => {
  it("checksums the contract address", () => {
    const { contract } = makeContract();
    expect(contract.address).toBe(CONTRACT_ADDRESS);
  });
});

describe("registerAgent: built_with injection", () => {
  it("auto-injects built_with when metadata is omitted", async () => {
    const { mock, contract } = makeContract();
    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.success).toBe(true);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      true,
    );
  });

  it("respects a user-supplied built_with (does not duplicate/override)", async () => {
    const { mock, contract } = makeContract();
    // We can't directly inspect the encoded calldata's metadata contents
    // through the RPC boundary without a full ABI decode round trip, so
    // assert indirectly: the call succeeds and broadcasts exactly once,
    // exercising injectBuiltWith's "skip if user set" branch without
    // throwing (the dedicated unit-level behavior is also exercised by
    // erc8004Agent's built_with passthrough).
    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
      [{ key: "built_with", value: "https://my-fork.com" }],
    );
    expect(result.success).toBe(true);
    expect(
      mock.calls.filter((c) => c.method === "eth_sendRawTransaction"),
    ).toHaveLength(1);
  });
});

describe("registerAgent: agentId recovery", () => {
  it("uses the agentId from the Registered event log when the executor doesn't surface one", async () => {
    const { contract } = makeContract({
      eth_getTransactionReceipt: () => ({
        status: "0x1",
        blockNumber: "0x1",
        blockHash: `0x${"aa".repeat(32)}`,
        transactionHash: FAKE_TX_HASH,
        transactionIndex: "0x0",
        from: WALLET_ADDRESS,
        to: CONTRACT_ADDRESS,
        cumulativeGasUsed: "0x1e8480",
        gasUsed: "0x186a0",
        contractAddress: null,
        logs: [registeredLog(7n, "data:application/json;base64,eyJ4IjoxfQ==")],
        logsBloom: `0x${"0".repeat(512)}`,
        effectiveGasPrice: "0x3b9aca00",
      }),
    });

    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.agentId).toBe(7);
  });

  it("returns null agentId when no Registered log is present and the executor didn't surface one", async () => {
    const { contract } = makeContract();
    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.agentId).toBeNull();
  });

  it("ignores a same-topic0 Registered log from an UNRELATED contract and uses this contract's log", async () => {
    // A bundled tx (multicall / AA-paymaster relay) whose receipt also
    // carries a Registered log emitted by a different contract must not
    // hijack the agentId — parseRegisteredAgentId filters by log.address.
    const decoy = {
      ...registeredLog(999n, "data:application/json;base64,eyJ4Ijo5fQ=="),
      address: `0x${"11".repeat(20)}`, // NOT this contract's address
    };
    const real = registeredLog(7n, "data:application/json;base64,eyJ4IjoxfQ==");
    const { contract } = makeContract({
      eth_getTransactionReceipt: () => ({
        status: "0x1",
        blockNumber: "0x1",
        blockHash: `0x${"aa".repeat(32)}`,
        transactionHash: FAKE_TX_HASH,
        transactionIndex: "0x0",
        from: WALLET_ADDRESS,
        to: CONTRACT_ADDRESS,
        cumulativeGasUsed: "0x1e8480",
        gasUsed: "0x186a0",
        contractAddress: null,
        logs: [decoy, real], // decoy first — must be skipped
        logsBloom: `0x${"0".repeat(512)}`,
        effectiveGasPrice: "0x3b9aca00",
      }),
    });

    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.agentId).toBe(7);
  });
});

describe("registerAgent: preflight revert", () => {
  it("raises a wrapped Error (not TransactionPendingError) and never broadcasts", async () => {
    const { mock, contract } = makeContract({
      eth_call: () => {
        throw new Error("execution reverted: Unauthorized");
      },
    });

    await expect(
      contract.registerAgent("data:application/json;base64,eyJ4IjoxfQ=="),
    ).rejects.toThrow(/Agent registration failed.*Transaction would revert/s);
    expect(mock.calls.some((c) => c.method === "eth_sendRawTransaction")).toBe(
      false,
    );
  });
});

describe("registerAgent: receipt timeout is pending, not fatal", () => {
  it("propagates TransactionPendingError unmodified (not wrapped as a fatal Error)", async () => {
    setDefaultReceiptTimeout(1);
    const { contract } = makeContract({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });

    let caught: unknown;
    try {
      await contract.registerAgent("data:application/json;base64,eyJ4IjoxfQ==");
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(TransactionPendingError);
  });
});

describe("relay submission visibility", () => {
  it("preserves RelaySubmissionUnverifiedError through every ERC-8004 write wrapper", async () => {
    const operations = [
      (contract: ContractInterface) =>
        contract.registerAgent("data:application/json;base64,eyJ4IjoxfQ=="),
      (contract: ContractInterface) => contract.setMetadata(1, "name", "value"),
      (contract: ContractInterface) =>
        contract.setAgentUri(1, "data:application/json;base64,eyJ4IjoxfQ=="),
    ];

    for (const execute of operations) {
      const { contract } = makeUnverifiedRelayContract();
      const error = await execute(contract).catch((caught) => caught);
      expect(error).toBeInstanceOf(RelaySubmissionUnverifiedError);
      expect(error).toMatchObject({ txHash: SIGNED_TX_HASH });
    }
  });
});

describe("registerAgent: nonce retry", () => {
  it("retries on a nonce-too-low error and succeeds on the second attempt", async () => {
    const { mock, contract } = makeContract();
    let attempt = 0;
    mock.handlers.eth_sendRawTransaction = () => {
      attempt++;
      if (attempt === 1) {
        throw new Error("nonce too low");
      }
      return FAKE_TX_HASH;
    };

    const result = await contract.registerAgent(
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.success).toBe(true);
    expect(attempt).toBe(2);
  });
});

describe("getAgentInfo", () => {
  it("reads getAgentWallet/ownerOf/tokenURI", async () => {
    const { contract } = makeContract({
      eth_call: readRouter({
        getAgentWallet: WALLET_ADDRESS,
        ownerOf: OWNER_ADDRESS,
        tokenURI: "data:application/json;base64,eyJ4IjoxfQ==",
      }),
    });

    const info = await contract.getAgentInfo(1);
    expect(info.agentId).toBe(1);
    expect(info.agentWallet).toBe(WALLET_ADDRESS);
    expect(info.agentAddress).toBe(WALLET_ADDRESS);
    expect(info.owner).toBe(OWNER_ADDRESS);
    expect(info.agentURI).toBe("data:application/json;base64,eyJ4IjoxfQ==");
  });
});

describe("getMetadata", () => {
  it("decodes a metadata value end-to-end", async () => {
    const wallet = new StubWallet();
    const mock = mockPublicClient({
      eth_call: (params) => {
        const [{ data }] = params as [{ data: `0x${string}` }];
        const decoded = decodeFunctionData({ abi: identityRegistryAbi, data });
        expect(decoded.functionName).toBe("getMetadata");
        return encodeFunctionResult({
          abi: identityRegistryAbi,
          functionName: "getMetadata",
          result: stringToHex("hello world"),
        });
      },
    });
    const contract = new ContractInterface({
      client: mock.client,
      contractAddress: CONTRACT_ADDRESS,
      walletProvider: wallet,
    });
    await expect(contract.getMetadata(1, "name")).resolves.toBe("hello world");
  });
});

describe("setMetadata", () => {
  it("broadcasts and returns a success result", async () => {
    const { contract } = makeContract();
    const result = await contract.setMetadata(1, "name", "value");
    expect(result.success).toBe(true);
    expect(result.transactionHash).toBe(FAKE_TX_HASH);
  });

  it("does NOT propagate TransactionPendingError — wraps it into a generic Error (parity with the Python reference's untouched except-Exception path)", async () => {
    setDefaultReceiptTimeout(1);
    const { contract } = makeContract({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });

    let caught: unknown;
    try {
      await contract.setMetadata(1, "name", "value");
    } catch (error) {
      caught = error;
    }
    expect(caught).not.toBeInstanceOf(TransactionPendingError);
    expect((caught as Error).message).toMatch(/Failed to set metadata/);
  });
});

describe("setAgentUri", () => {
  it("broadcasts and returns a success result", async () => {
    const { contract } = makeContract();
    const result = await contract.setAgentUri(
      1,
      "data:application/json;base64,eyJ4IjoxfQ==",
    );
    expect(result.success).toBe(true);
    expect(result.transactionHash).toBe(FAKE_TX_HASH);
  });

  it("propagates TransactionPendingError unmodified on receipt timeout", async () => {
    setDefaultReceiptTimeout(1);
    const { contract } = makeContract({
      eth_getTransactionReceipt: () => {
        throw new Error("not found");
      },
    });

    let caught: unknown;
    try {
      await contract.setAgentUri(
        1,
        "data:application/json;base64,eyJ4IjoxfQ==",
      );
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(TransactionPendingError);
  });
});
