/**
 * Live BSC-testnet E2E for the Turnkey wallet provider — 5 gated steps.
 *
 * ⚠️ SPENDS REAL MONEY-SHAPED RESOURCES. Every successful Turnkey signature
 * is BILLED against the org's quota (free tier: 25 signatures/month at
 * 1 request/second; pay-as-you-go $0.10/signature). A full run consumes
 * exactly 5 billed signatures plus a few 10⁻⁵ tBNB of gas for two
 * self-transfers. Calls are strictly serial with a ≥1.1 s gap (free-tier
 * rate limit); the chain-id assertion runs BEFORE anything billable.
 *
 * ⚠️ Production posture reminder: run this with a NON-ROOT API user
 * restricted by an explicit ALLOW policy — a root user's API key bypasses
 * ALL Turnkey server-side policies (root quorum). The SDK-side
 * SigningPolicy still applies either way.
 *
 * What the steps prove (mapping to the 2026-07-24 probe findings):
 *   1. EIP-191 blind digest signing recovers to the Turnkey address.
 *   2. EIP-712 signing binds the REAL domain — the live regression check
 *      for the `@turnkey/viem` ≤0.14.34 EIP712Domain-stripping trap the
 *      provider patches (an empty-domain signature would fail the
 *      recoverTypedDataAddress comparison here).
 *   3. A legacy (gasPrice) self-transfer signs, broadcasts over our own
 *      RPC (managed broadcast is paywalled — never used) and lands.
 *   4. An EIP-1559 self-transfer does the same.
 *   5. `X402Signer.signPayment` works against the provider unchanged —
 *      the "implement signTypedData and x402 comes free" contract.
 * ERC-8004 registration through the full ContractInterface→LocalExecutor
 * pipeline was probe-verified on-chain (agentId=1727) and is not repeated
 * here to protect the signature budget.
 *
 * Usage (env in `typescript/.env`, never committed):
 *     TURNKEY_E2E=1
 *     TURNKEY_API_PUBLIC_KEY=...   TURNKEY_API_PRIVATE_KEY=...
 *     TURNKEY_ORG_ID=...           TURNKEY_SIGN_WITH=0x...
 *     # optional: TURNKEY_API_BASE_URL, RPC_URL
 *     pnpm -C typescript run e2e:turnkey
 *
 * The TURNKEY_SIGN_WITH address needs a little tBNB for gas (~0.001).
 * Exits 0 only when every step PASSes; any FAIL aborts and exits 1.
 * Deliberately NOT part of CI.
 */

import {
  http,
  createPublicClient,
  formatEther,
  hashMessage,
  recoverMessageAddress,
  recoverTypedDataAddress,
} from "viem";
import { NETWORKS } from "../../src/config.js";
import { loadEnv } from "../../src/core/env.js";
import { getEnv } from "../../src/core/envUtil.js";
import { BNB_CHAIN_ADDRESSES } from "../../src/networks/addresses.js";
import { TurnkeyWalletProvider } from "../../src/wallets/turnkey/provider.js";
import { X402Signer } from "../../src/x402/signer.js";

const CHAIN_ID = 97;
const GAP_MS = 1100; // free tier: 1 request/second — stay under it
const SIGNATURE_BUDGET = 5;

let billedSignatures = 0;
let lastVendorCallAt = 0;

/** Serialize vendor calls (≥1.1 s apart) and count the signature budget. */
async function vendor<T>(label: string, fn: () => Promise<T>): Promise<T> {
  if (billedSignatures >= SIGNATURE_BUDGET) {
    throw new Error(
      `signature budget of ${SIGNATURE_BUDGET} exhausted before '${label}'`,
    );
  }
  const wait = lastVendorCallAt + GAP_MS - Date.now();
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  lastVendorCallAt = Date.now();
  const result = await fn();
  billedSignatures += 1;
  console.log(`   [budget] ${billedSignatures}/${SIGNATURE_BUDGET} billed signatures`);
  return result;
}

function pass(step: string, detail: string): void {
  console.log(`✅ ${step} — ${detail}`);
}

