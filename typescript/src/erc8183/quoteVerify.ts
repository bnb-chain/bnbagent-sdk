/** Reference verification for ERC-8183 provider quote signatures. */

import type { PublicClient } from "viem";
import { getAddress, hashMessage, parseAbi, recoverMessageAddress } from "viem";
import { canonicalJson, keccakOfText } from "../core/canonicalJson.js";
import { buildDescriptionContent } from "./negotiation.js";

const ERC1271_MAGIC_VALUE = "0x1626ba7e";
const ERC1271_ABI = parseAbi([
  "function isValidSignature(bytes32 hash, bytes signature) view returns (bytes4)",
]);
const HASH_HEX = /^0x[0-9a-fA-F]{64}$/;
const SIGNATURE_HEX = /^0x(?:[0-9a-fA-F]{2})+$/;

export type QuoteSigVerdict =
  | {
      valid: true;
      method: "eip191" | "erc1271";
      signer: `0x${string}`;
    }
  | { valid: false; reason: string };

export interface VerifyQuoteSignatureOpts {
  /** Full `NegotiationResult` envelope or flattened on-chain description. */
  envelope: Record<string, unknown>;
  /** Expected provider account (`job.provider`). */
  provider: `0x${string}`;
  /** Chain reads used for account signatures and time/chain anchoring. */
  publicClient: PublicClient;
  /** Expected commerce/checker contract, when the caller has one in context. */
  expectedVerifyingContract?: `0x${string}`;
  /**
   * Historical acceptance block (normally the `JobFunded` block). Omit
   * before acceptance to verify against current chain state.
   */
  blockNumber?: bigint;
}

function signedContent(
  envelope: Record<string, unknown>,
): Record<string, unknown> {
  if (
    envelope.response !== null &&
    typeof envelope.response === "object" &&
    !Array.isArray(envelope.response)
  ) {
    return buildDescriptionContent(
      envelope,
      (envelope.chain_id as number | undefined) ?? null,
      (envelope.verifying_contract as string | undefined) ?? null,
    );
  }

  return Object.fromEntries(
    Object.entries(envelope).filter(
      ([key]) => key !== "negotiation_hash" && key !== "provider_sig",
    ),
  );
}

function quoteExpiresAt(envelope: Record<string, unknown>): unknown {
  if (
    envelope.response !== null &&
    typeof envelope.response === "object" &&
    !Array.isArray(envelope.response)
  ) {
    return (
      envelope.quote_expires_at ||
      (envelope.response as Record<string, unknown>).quote_expires_at
    );
  }
  return envelope.quote_expires_at;
}

/**
 * Verify that `provider_sig` authenticates the canonical quote content.
 *
 * EOA quotes recover directly from the EIP-191 message. Contract-account
 * quotes fall through to ERC-1271 (implemented below this EOA fast path).
 * Malformed or cryptographically invalid input returns `{ valid: false }`;
 * RPC failures throw because "unavailable" is not a cryptographic verdict.
 *
 * Passing `blockNumber` checks both quote expiry and ERC-1271 authorization
 * at that historical block. This preserves the acceptance-time verdict when
 * a session/checker is revoked later. The RPC must serve historical state.
 * An `eth_call` at a block observes end-of-block state, so exact ordering of
 * acceptance and revocation within one block requires atomic verification by
 * the accepting contract rather than this off-chain helper.
 */
export async function verifyQuoteSignature(
  opts: VerifyQuoteSignatureOpts,
): Promise<QuoteSigVerdict> {
  const negotiationHash = opts.envelope.negotiation_hash;
  const providerSig = opts.envelope.provider_sig;
  if (typeof negotiationHash !== "string" || !HASH_HEX.test(negotiationHash)) {
    return { valid: false, reason: "missing or invalid negotiation_hash" };
  }
  if (typeof providerSig !== "string" || !SIGNATURE_HEX.test(providerSig)) {
    return { valid: false, reason: "missing or invalid provider_sig" };
  }

  const signedVerifyingContract = opts.envelope.verifying_contract;
  let checker: `0x${string}` | undefined;
  if (
    signedVerifyingContract !== undefined &&
    signedVerifyingContract !== null
  ) {
    if (typeof signedVerifyingContract !== "string") {
      return { valid: false, reason: "invalid verifying_contract" };
    }
    try {
      checker = getAddress(signedVerifyingContract);
    } catch {
      return { valid: false, reason: "invalid verifying_contract" };
    }
  }
  if (opts.expectedVerifyingContract) {
    if (!checker) {
      return {
        valid: false,
        reason: "quote is not bound to a verifying_contract",
      };
    }
    if (checker !== getAddress(opts.expectedVerifyingContract)) {
      return { valid: false, reason: "verifying_contract mismatch" };
    }
  }

  const signedChainId = opts.envelope.chain_id;
  if (signedChainId !== undefined && signedChainId !== null) {
    if (
      typeof signedChainId !== "number" ||
      !Number.isSafeInteger(signedChainId) ||
      signedChainId <= 0
    ) {
      return { valid: false, reason: "invalid chain_id" };
    }
    if ((await opts.publicClient.getChainId()) !== signedChainId) {
      return { valid: false, reason: "chain_id mismatch" };
    }
  }

  let recomputed: string;
  try {
    recomputed = keccakOfText(canonicalJson(signedContent(opts.envelope)));
  } catch {
    return { valid: false, reason: "invalid quote content" };
  }
  if (recomputed.toLowerCase() !== negotiationHash.toLowerCase()) {
    return { valid: false, reason: "negotiation_hash mismatch" };
  }

  const expiry = quoteExpiresAt(opts.envelope);
  if (expiry !== undefined && expiry !== null) {
    if (typeof expiry !== "number" || !Number.isSafeInteger(expiry)) {
      return { valid: false, reason: "invalid quote_expires_at" };
    }
    const verificationBlock =
      opts.blockNumber === undefined
        ? await opts.publicClient.getBlock()
        : await opts.publicClient.getBlock({ blockNumber: opts.blockNumber });
    if (BigInt(expiry) <= verificationBlock.timestamp) {
      return { valid: false, reason: "quote has expired" };
    }
  }

  const provider = getAddress(opts.provider);
  try {
    const recovered = await recoverMessageAddress({
      message: negotiationHash,
      signature: providerSig as `0x${string}`,
    });
    if (recovered === provider) {
      return { valid: true, method: "eip191", signer: recovered };
    }
  } catch {
    // ERC-1271 envelopes are not 65-byte EOA signatures; try the account path.
  }

  const block =
    opts.blockNumber === undefined ? {} : { blockNumber: opts.blockNumber };
  const bytecode = await opts.publicClient.getBytecode({
    address: provider,
    ...block,
  });
  if (!bytecode || bytecode === "0x") {
    return { valid: false, reason: "provider signature is not valid" };
  }

  const result = await opts.publicClient.readContract({
    address: provider,
    abi: ERC1271_ABI,
    functionName: "isValidSignature",
    args: [hashMessage(negotiationHash), providerSig as `0x${string}`],
    ...(checker ? { account: checker } : {}),
    ...block,
  });
  if (String(result).toLowerCase() !== ERC1271_MAGIC_VALUE) {
    return { valid: false, reason: "ERC-1271 account rejected provider_sig" };
  }

  return { valid: true, method: "erc1271", signer: provider };
}
