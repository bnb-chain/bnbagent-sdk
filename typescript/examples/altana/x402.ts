/**
 * x402 session-payer verification — MAINNET, dust amounts.
 *
 * Runs the full single-account x402 story end-to-end: admin grants a
 * short-lived session, whitelists Permit2 as its signature checker, funds
 * a BOUNDED Permit2 allowance, then the session pays one x402 challenge
 * through `makeX402Payer()` — and everything is torn back down.
 * Requires `@altananetwork/sdk` >= 0.4.0; see the runbook in `README.md`.
 *
 * There is no Altana testnet yet, so this spends REAL (dust) funds:
 * ~$0.50-equiv BNB session registration + gas for 3 relay calls + the
 * payment itself (default cap 0.10 token units).
 *
 * Env (typescript/.env, gitignored):
 *   PRIVATE_KEY       admin EOA == the Altana wallet; needs BNB + the token
 *   X402_ENDPOINT     a paid endpoint answering 402 on BNB chain
 *   X402_TOKEN        payment token (default: BSC USDC 0x8AC7…580d)
 *   X402_MAX_PAYMENT  per-request cap, atomic units (default: 100000)
 */

import { getAddress } from "viem";
import { loadEnv } from "../../src/core/env.js";
import { getEnv } from "../../src/core/envUtil.js";
import {
  AltanaWalletProvider,
  defaultAgentPermissions,
} from "../../src/wallets/index.js";

const USDC_BSC: `0x${string}` = "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d";

function requireEnv(name: string): string {
  const value = getEnv(name);
  if (!value) {
    throw new Error(`${name} is required (set it in typescript/.env)`);
  }
  return value;
}

async function main(): Promise<void> {
  loadEnv();
  const privateKey = requireEnv("PRIVATE_KEY");
  const endpoint = requireEnv("X402_ENDPOINT");
  const token = getAddress((getEnv("X402_TOKEN") ?? USDC_BSC) as `0x${string}`);
  const maxPayment = BigInt(getEnv("X402_MAX_PAYMENT") ?? "100000");
  const allowance = maxPayment * 5n; // the on-chain ceiling for this run
  // Relay fees charged in this token instead of native BNB (lets a
  // BNB-less wallet run the whole flow); default: the payment token.
  const feeToken = getAddress(
    (getEnv("X402_FEE_TOKEN") ?? token) as `0x${string}`,
  );

  const admin = new AltanaWalletProvider({ privateKey });
  console.log(`[1/7] wallet ${admin.address} — granting a 1h session`);
  // A short-lived x402 payment key is the textbook case for
  // `register: false` (SDK >= 0.5.0): same on-chain enforcement, no
  // ~$0.50 KeyStore registration fee — the key is just invisible to
  // registry readers like verify_authorization, which x402 never uses.
  // Kept registered here until the flow is re-verified end-to-end
  // against 0.5.0; flip it after that run.
  const session = await admin.grantSession({
    permissions: defaultAgentPermissions({
      chainId: 56,
      tokenSpend: { limit: allowance },
    }),
    expiry: Math.floor(Date.now() / 1000) + 3600,
    feeToken,
  });

  console.log("[2/7] approving Permit2 as the session's signature checker");
  await admin.approveX402SignatureChecker(session, { feeToken });

  console.log(
    `[3/7] bounding the Permit2 allowance to ${allowance} of ${token}`,
  );
  await admin.setPermit2Allowance(token, allowance, { feeToken });

  const agent = new AltanaWalletProvider({ session });
  const payer = agent.makeX402Payer({
    sessionBudget: { [token]: allowance },
    expectedAsset: token, // only this token may leave the wallet
  });

  console.log(`[4/7] quoting ${endpoint}`);
  const quote = await payer.quote(endpoint);
  console.log(
    quote.accepts.length
      ? quote.accepts
          .map(
            (o) => `  route ${o.scheme}@${o.network}: ${o.amount} → ${o.payTo}`,
          )
          .join("\n")
      : "  endpoint did not challenge (nothing to pay)",
  );

  console.log(`[5/7] paying (maxPayment ${maxPayment})`);
  const result = await payer.request(endpoint, { maxPayment });
  console.log(
    `  paid ${result.amount ?? 0n} ${result.asset ?? ""} → ${result.payTo ?? "-"}${result.transaction ? ` (tx ${result.transaction})` : ""}`,
  );
  console.log(`  response: ${JSON.stringify(result.response).slice(0, 200)}`);

  console.log("[6/7] cleanup: zeroing the Permit2 allowance");
  await admin.setPermit2Allowance(token, 0n, { feeToken });

  console.log("[7/7] cleanup: revoking the signature checker + session");
  await admin.revokeX402SignatureChecker(session, { feeToken });
  await admin.revokeSession(session, { feeToken });

  console.log("x402 session-payer verification PASSED");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
