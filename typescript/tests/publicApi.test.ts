/**
 * Public API surface tests.
 *
 * Verifies the Tier 1 barrel (`src/index.ts`) and every Tier 2 subpath
 * barrel (each module's `index.ts`) actually export the names documented in
 * `python/bnbagent/__init__.py` / `ARCHITECTURE.md`. This is a wiring test,
 * not a behavior test — it exists to catch "forgot to re-export X" and
 * "renamed X but not the barrel" regressions.
 */
import { describe, expect, it } from "vitest";
import * as Erc8004 from "../src/erc8004/index.js";
import * as Erc8183 from "../src/erc8183/index.js";
import * as Tier1 from "../src/index.js";
import * as Networks from "../src/networks/index.js";
import * as Signing from "../src/signing/index.js";
import * as Storage from "../src/storage/index.js";
import * as Utils from "../src/utils/index.js";
import * as Wallets from "../src/wallets/index.js";
import * as X402 from "../src/x402/index.js";

describe("Tier 1 public API (src/index.ts)", () => {
  it("exports NetworkConfig helpers", () => {
    expect(typeof Tier1.NETWORKS).toBe("object");
    expect(Tier1.NETWORKS["bsc-testnet"]).toBeDefined();
    expect(typeof Tier1.resolveNetwork).toBe("function");
  });

  it("exports the full error hierarchy", () => {
    const errorClasses = [
      "BNBAgentError",
      "ContractError",
      "StorageError",
      "ConfigurationError",
      "ABILoadError",
      "NetworkError",
      "RpcRangeLimitError",
      "JobError",
      "NegotiationError",
      "TransactionPendingError",
      "ERC8004PartialRegistrationError",
    ] as const;
    for (const name of errorClasses) {
      const cls = (Tier1 as Record<string, unknown>)[name];
      expect(typeof cls, `${name} should be a class`).toBe("function");
    }
    expect(new Tier1.BNBAgentError("x")).toBeInstanceOf(Error);
    expect(new Tier1.ContractError("x")).toBeInstanceOf(Tier1.BNBAgentError);
    expect(new Tier1.RpcRangeLimitError("x")).toBeInstanceOf(
      Tier1.NetworkError,
    );
    expect(new Tier1.TransactionPendingError("0xabc", 300)).toBeInstanceOf(
      Tier1.BNBAgentError,
    );
    expect(
      new Tier1.ERC8004PartialRegistrationError(1, null, new Error("x")),
    ).toBeInstanceOf(Tier1.BNBAgentError);
  });

  it("exports ERC-8004 essentials", () => {
    expect(typeof Tier1.ERC8004Agent).toBe("function");
    expect(typeof Tier1.AgentEndpoint).toBe("function");
  });

  it("exports wallet providers", () => {
    expect(typeof Tier1.WalletProvider).toBe("function");
    expect(typeof Tier1.EVMWalletProvider).toBe("function");
    expect(typeof Tier1.AltanaWalletProvider).toBe("function");
    expect(Tier1.AltanaWalletProvider.kind).toBe("altana");
  });

  it("exports ERC-8183 essentials", () => {
    expect(typeof Tier1.ERC8183Client).toBe("function");
    expect(typeof Tier1.JobStatus).toBe("object");
    expect(typeof Tier1.Verdict).toBe("object");
  });

  it("exports signing policy", () => {
    expect(typeof Tier1.SigningPolicy).toBe("function");
    expect(typeof Tier1.PolicyViolation).toBe("function");
    expect(new Tier1.PolicyViolation("x")).toBeInstanceOf(Error);
  });

  it("exports X402Signer", () => {
    expect(typeof Tier1.X402Signer).toBe("function");
  });

  it("exports loadEnv", () => {
    expect(typeof Tier1.loadEnv).toBe("function");
  });

  it("exports tx-tuning knobs", () => {
    expect(typeof Tier1.setMinGasPriceWei).toBe("function");
    expect(typeof Tier1.minGasPriceWei).toBe("function");
    expect(typeof Tier1.setDefaultReceiptTimeout).toBe("function");
    expect(typeof Tier1.getDefaultReceiptTimeout).toBe("function");
    expect(typeof Tier1.getDefaultReceiptTimeout()).toBe("number");
  });

  it("exports Paymaster, NonceManager, SCAN_API_URL", () => {
    expect(typeof Tier1.Paymaster).toBe("function");
    expect(typeof Tier1.NonceManager).toBe("function");
    expect(typeof Tier1.SCAN_API_URL).toBe("string");
    expect(Tier1.SCAN_API_URL.startsWith("https://")).toBe(true);
  });
});

