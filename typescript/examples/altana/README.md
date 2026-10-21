# Altana wallet provider - examples & testnet E2E

[Altana](https://docs.altana.network) is non-custodial agentic-wallet infrastructure on EIP-7702: your EOA **is** the wallet, an admin key grants **session keys** whose call whitelist + spend caps + expiry are enforced by the on-chain account validator, and authorization state lives in a public KeyStore registry anyone can verify with one free `eth_call`. `AltanaWalletProvider` plugs that model into this SDK's wallet seam - the ERC-8004/8183 protocol clients run over it **unchanged**.

See the [Altana capability reference](../../../docs/altana.md) for the complete supported surface, version requirements, and security boundaries. This directory focuses on runnable examples and the testnet E2E.

## Install

The Altana SDK is an **optional peer dependency** (GPL-3.0-or-later, ESM-only). Nothing GPL ships inside `@bnbagent/sdk`; the provider lazily `import()`s it on first backend use:

```bash
pnpm add -E @altananetwork/sdk@0.7.1  # only if you use AltanaWalletProvider
```

## Two modes

```ts
import {
  AltanaWalletProvider,
  defaultAgentPermissions,
  serializeSession,
} from "@bnbagent/sdk/wallets";
import { getAddress as getDeployment } from "@bnbagent/sdk/networks";

const { paymentToken, commerceProxy } = getDeployment(56);

// ADMIN (holds the wallet's EOA key; grants/revokes sessions) -
// run this wherever the admin key lives, NOT in the agent process.
const admin = new AltanaWalletProvider({
  privateKey: process.env.PRIVATE_KEY!,
});
const session = await admin.grantSession({
  permissions: defaultAgentPermissions({
    chainId: 56,
    tokenSpend: { limit: 10n ** 18n }, // 1 token / day
  }),
  expiry: Math.floor(Date.now() / 1000) + 86_400,
});
await admin.setErc8183Allowance(paymentToken, commerceProxy, 10n ** 18n);
// Persist BYTE-EXACTLY (the chain hash-commits the granted bytes):
fs.writeFileSync(".session.json", serializeSession(session), { mode: 0o600 });

// AGENT (session mode; executes only inside the grant)
const wallet = await AltanaWalletProvider.sessionFromEnv(); // ALTANA_SESSION / ALTANA_SESSION_FILE
const jobs = await ERC8183Client.create({
  walletProvider: wallet,
  network: "bsc-mainnet",
});
```

`grantSession` generates a fresh secp256k1 session key when `sessionSigner` is omitted. A passkey (WebAuthn/P256) `sessionSigner` also works, but revoke such a session with this provider's `revokeSession`: the Altana SDK's own `revokeSession` (and earlier versions of this SDK) revoke every session as a secp256k1 key and cannot remove it. `revokeSession` recognises a passkey session from the session object or its bare 64-byte P256 public key.

`defaultAgentPermissions` whitelists selector-bound registry/Commerce/Router/Policy calls and **always** includes a small native spend cap. The payment token is spend-cap-only: session keys cannot call `approve`, so Commerce allowance must be provisioned by the admin with `setErc8183Allowance()` and kept no higher than the session token cap. Zero it when revoking the session. `extraCalls` cannot put these back: it rejects calls to catalog payment tokens and to the registry's ERC-721 approval and transfer functions.

Session persistence env vars:

| var | meaning |
| --- | --- |
| `ALTANA_SESSION` | the serialized session JSON itself (wins when both set) |
| `ALTANA_SESSION_FILE` | path to a file holding it |

The serialized payload **contains the session private key** - mode 0600, never commit, never log. Its blast radius is capped by the on-chain grant.

## ERC-8183 quotes from the session key

The pinned `@altananetwork/sdk` 0.7.1 release provides `signOrder` for quote signing. `sessionQuoteSigner()` hashes the canonical negotiation hash with EIP-191 and returns Altana's wallet-level ERC-1271 envelope. The public quote still contains only `negotiation_hash` and `provider_sig`: it does not expose the session address, add a new signature scheme, or give the agent generic message-signing authority.

The trusted environment approves the ERC-8183 Commerce/verifier as the session's signature checker once, alongside the session grant:

```ts
// Trusted admin environment only. Never copy this EOA into the agent.
await admin.approveQuoteSignatureChecker(session, COMMERCE_ADDRESS);
```

The agent process loads only the serialized session and passes the narrow signer to negotiation:

```ts
import { ERC8183Client } from "@bnbagent/sdk";
import { NegotiationHandler } from "@bnbagent/sdk/erc8183";
import { AltanaWalletProvider } from "@bnbagent/sdk/wallets";

const wallet = await AltanaWalletProvider.sessionFromEnv();
const erc8183 = await ERC8183Client.create({
  walletProvider: wallet,
  network: "bsc-mainnet",
});
const negotiation = await NegotiationHandler.fromErc8183Client(erc8183, {
  servicePrice: "1000000",
  quoteSigner: wallet.sessionQuoteSigner(),
});
```

The quote expiry is automatically clamped to the Altana session expiry. A signing failure is fail-closed: the handler does not return an accepted but unsigned quote.

The buyer verifies against the provider wallet and the expected Commerce contract. Before acceptance, omit `blockNumber` to use current state:

```ts
import { verifyQuoteSignature } from "@bnbagent/sdk/erc8183";

const verdict = await verifyQuoteSignature({
  envelope: quote,
  provider: providerWallet,
  publicClient,
  expectedVerifyingContract: COMMERCE_ADDRESS,
});
```

Before funding, current-state verification can stop an expired or revoked quote. Once funded, verify the description stored on-chain at the `JobFunded` economic-acceptance block:

```ts
const acceptedVerdict = await verifyQuoteSignature({
  envelope: JSON.parse(job.description),
  provider: job.provider,
  publicClient,
  expectedVerifyingContract: COMMERCE_ADDRESS,
  blockNumber: jobFundedBlock,
});
```

This gives the intended lifecycle: session/checker revocation can stop a quote that has not yet been funded, while a later revocation or session expiry does not rewrite the verdict anchored at funding. Seller-side `ERC8183JobOps.verifyJob()` performs this check by default before any work or submission: it locates the indexed `JobFunded(jobId)` event inside the signed `negotiated_at..quote_expires_at` window, then verifies content, provider, expiry, chain, Commerce binding, and EOA/ERC-1271 signature at that block. Removing the signature or replacing the structured description is rejected. `allowUnsignedJobs: true` exists only as an unsafe migration escape hatch for legacy jobs.

The SDK exposes no quote-revoke operation. Historical ERC-1271 verification requires an RPC that retains state for the funding block. Because an off-chain `eth_call` sees end-of-block state, exact funding-versus-revocation ordering inside one block remains an acknowledged limitation while the Commerce contract is unchanged. Strict irrevocability from the instant of off-chain signing would instead require an on-chain quote attestation; `signOrder` alone cannot provide it.

## Fees (field-measured, oracle-priced)

| action | protocol fee | gas |
| --- | --- | --- |
| register admin key (auto, first execute ever) | ~$0.50 in native BNB | self-paid via relay |
| register session key (each `grantSession`) | ~$0.50 in native BNB | self-paid |
| ephemeral session (`grantSession({ register: false })`, pinned SDK 0.7.1) | 0 - no registry entry, so no fee; invisible to `verify_authorization` | self-paid |
| `registerSessionKey` (upgrade an ephemeral key later; idempotent) | ~$0.50 in native BNB (0 if already registered) | self-paid |
| every `execute` | 0 | self-paid |
| `revokeSession` / registry reads | 0 | gas / free |

"Gasless" means the relay **fronts** gas and recovers it from the wallet in the same transaction - nothing is sponsored today, and MegaFuel does not participate in Altana-routed transactions.

## x402 from the session key (`x402.pay`, pinned SDK 0.7.1)

Raw x402 signatures used to be a dead end here (confirmed on-chain: `ecrecover` yields the session key, and the Porto account's ERC-1271 rejects raw digests). The pinned `@altananetwork/sdk` 0.7.1 release provides the supported path around both: `signX402Payment` produces an ERC-7739-nested ERC-1271 envelope, and `isValidSignature` answers only callers whitelisted via `approveSignatureChecker` - so the old dual-account workaround (separate x402 EOA) is retired. One-time setup, then the agent pays with the session alone:

```ts
// admin, once per session
const approved = await admin.approveX402SignatureChecker(session); // checker = Permit2
const checkerBlock = approved.blockNumber; // persist next to the session file
await admin.setPermit2Allowance(USDC, 50_000_000n, {
  // BOUNDED - see below; locks down earlier sessions' stored allowances
  permit2Cleanup: { client: archiveClient },
});

// agent (session mode)
const payer = wallet.makeX402Payer({ sessionBudget: { [USDC]: 10_000_000n } });
const result = await payer.request(url, { maxPayment: 1_000_000n });
```

Security model: session **spend caps do not apply to Permit2 pulls** (the checker gate is independent of spend permissions), so the wallet's outer approvals to Permit2 are the only on-chain ceiling on what a leaked session key can pull, **all of them**: as a Permit2 signature checker the session can sign for any token the wallet approved to Permit2, not only the x402 token. Keep the x402 allowance bounded (`setPermit2Allowance`, sized like the old dedicated-EOA balance), use a wallet with no other Permit2 approvals (an EOA that used Uniswap usually has some; set them to 0 first), and layer the in-process caps (`maxPayment`, `sessionBudget`) on top. Kill switches: `revokeX402SignatureChecker` (x402 only) or `revokeSession` (everything).

A revoke does not remove a Permit2 allowance the session already signed: as a Permit2 signature checker the session can sign a `PermitSingle`, and once submitted that allowance lives in Permit2 under the wallet, independent of the session and its expiry. It pulls only through the wallet's outer `approve(Permit2, …)`, so both revokes are followed by a batch that sets that approval to 0 for every catalog token that has one (`result.permit2Reset`; `permit2Reset: false` skips it, since it also pauses x402 for the wallet's other sessions). The session can sign such an allowance for any token, but only the tokens in `result.permit2Reset.covered` are reset: if the wallet approved other tokens to Permit2 (an EOA that used Uniswap usually has), list them in `permit2Reset: { extraTokens }`, or pass `permit2Cleanup`, whose scan also resets the tokens it finds. Raising the approval again re-arms such allowances, which is why a non-zero `setPermit2Allowance` needs `permit2Cleanup` (lockdown and approval in one batch, scanning from the wallet's first EIP-7702 delegation, which the call finds itself; `client` must be on an RPC serving historical state, `eth_getProof` and logs) or `skipPermit2Cleanup: true` (no session of the wallet was ever a Permit2 checker). To lock down at revoke time as well:

```ts
await admin.revokeX402SignatureChecker(session, {
  // scan Permit / Approval events from here, lock down what is still live
  permit2Cleanup: { fromBlock: checkerBlock, client: archiveClient },
});
// or, separately:
const live = await admin.listPermit2Allowances({ fromBlock: checkerBlock });
await admin.lockdownPermit2Allowances(live);
```

`lockdownPermit2Allowances` calls Permit2 `lockdown` (and, optionally, `approve(Permit2, 0)`) in one relay batch; a locked-down allowance stays zero when Permit2 is approved again. The scan covers every stored Permit2 allowance of the wallet, including ones the admin set on purpose: pass their spenders as `permit2Cleanup.keepSpenders` (or filter the list yourself) if the wallet uses Permit2 elsewhere; a token that still has a kept allowance keeps its outer approval. The revoke-time and re-arm scans fail, instead of reporting nothing found, when their range does not show a Permit2 checker approval (a late `fromBlock`, or an RPC that served no logs). The default public BSC RPC serves `eth_getLogs` only for roughly the last hour of blocks; pass `client` (a viem `PublicClient` on an archive-capable RPC that returns an error, not an empty result, for a range it does not keep) and `chunkSize` as needed. Session expiry runs no code: retire a session by revoking it, so its outer approvals are reset.

x402 is also the textbook fit for **ephemeral sessions** in the pinned 0.7.1 release: the facilitator verifies signatures through the account's ERC-1271, never the KeyStore registry, so `grantSession({ register: false })` buys the same payment power without the registration fee.

**Verification runbook (supported and tested with 0.7.1):**

1. `pnpm add -D -E @altananetwork/sdk@0.7.1`, then add the x402 mirrors to `tests/altanaTypeCompat.test.ts` (see the note in `src/wallets/altana/types.ts`) - `pnpm check` arbitrates every assumed shape.
2. `pnpm exec tsx examples/altana/x402.ts` (mainnet, dust amounts; env: `PRIVATE_KEY`, `X402_ENDPOINT`, optional `X402_MAX_PAYMENT`) - runs setup + one paid request end-to-end.
3. Receiving is unchanged: point `payTo` at the Altana wallet.

## Testnet: the official stack

The pinned `@altananetwork/sdk` 0.7.1 release ships the official BSC-testnet deployment as the `BNB_TESTNET` export - in this SDK, just set `network: "bnb-testnet"` on the provider. The full lifecycle is E2E-verified against it (12/12, 2026-07-15). Version 0.7.1 uses the current `testnet-relay.altana.network` relay. (The legacy functor stack and its `getKeys→getActiveKeys` RPC shim are retired; `testnet.ts` now only carries shared constants.)

## Running the E2E

Spends real testnet funds (~$0.50-equiv tBNB per run for the session registration + dust gas; step 9 escrows 0.1 testnet U when available).

```bash
# typescript/.env  (gitignored - never commit)
PRIVATE_KEY=0x...   # funded BSC-testnet key, DEDICATED to testing
ALTANA_E2E=1

pnpm -C typescript run e2e:altana
```

12 steps: admin provider → 7702 bootstrap (idempotent) → bounded Commerce allowance + grantSession → byte-exact serde round-trip → session provider → ERC-8004 register → ERC-8183 createJob → cancelOpen → allowance-checked fund (skipped if U < 0.1) → revoke + negative verification → fee accounting → cleanup. Exit 0 only when everything passes.
