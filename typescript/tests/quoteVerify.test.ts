import { type Session, signerFromPrivateKey } from "@altananetwork/sdk";
import { getAddress } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { describe, expect, it, vi } from "vitest";
import {
  NegotiationHandler,
  buildJobDescription,
} from "../src/erc8183/negotiation.js";
import { verifyQuoteSignature } from "../src/erc8183/quoteVerify.js";
import { AltanaWalletProvider } from "../src/wallets/altana/provider.js";

const NOW = 1_700_000_000;
const COMMERCE = getAddress("0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de");
const PROVIDER_KEY =
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
const ALTANA_WALLET = getAddress("0x1111111111111111111111111111111111111111");

function request(): Record<string, unknown> {
  return {
    task_description: "Summarize the report",
    terms: { deliverables: "summary", quality_standards: "accurate" },
  };
}

function mockPublicClient() {
  return {
    getChainId: vi.fn(async () => 97),
    getBlock: vi.fn(async () => ({ timestamp: BigInt(NOW + 1) })),
    getBytecode: vi.fn(async () => "0x01"),
    readContract: vi.fn(),
  };
}

function altanaSession(): Session {
  const signer = signerFromPrivateKey(PROVIDER_KEY);
  return {
    walletAddress: ALTANA_WALLET,
    signer,
    publicKey: signer.publicKey,
    permissions: { calls: [] },
    expiry: 2_000_000_000,
  };
}

async function signedEoaEnvelope(): Promise<{
  envelope: Record<string, unknown>;
  provider: `0x${string}`;
}> {
  const account = privateKeyToAccount(PROVIDER_KEY);
  const handler = new NegotiationHandler({
    servicePrice: "1000000000000000000",
    currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
    walletProvider: {
      address: account.address,
      signMessage: async (message) => ({
        signature: await account.signMessage({ message }),
      }),
    },
    chainId: 97,
    verifyingContract: COMMERCE,
    now: () => NOW,
  });
  return {
    envelope: (await handler.negotiate(request())).toDict(),
    provider: account.address,
  };
}

async function signedAltanaEnvelope(): Promise<Record<string, unknown>> {
  const provider = new AltanaWalletProvider({ session: altanaSession() });
  const handler = new NegotiationHandler({
    servicePrice: "1000000000000000000",
    currency: "0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565",
    quoteSigner: provider.sessionQuoteSigner(),
    chainId: 97,
    verifyingContract: COMMERCE,
    now: () => NOW,
  });
  return (await handler.negotiate(request())).toDict();
}

describe("verifyQuoteSignature", () => {
  it("verifies a canonical EIP-191 quote against the provider account", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const publicClient = mockPublicClient();

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: true,
      method: "eip191",
      signer: provider,
    });
    expect(publicClient.readContract).not.toHaveBeenCalled();
  });

  it("rejects a quote at its expiry using the verification block timestamp", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const response = envelope.response as Record<string, unknown>;
    const expiry = response.quote_expires_at as number;
    const publicClient = mockPublicClient();
    publicClient.getBlock.mockResolvedValue({ timestamp: BigInt(expiry) });

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
      blockNumber: 123n,
    });

    expect(verdict).toEqual({ valid: false, reason: "quote has expired" });
    expect(publicClient.getBlock).toHaveBeenCalledWith({ blockNumber: 123n });
  });

  it("enforces a full envelope's top-level quote expiry", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const response = envelope.response as Record<string, unknown>;
    envelope.quote_expires_at = response.quote_expires_at;
    response.quote_expires_at = undefined;
    const publicClient = mockPublicClient();
    publicClient.getBlock.mockResolvedValue({
      timestamp: BigInt(envelope.quote_expires_at as number),
    });

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({ valid: false, reason: "quote has expired" });
  });

  it("rejects a quote bound to a different chain", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const publicClient = mockPublicClient();
    publicClient.getChainId.mockResolvedValue(56);

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({ valid: false, reason: "chain_id mismatch" });
  });

  it("returns an invalid verdict for a malformed signed verifier address", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    envelope.verifying_contract = "not-an-address";

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: mockPublicClient() as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: false,
      reason: "invalid verifying_contract",
    });
  });

  it("returns an invalid verdict for malformed signed quote content", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const response = envelope.response as Record<string, unknown>;
    response.terms = {};

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: mockPublicClient() as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: false,
      reason: "invalid quote content",
    });
  });

  it("rejects malformed signature bytes before making chain reads", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    envelope.provider_sig = "0xnot-hex";
    const publicClient = mockPublicClient();

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: false,
      reason: "missing or invalid provider_sig",
    });
    expect(publicClient.getBlock).not.toHaveBeenCalled();
    expect(publicClient.getBytecode).not.toHaveBeenCalled();
  });

  it("rejects content tampering before attempting signature verification", async () => {
    const { envelope, provider } = await signedEoaEnvelope();
    const response = envelope.response as Record<string, unknown>;
    const terms = response.terms as Record<string, unknown>;
    terms.price = "999999999999999999999";
    const publicClient = mockPublicClient();

    const verdict = await verifyQuoteSignature({
      envelope,
      provider,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: false,
      reason: "negotiation_hash mismatch",
    });
    expect(publicClient.getBlock).not.toHaveBeenCalled();
    expect(publicClient.readContract).not.toHaveBeenCalled();
  });

  it("verifies an Altana session quote against the wallet through ERC-1271", async () => {
    const envelope = await signedAltanaEnvelope();
    const publicClient = mockPublicClient();
    publicClient.readContract.mockResolvedValue("0x1626ba7e");

    const verdict = await verifyQuoteSignature({
      envelope,
      provider: ALTANA_WALLET,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(verdict).toEqual({
      valid: true,
      method: "erc1271",
      signer: ALTANA_WALLET,
    });
    expect(publicClient.readContract).toHaveBeenCalledWith(
      expect.objectContaining({
        address: ALTANA_WALLET,
        functionName: "isValidSignature",
        account: COMMERCE,
      }),
    );
  });

  it("keeps an accepted quote valid at its anchor block when latest ERC-1271 state rejects it", async () => {
    const negotiationEnvelope = await signedAltanaEnvelope();
    const envelope = JSON.parse(
      buildJobDescription(negotiationEnvelope),
    ) as Record<string, unknown>;
    const publicClient = mockPublicClient();
    publicClient.readContract.mockImplementation(
      async (call: { blockNumber?: bigint }) =>
        call.blockNumber === 123n ? "0x1626ba7e" : "0xffffffff",
    );

    const anchored = await verifyQuoteSignature({
      envelope,
      provider: ALTANA_WALLET,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
      blockNumber: 123n,
    });
    const latest = await verifyQuoteSignature({
      envelope,
      provider: ALTANA_WALLET,
      publicClient: publicClient as never,
      expectedVerifyingContract: COMMERCE,
    });

    expect(anchored).toEqual({
      valid: true,
      method: "erc1271",
      signer: ALTANA_WALLET,
    });
    expect(latest).toEqual({
      valid: false,
      reason: "ERC-1271 account rejected provider_sig",
    });
  });
});