async function main(): Promise<void> {
  loadEnv();
  if (getEnv("TURNKEY_E2E") !== "1") {
    console.log(
      "TURNKEY_E2E != 1 — refusing to run (this script consumes billed Turnkey signatures and testnet gas). Set TURNKEY_E2E=1 plus the TURNKEY_* env vars in typescript/.env to opt in.",
    );
    return;
  }

  const network = NETWORKS["bsc-testnet"];
  if (!network) throw new Error("bsc-testnet preset missing");
  const paymentToken = BNB_CHAIN_ADDRESSES[CHAIN_ID]?.paymentToken;
  if (!paymentToken) throw new Error(`no payment token registered for chain ${CHAIN_ID}`);
  const rpcUrl = getEnv("RPC_URL") ?? network.rpcUrl;
  const client = createPublicClient({ transport: http(rpcUrl) });

  // ── Gate 0: chain identity, BEFORE anything billable ────────────────
  const chainId = await client.getChainId();
  if (chainId !== CHAIN_ID) {
    throw new Error(`RPC ${rpcUrl} reports chainId=${chainId}, need ${CHAIN_ID}`);
  }

  const wallet = TurnkeyWalletProvider.fromEnv({ expectedChainId: CHAIN_ID });
  const address = wallet.address;
  const balance = await client.getBalance({ address });
  console.log(
    `turnkey e2e: signer=${address} balance=${formatEther(balance)} tBNB rpc=${rpcUrl}`,
  );
  if (balance < 300_000_000_000_000n) {
    throw new Error(
      `signer balance ${formatEther(balance)} tBNB is below the ~0.0003 needed for two self-transfers — fund ${address} first`,
    );
  }

  // ── 1. EIP-191 ──────────────────────────────────────────────────────
  const message = `turnkey-e2e ${new Date().toISOString()}`;
  const signed191 = await vendor("eip191", () => wallet.signMessage(message));
  if (signed191.messageHash !== hashMessage(message)) {
    throw new Error("191 digest mismatch");
  }
  const recovered191 = await recoverMessageAddress({
    message,
    signature: signed191.signature,
  });
  if (recovered191 !== address) {
    throw new Error(`191 recovered ${recovered191}, want ${address}`);
  }
  pass("1/5 EIP-191", `recovered ${recovered191}`);

  // ── 2. EIP-712 with domain binding (the stripping-trap live check) ──
  const nowSec = Math.floor(Date.now() / 1000);
  const domain = {
    name: "United Stables",
    version: "1",
    chainId: CHAIN_ID,
    verifyingContract: paymentToken,
  };
  const types = {
    TransferWithAuthorization: [
      { name: "from", type: "address" },
      { name: "to", type: "address" },
      { name: "value", type: "uint256" },
      { name: "validAfter", type: "uint256" },
      { name: "validBefore", type: "uint256" },
      { name: "nonce", type: "bytes32" },
    ],
  };
  const message712 = {
    from: address,
    to: address,
    value: 1n,
    validAfter: BigInt(nowSec - 10),
    validBefore: BigInt(nowSec + 580),
    nonce: `0x${crypto.getRandomValues(new Uint8Array(32)).reduce((acc, b) => acc + b.toString(16).padStart(2, "0"), "")}` as `0x${string}`,
  };
  const signed712 = await vendor("eip712", () =>
    wallet.signTypedData(domain, types, message712),
  );
  const recovered712 = await recoverTypedDataAddress({
    domain,
    types,
    primaryType: "TransferWithAuthorization",
    message: message712,
    signature: signed712.signature,
  });
  if (recovered712 !== address) {
    throw new Error(
      `712 recovered ${recovered712} against the REAL domain, want ${address} — empty-domain binding (the 0.14.x trap) would fail exactly here`,
    );
  }
  pass("2/5 EIP-712", `real-domain recovery ${recovered712}`);

  // ── 3+4. legacy and 1559 self-transfers over our own RPC ────────────
  const gasPrice = await client.getGasPrice();
  let nonce = await client.getTransactionCount({ address, blockTag: "pending" });

  for (const [step, tx] of [
    [
      "3/5 legacy tx",
      { chainId: CHAIN_ID, to: address, value: 1n, gas: 21_000n, nonce: nonce++, gasPrice },
    ],
    [
      "4/5 eip-1559 tx",
      {
        chainId: CHAIN_ID,
        to: address,
        value: 1n,
        gas: 21_000n,
        nonce: nonce++,
        maxFeePerGas: gasPrice * 2n,
        maxPriorityFeePerGas: gasPrice,
      },
    ],
  ] as const) {
    const signedTx = await vendor(step, () =>
      wallet.signTransaction(tx as Parameters<typeof wallet.signTransaction>[0]),
    );
    const hash = await client.sendRawTransaction({
      serializedTransaction: signedTx.rawTransaction,
    });
    const receipt = await client.waitForTransactionReceipt({ hash, timeout: 120_000 });
    if (receipt.status !== "success") throw new Error(`${step}: reverted ${hash}`);
    pass(step, `landed ${hash}`);
  }

  // ── 5. x402 free-ride through X402Signer ────────────────────────────
  const signer = new X402Signer(wallet, {
    maxValuePerCall: { [paymentToken]: 10n },
    sessionBudget: { [paymentToken]: 10n },
  });
  const payment = await vendor("x402 signPayment", () =>
    signer.signPayment({
      domain: domain as Record<string, unknown>,
      types,
      message: { ...message712, value: 10n },
      expectedTo: address,
    }),
  );
  const recoveredPay = await recoverTypedDataAddress({
    domain,
    types,
    primaryType: "TransferWithAuthorization",
    message: { ...message712, value: 10n },
    signature: payment.signature as `0x${string}`,
  });
  if (recoveredPay !== address) {
    throw new Error(`x402 payment recovered ${recoveredPay}, want ${address}`);
  }
  pass("5/5 X402Signer", "payment signature recovers; policy + budget gates passed");

  console.log(`\nall 5 steps PASS — ${billedSignatures} billed signatures used`);
}

main().catch((error) => {
  console.error("E2E FAILED:", error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