describe("Tier 2 subpath: ./erc8004", () => {
  it("exports agent, contract, uri, config surface", () => {
    expect(typeof Erc8004.ERC8004Agent).toBe("function");
    expect(typeof Erc8004.AgentEndpoint).toBe("function");
    expect(typeof Erc8004.ContractInterface).toBe("function");
    expect(typeof Erc8004.AgentURIGenerator).toBe("object");
    expect(typeof Erc8004.AgentURIGenerator.generateRegistrationFile).toBe(
      "function",
    );
    expect(typeof Erc8004.getErc8004Config).toBe("function");
    expect(typeof Erc8004.getBuiltWithValue).toBe("function");
    expect(typeof Erc8004.BUILT_WITH_KEY).toBe("string");
    expect(typeof Erc8004.ERC8004_ENV_PREFIX).toBe("string");
  });
});

describe("Tier 2 subpath: ./erc8183", () => {
  it("exports client, sub-clients, job ops, negotiation, schema", () => {
    expect(typeof Erc8183.ERC8183Client).toBe("function");
    expect(typeof Erc8183.CommerceClient).toBe("function");
    expect(typeof Erc8183.RouterClient).toBe("function");
    expect(typeof Erc8183.PolicyClient).toBe("function");
    expect(typeof Erc8183.JobStatus).toBe("object");
    expect(typeof Erc8183.Verdict).toBe("object");
    expect(typeof Erc8183.ERC8183Config).toBe("function");
    expect(typeof Erc8183.getErc8183Config).toBe("function");
    expect(typeof Erc8183.ERC8183JobOps).toBe("function");
    expect(typeof Erc8183.fundedJobWatcher).toBe("function");
    expect(typeof Erc8183.NegotiationHandler).toBe("function");
    expect(typeof Erc8183.DeliverableManifest).toBe("function");
    expect(typeof Erc8183.JobDescription).toBe("function");
    expect(typeof Erc8183.SCHEMA_VERSION).toBe("number");
    expect(typeof Erc8183.NegotiationRequest).toBe("function");
    expect(typeof Erc8183.NegotiationResponse).toBe("function");
    expect(typeof Erc8183.NegotiationResult).toBe("function");
    expect(typeof Erc8183.TermSpecification).toBe("function");
    expect(typeof Erc8183.ReasonCode).toBe("object");
    expect(typeof Erc8183.buildJobDescription).toBe("function");
    expect(typeof Erc8183.parseJobDescription).toBe("function");
    expect(typeof Erc8183.verifyQuoteSignature).toBe("function");
    expect(Erc8183.ERR_QUOTE_INVALID).toBe("quote_invalid");
    expect(Erc8183.ERR_TX_UNVERIFIED).toBe("tx_unverified");
  });
});

describe("Tier 2 subpath: ./x402", () => {
  it("exports signer, budget tracker, payer helpers, errors", () => {
    expect(typeof X402.X402Signer).toBe("function");
    expect(typeof X402.SessionBudgetTracker).toBe("function");
    expect(typeof X402.paymentOptionFromCli).toBe("function");
    expect(typeof X402.quoteFromCli).toBe("function");
    expect(typeof X402.X402SignerError).toBe("function");
    expect(typeof X402.X402AmountExceededError).toBe("function");
    expect(typeof X402.X402BudgetExhaustedError).toBe("function");
    expect(typeof X402.X402NoPayableRouteError).toBe("function");
    expect(typeof X402.X402PolicyError).toBe("function");
    expect(typeof X402.X402RecipientMismatchError).toBe("function");
  });
});

