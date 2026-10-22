# Altana wallet - capability reference

This document describes what `AltanaWalletProvider` supports through the TypeScript SDK for ERC-8004 identity, ERC-8183 commerce, and x402 payments.

[Altana](https://docs.altana.network) provides non-custodial agent wallets based on EIP-7702. The wallet keeps the admin EOA address, while the admin grants session keys with an on-chain call whitelist, spending limits, and expiry. `AltanaWalletProvider` sends calls through the Altana relay, which broadcasts the transaction and recovers the gas cost from the wallet.

Altana support is currently available only in the TypeScript SDK.

## Install

`@altananetwork/sdk` is an optional peer dependency. It is ESM-only and licensed GPL-3.0-or-later. The provider loads it only when an Altana operation runs.

```bash
pnpm add @altananetwork/sdk
```

The supported and tested release is exactly `0.7.1`:

```bash
pnpm add @altananetwork/sdk@0.7.1
```

The repository pins that version in its lockfile, checks the mirrored vendor
types during `pnpm typecheck`, and validates the required runtime exports when
the optional module is loaded. Treat every vendor-version change as a security
change: review its source and changelog, regenerate the lockfile, then run the
Altana type-compatibility, unit, and testnet E2E suites before changing the pin.

## Admin and session modes

An Altana provider has exactly one mode:

- Admin mode holds the wallet's EOA key or signer. It can execute calls, grant and revoke sessions, register an ephemeral session key, and configure quote or x402 signature checkers.
- Session mode holds only a scoped session key. It can execute calls allowed by the session grant, sign ERC-8183 quotes through a narrow signer, and make x402 payments.

```ts
import { writeFileSync } from "node:fs";
import {
  AltanaWalletProvider,
  defaultAgentPermissions,
  serializeSession,
} from "@bnbagent/sdk/wallets";

const admin = new AltanaWalletProvider({
  privateKey: process.env.PRIVATE_KEY!,
  network: "bnb-mainnet",
});

const session = await admin.grantSession({
  permissions: defaultAgentPermissions({
    chainId: 56,
    tokenSpend: { limit: 10n ** 18n },
  }),
  expiry: Math.floor(Date.now() / 1000) + 86_400,
});

writeFileSync(".session.json", serializeSession(session), { mode: 0o600 });

// In the agent process, with ALTANA_SESSION_FILE=.session.json
const wallet = await AltanaWalletProvider.sessionFromEnv();
```

Admin mode can also load the existing BNBAgent Keystore V3 format with `AltanaWalletProvider.adminFromKeystore(...)`. Like `EVMWalletProvider`, it creates and saves a new key when the keystore directory is empty and no `address` is given, and logs a warning when it does; in production pass the admin `address` or `create: false`, so a wrong directory fails instead of making a fresh key the admin. Session mode reads `ALTANA_SESSION` first, then `ALTANA_SESSION_FILE`.

The serialized session contains its private key. Store it with mode `0600`, and never commit or log it. Use `serializeSession` and `deserializeSession`; the on-chain account checks the granted session data byte for byte.

## Capability model

`AltanaWalletProvider.capabilities()` returns:

| Capability | Admin mode | Session mode | Backed by |
| --- | --- | --- | --- |
| `broadcast.self` | Yes | Yes | Altana relay execution |
| `calls.arbitrary` | Yes | Within the session grant | Pre-encoded contract calls sent through the relay |
| `intents.erc8004` | Yes | Within the session grant | ERC-8004 calls through `AltanaIntentExecutor` |
| `intents.erc8183` | Yes | Within the session grant | ERC-8183 calls through `AltanaIntentExecutor` |
| `x402.pay` | No | Yes | `AltanaX402Payer` and the session key |

The provider does not expose `sign.message`, `sign.transaction`, or `sign.typed_data`. Altana execution is relay-shaped rather than raw-signer-shaped. Use `EVMWalletProvider` when an application needs generic signing.

`sessionQuoteSigner()` is a separate, narrow interface for ERC-8183 negotiation quotes. It does not add a generic signing capability to the agent.

## Session permissions

`defaultAgentPermissions(...)` creates the normal BNBAgent grant. It allows selector-bound calls to the ERC-8004 registry and ERC-8183 Commerce, Router, and Policy contracts. The payment token is present only in the spend cap; the session receives no ERC-20 `approve` permission because an allowance created by a leaked key would survive session expiry/revocation. Extra rules passed as `extraCalls` must name both `to` and `signature`, and cannot target catalog payment tokens or the registry's ERC-721 approval and transfer functions (`approve`, `setApprovalForAll`, `transferFrom`, `safeTransferFrom`), for the same reason. They also cannot use Porto's wildcard values (`to` = `0x3232…3232`, any contract; `signature` = `0x32323232`, any function), which would include those targets and functions, and `signature` must be a function signature or an exact 4-byte selector: Porto sends any other `0x`-prefixed value to the relay as the selector unchecked. `grantSession` applies the same checks to every call permission, however the set was built; Porto would turn a call without `to` or `signature` into a wildcard. Both paths refuse every call to a catalog payment token, including a plain `transfer`; if an agent needs to transfer tokens directly from a session, open an issue.

These checks only shape new sessions. An ERC-721 approval already written on the identity Registry lives there under the wallet, and revoking or expiring the session that wrote it does not clear it. Sessions granted with `defaultAgentPermissions` from `@bnbagent/sdk` 0.5.0 through 0.5.3 (the Registry without a function restriction), or with a wildcard `extraCalls` entry, could write one. For such wallets, do a one-time check: list the Registry's `ApprovalForAll` and `Approval` events with the wallet as `owner`, confirm what is still live with `isApprovedForAll(wallet, operator)` and `getApproved(agentId)`, and clear anything unexpected from the admin with the Altana SDK's `execute` (`setApprovalForAll(operator, false)`, `approve(0x0000000000000000000000000000000000000000, agentId)`).

The native allowance is required. A session without native spend permission cannot pay the relay-recovered gas and fails on-chain with `NoSpendPermissions`.

Custom contract deployments are supported because Altana executes the intent's pre-encoded call. A session must include every custom target in its call permissions, and the BNBAgent protocol configuration must point to the same addresses.

`grantSession({ register: false })` creates an ephemeral session without a KeyStore registry entry. Its permissions and expiry are still enforced by the wallet, but third parties cannot discover or verify the key through the registry. `registerSessionKey(session)` can register it later.

## ERC-8004 and ERC-8183

The provider supports all write intents currently emitted by the TypeScript SDK:

| Protocol | Intents |
| --- | --- |
| ERC-8004 | `register`, `set_metadata`, `set_agent_uri` |
| ERC-8183 Commerce | `create_job`, `set_provider`, `set_budget`, `fund`, `submit`, `complete`, `reject`, `claim_refund` |
| ERC-8183 Router | `register_job`, `settle`, `mark_expired` |
| ERC-8183 Policy | `dispute`, `vote_reject` |

Reads continue through the SDK's public RPC client. Writes are encoded by the protocol client and submitted through the Altana relay. Before handing the session to the agent, the admin must provision a bounded Commerce allowance no larger than the session token cap:

```ts
await admin.setErc8183Allowance(paymentToken, commerceAddress, sessionTokenCap);
```

`erc8183.fund` checks that allowance and relays only the Commerce `fund` call. Zero the allowance from the admin provider when revoking the session.

## ERC-8183 quote signing

Session mode can sign negotiation quotes with `sessionQuoteSigner()`. The method returns an ERC-1271 signature envelope for the Altana wallet without exposing the admin key or generic message signing to the agent.

The admin must first allow the ERC-8183 Commerce verifier to check that session:

```ts
await admin.approveQuoteSignatureChecker(session, commerceAddress);
```

Quote expiry is limited by the session expiry. Provider-side job verification checks the signed quote at the indexed `JobFunded` block, so later session expiry or revocation does not change the funding-time result. See the [Altana quote example](../typescript/examples/altana/README.md#erc-8183-quotes-from-the-session-key) for the complete flow.

## x402 payments

Only session mode exposes `x402.pay`. The admin performs two setup operations for each session before the agent pays:

```ts
const approved = await admin.approveX402SignatureChecker(session);
await admin.setPermit2Allowance(token, allowance, {
  // see "Re-arm" below; skipPermit2Cleanup: true for a wallet whose
  // sessions were never Permit2 signature checkers
  permit2Cleanup: { client: archiveClient },
});
```

The session then creates a payer with `makeX402Payer()`. Each request is pinned to the provider's chain, enforces `maxPayment`, and can also restrict the asset with `expectedAsset` and total in-process spending with `sessionBudget`.

Altana session spend limits do not cover Permit2 token pulls. The wallet's outer `approve(Permit2, …)` approvals are the only on-chain ceiling, and that means all of them, not only the one `setPermit2Allowance` sets: a session approved as a Permit2 signature checker can sign a Permit2 allowance for any token the wallet approved to Permit2. An EIP-7702 wallet is the owner's EOA, and earlier DEX use often left tokens approved to Permit2 without a limit, so use a wallet with no other Permit2 approvals as the Altana agent wallet, or set them to 0 first. `maxPayment` and `sessionBudget` are additional in-process limits. `revokeX402SignatureChecker` disables x402 for the session, while `revokeSession` disables every session operation.

A session approved as a Permit2 signature checker can also sign a `PermitSingle`, for any token. Once submitted, that allowance is stored in Permit2 under the wallet and survives the checker revoke, the session revoke and the session's expiry (SRC-1609). It can only pull while the wallet's outer `approve(Permit2, …)` for that token is non-zero, so the provider handles it in two places:

- **Revoke.** `revokeX402SignatureChecker` and `revokeSession` are followed by one batch that sets the outer Permit2 approval to 0 (`permit2Reset`):
  - It covers the chain's catalog tokens, `permit2Reset.extraTokens`, and with `permit2Cleanup` the tokens of every allowance the scan found. The result lists the tokens read in `permit2Reset.covered` and those reset in `permit2Reset.tokens`.
  - A token outside `covered` is not touched. If the wallet approved other tokens to Permit2 (an EOA that used Uniswap usually has), list them in `extraTokens` or pass `permit2Cleanup`.
  - It reads only current approvals, so it works on the default RPC.
  - It also pauses x402 for every other session of the wallet, and any other Permit2 use of those tokens, until the approval is raised again. `permit2Reset: false` skips it.
  - If the batch fails, the error says that the revoke went through.
- **Re-arm.** A non-zero `setPermit2Allowance` re-arms every stored allowance of the token, so it needs one of two options, or nothing is submitted:
  - `permit2Cleanup: { client, fromBlock?, keepSpenders?, chunkSize? }` scans the token's stored allowances and submits their `lockdown` and the approval in one batch.
  - The scan starts at the wallet's first EIP-7702 delegation, which the call finds itself (`findDelegationBlock`), so it covers every session the wallet ever approved. An explicit `fromBlock` must not be later: the call reads the wallet's code and storage root (`eth_getProof`) at `fromBlock - 1` and refuses if either is non-empty. A delegation that was cleared and set again keeps the account's storage, so it is caught too.
  - Pass `client` on an RPC that serves historical state, `eth_getProof` and the logs since the delegation; the networks' default public RPC does not, and then nothing is submitted. The RPC must return an error, not an empty result, for a range it cannot serve: one that silently drops old logs hides their allowances from the scan. The scan must also show a Permit2 signature-checker approval of some session of the wallet.
  - `skipPermit2Cleanup: true` is for a wallet whose sessions were never Permit2 signature checkers. Setting the approval to 0 needs neither.

`approveX402SignatureChecker` always returns `blockNumber`: its receipt's block, or the block read just before submitting when the receipt cannot be read (it submits nothing if that first read fails).

Either revoke also accepts `permit2Cleanup: { fromBlock, tokens?, keepSpenders?, client?, chunkSize? }`, which scans Permit2 `Permit` / `Approval` events from `fromBlock` and locks down every allowance that is still non-zero in the same batch as the reset; `listPermit2Allowances` and `lockdownPermit2Allowances` do the same separately. The scan includes allowances the admin set on purpose; list their spenders in `keepSpenders` (reported back as `kept`) if the wallet uses Permit2 elsewhere. A token that still has a kept allowance keeps its outer Permit2 approval. The revoke-time cleanup checks that the scanned range shows the account's `SignatureCheckerApprovalSet` approving Permit2 for this session; when it does not, because `fromBlock` is later than the approval or the RPC served no logs, the cleanup fails instead of reporting that nothing was found. `listPermit2Allowances` has no such check: an empty list proves nothing unless its range covers the approval block on an RPC that served the logs. The networks' default public RPC serves `eth_getLogs` only for roughly the last hour of blocks, so pass `client` on an archive-capable RPC for the cleanup and for re-arming, one that returns an error rather than an empty result for a range it does not keep.

What this does not cover: session expiry runs no code, so the stored allowances of an expired session that was never revoked keep pulling up to the outer approval. Retire sessions by revoking them, not by letting them expire. Before it is revoked, a leaked session can also pull up to the wallet's outer Permit2 approval of every token, so keep `setPermit2Allowance` small and leave no other token approved to Permit2. Stopping sessions from signing standing Permit2 allowances at all needs a change in the Altana account.

An Altana wallet can receive x402 payments without session setup. Point the challenge's `payTo` address at the wallet.

## Other provider operations

| Method | Mode | Purpose |
| --- | --- | --- |
| `grantSession` | Admin | Grant a registered or ephemeral session |
| `registerSessionKey` | Admin | Add an ephemeral session key to the KeyStore registry |
| `revokeSession` | Admin | Revoke all authority for a session, then reset outer Permit2 approvals |
| `approveQuoteSignatureChecker` | Admin | Allow ERC-8183 quote verification for a session |
| `approveX402SignatureChecker` | Admin | Allow x402 ERC-1271 verification for a session |
| `revokeX402SignatureChecker` | Admin | Disable the x402 checker without revoking the session, then reset outer Permit2 approvals |
| `setPermit2Allowance` | Admin | Set the bounded token allowance used by x402 (a non-zero amount needs `permit2Cleanup` or `skipPermit2Cleanup`) |
| `listPermit2Allowances` | Admin or session | List the wallet's stored Permit2 allowances that are still non-zero |
| `lockdownPermit2Allowances` | Admin | Zero stored Permit2 allowances with Permit2 `lockdown` |
| `balances` | Admin or session | Read native and optional ERC-20 balances |

## Current boundaries

- The provider is TypeScript-only.
- Passkey admin signers are not supported because the provider requires a synchronous wallet address. Use a private-key or injected EOA signer, or drive passkey flows through the Altana SDK directly.
- Passkey (WebAuthn/P256) sessions must be revoked with this provider's `revokeSession` (the version that includes this fix). The Altana SDK's own `revokeSession` (0.5.1 through at least 0.9.0), and earlier versions of this SDK that call it, revoke every session as a secp256k1 key and cannot remove a passkey session: the account rejects the revoke with `KeyDoesNotExist`. This provider submits one relay batch with the account's `revoke` under the WebAuthn-P256 key hash and, when the key is registered, the KeyStore `revokeKey`. It recognises a passkey session by its passkey signer or by its 64-byte P256 public key (secp256k1 session keys are 65 bytes with a `0x04` prefix), so a bare public key works too.
- The relay fronts gas and recovers it from the wallet. This is not gas sponsorship, and MegaFuel is not used for Altana transactions.
- Session files contain private key material even though on-chain permissions limit their authority.
- Admin mode has full wallet authority. Keep the admin key outside the agent process and give the agent only a scoped session.

For runnable setup, fee notes, quote signing, x402, and the BSC testnet E2E, see [`typescript/examples/altana/`](../typescript/examples/altana/README.md).
