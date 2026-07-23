import { describe, expect, it } from "vitest";
import {
  ABILoadError,
  BNBAgentError,
  ConfigurationError,
  ContractError,
  ERC8004PartialRegistrationError,
  JobError,
  NegotiationError,
  NetworkError,
  RelayFallbackFailedError,
  RelaySubmissionUnverifiedError,
  RpcRangeLimitError,
  StorageError,
  TransactionPendingError,
} from "../src/errors.js";

describe("Error Hierarchy", () => {
  it("StorageError is instance of BNBAgentError", () => {
    const error = new StorageError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(StorageError);
  });

  it("ContractError is instance of BNBAgentError", () => {
    const error = new ContractError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(ContractError);
  });

  it("ConfigurationError is instance of BNBAgentError", () => {
    const error = new ConfigurationError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(ConfigurationError);
  });

  it("ABILoadError is instance of BNBAgentError", () => {
    const error = new ABILoadError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(ABILoadError);
  });

  it("NetworkError is instance of BNBAgentError", () => {
    const error = new NetworkError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(NetworkError);
  });

  it("RpcRangeLimitError is instance of NetworkError and BNBAgentError", () => {
    const error = new RpcRangeLimitError("test");
    expect(error).toBeInstanceOf(NetworkError);
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(RpcRangeLimitError);
  });

  it("JobError is instance of BNBAgentError", () => {
    const error = new JobError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(JobError);
  });

  it("NegotiationError is instance of BNBAgentError", () => {
    const error = new NegotiationError("test");
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(NegotiationError);
  });
});

describe("TransactionPendingError", () => {
  it("has txHash and timeoutSeconds fields", () => {
    const error = new TransactionPendingError("0xabc123", 30);
    expect(error.txHash).toBe("0xabc123");
    expect(error.timeoutSeconds).toBe(30);
  });

  it("generates default message when not provided", () => {
    const error = new TransactionPendingError("0xabc123", 30);
    expect(error.message).toBe(
      "Transaction 0xabc123 broadcast but not confirmed within 30s; check later or retry safely.",
    );
  });

  it("uses provided message when given", () => {
    const customMsg = "Custom message";
    const error = new TransactionPendingError("0xabc123", 30, customMsg);
    expect(error.message).toBe(customMsg);
  });

  it("is instance of BNBAgentError", () => {
    const error = new TransactionPendingError("0xabc123", 30);
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(TransactionPendingError);
  });

  it("sets name to TransactionPendingError", () => {
    const error = new TransactionPendingError("0xabc123", 30);
    expect(error.name).toBe("TransactionPendingError");
  });
});

describe("ERC8004PartialRegistrationError", () => {
  it("contains agent_id in message", () => {
    const cause = new Error("some cause");
    const error = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
    );
    expect(error.message).toContain("agent_id=1");
  });

  it("appends pending tx_hash only when txHash is provided", () => {
    const cause = new Error("some cause");
    const errorWithoutHash = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
    );
    expect(errorWithoutHash.message).not.toContain("pending tx_hash=");

    const errorWithHash = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
      "0xtxhash123",
    );
    expect(errorWithHash.message).toContain("pending tx_hash=0xtxhash123");
  });

  it("has all required fields", () => {
    const cause = new Error("some cause");
    const error = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
      "0xtx",
      true,
    );
    expect(error.agentId).toBe(1);
    expect(error.agentUri).toBe("http://example.com");
    expect(error.cause).toBe(cause);
    expect(error.txHash).toBe("0xtx");
    expect(error.retryable).toBe(true);
  });

  it("is instance of BNBAgentError", () => {
    const cause = new Error("some cause");
    const error = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
    );
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error).toBeInstanceOf(ERC8004PartialRegistrationError);
  });

  it("sets name to ERC8004PartialRegistrationError", () => {
    const cause = new Error("some cause");
    const error = new ERC8004PartialRegistrationError(
      1,
      "http://example.com",
      cause,
    );
    expect(error.name).toBe("ERC8004PartialRegistrationError");
  });
});

describe("RelaySubmissionUnverifiedError", () => {
  it("default message carries the escape-hatch hint", () => {
    const error = new RelaySubmissionUnverifiedError("0xabc123", 30);
    expect(error.message).toContain("0xabc123");
    expect(error.message).toContain("BNBAGENT_USE_PAYMASTER=0");
    expect(error.message).toContain("usePaymaster: false");
  });

  it("uses a provided message verbatim", () => {
    const error = new RelaySubmissionUnverifiedError("0xabc", 30, "custom");
    expect(error.message).toBe("custom");
  });
});

describe("RelayFallbackFailedError", () => {
  it("extends RelaySubmissionUnverifiedError so classification is inherited", () => {
    const error = new RelayFallbackFailedError("0xabc", 30, "no gas");
    expect(error).toBeInstanceOf(RelaySubmissionUnverifiedError);
    expect(error).toBeInstanceOf(BNBAgentError);
    expect(error.name).toBe("RelayFallbackFailedError");
  });

  it("surfaces the fallback reason and funding guidance", () => {
    const error = new RelayFallbackFailedError(
      "0xabc",
      30,
      "wallet 0xdead has insufficient BNB for gas",
    );
    expect(error.fallbackReason).toBe(
      "wallet 0xdead has insufficient BNB for gas",
    );
    expect(error.message).toContain("insufficient BNB");
    expect(error.message).toContain("tBNB faucet");
  });

  it("retains the underlying cause when provided", () => {
    const cause = new Error("insufficient funds for gas * price + value");
    const error = new RelayFallbackFailedError("0xabc", 30, "no gas", {
      cause,
    });
    expect(error.cause).toBe(cause);
  });
});