describe("Tier 2 subpath: ./storage", () => {
  it("exports the three storage providers", () => {
    expect(typeof Storage.StorageProvider).toBe("function");
    expect(typeof Storage.LocalStorageProvider).toBe("function");
    expect(typeof Storage.IPFSStorageProvider).toBe("function");
  });
});

describe("Tier 2 subpath: ./wallets", () => {
  it("exports providers, capabilities, intents, executor, errors", () => {
    expect(typeof Wallets.WalletProvider).toBe("function");
    expect(typeof Wallets.EVMWalletProvider).toBe("function");
    expect(typeof Wallets.LocalExecutor).toBe("function");
    expect(typeof Wallets.UnsupportedWalletOperation).toBe("function");
    expect(typeof Wallets.WalletIdentityMismatch).toBe("function");
    expect(typeof Wallets.SIGN_MESSAGE).toBe("string");
    expect(typeof Wallets.SIGN_TRANSACTION).toBe("string");
    expect(typeof Wallets.SIGN_TYPED_DATA).toBe("string");
    expect(typeof Wallets.CALLS_ARBITRARY).toBe("string");
    expect(typeof Wallets.BROADCAST_SELF).toBe("string");
    expect(typeof Wallets.X402_PAY).toBe("string");
    expect(typeof Wallets.PAYMASTER_SPONSOR).toBe("string");
  });

  it("exports the altana surface (provider, executor, serde, permissions, constants)", () => {
    expect(typeof Wallets.AltanaWalletProvider).toBe("function");
    expect(typeof Wallets.AltanaIntentExecutor).toBe("function");
    expect(typeof Wallets.serializeSession).toBe("function");
    expect(typeof Wallets.deserializeSession).toBe("function");
    expect(typeof Wallets.defaultAgentPermissions).toBe("function");
    expect(Wallets.ALTANA_SESSION_VERSION).toBe(1);
    expect(typeof Wallets.DEFAULT_NATIVE_GAS_ALLOWANCE_WEI).toBe("bigint");
    expect(Wallets.ALTANA_SDK_PACKAGE).toBe("@altananetwork/sdk");
    expect(typeof Wallets.setAltanaSdkImporter).toBe("function");
    expect(typeof Wallets.ALTANA_NONCE_RETRY_TRIES).toBe("number");
    expect(typeof Wallets.ALTANA_NONCE_RETRY_DELAY_MS).toBe("number");
  });
});

describe("Tier 2 subpath: ./signing", () => {
  it("exports policy, errors, checks, type-set constants", () => {
    expect(typeof Signing.SigningPolicy).toBe("function");
    expect(typeof Signing.PolicyViolation).toBe("function");
    expect(typeof Signing.check).toBe("function");
    expect(typeof Signing.inferPrimaryType).toBe("function");
    expect(Signing.EIP3009_TYPES).toBeInstanceOf(Set);
    expect(Signing.PERMIT_UNBOUNDED_TYPES).toBeInstanceOf(Set);
    expect(Signing.PERMIT2_SIGNATURE_TRANSFER_TYPES).toBeInstanceOf(Set);
  });
});

describe("Tier 2 subpath: ./networks", () => {
  it("exports address helpers and chain-id constants", () => {
    expect(typeof Networks.getAddress).toBe("function");
    expect(typeof Networks.BNB_CHAIN_ADDRESSES).toBe("object");
    expect(typeof Networks.knownPaymentTokens).toBe("function");
    expect(Networks.BSC_MAINNET_CHAIN_ID).toBe(56);
    expect(Networks.BSC_TESTNET_CHAIN_ID).toBe(97);
    expect(typeof Networks.PAYMENT_TOKEN_EIP712_NAME).toBe("string");
    expect(typeof Networks.PAYMENT_TOKEN_EIP712_VERSION).toBe("string");
  });
});

describe("Tier 2 subpath: ./utils", () => {
  it("exports amount helpers and the rate limiter", () => {
    expect(typeof Utils.toRaw).toBe("function");
    expect(typeof Utils.fromRaw).toBe("function");
    expect(typeof Utils.SlidingWindowLimiter).toBe("function");
    expect(typeof Utils.RateLimitExceeded).toBe("function");
  });
});
