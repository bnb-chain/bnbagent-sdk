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

Admin mode can also load the existing BNBAgent Keystore V3 format with `AltanaWalletProvider.adminFromKeystore(...)`. Session mode reads `ALTANA_SESSION` first, then `ALTANA_SESSION_FILE`.

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

`defaultAgentPermissions(...)` creates the normal BNBAgent grant. It allows selector-bound calls to the ERC-8004 registry and ERC-8183 Commerce, Router, and Policy contracts. The payment token is present only in the spend cap; the session receives no ERC-20 `approve` permission because an allowance created by a leaked key would survive session expiry/revocation.

多币种买家应使用 `tokenSpends`为 catalog 中的每个 checksum token 分别配置上限；旧 `tokenSpend` 仍表示默认 U 的单币种上限，两者不能同时传入。Buyer 权限只新增精确的 `createJobWithToken(address,address,uint256,string,address,address)` selector，不会开放 Commerce target-wide 权限，也不会给 session ERC-20 `approve` 权限。

```ts
permissions: defaultAgentPermissions({
  chainId: 56,
  tokenSpends: [
    { token: u.address, limit: uBudget },
    { token: usdc.address, limit: usdcBudget },
    { token: usdt.address, limit: usdtBudget },
  ],
})
```

The native allowance is required. A session without native spend permission cannot pay the relay-recovered gas and fails on-chain with `NoSpendPermissions`.

Custom contract deployments are supported because Altana executes the intent's pre-encoded call. A session must include every custom target in its call permissions, and the BNBAgent protocol configuration must point to the same addresses.

`grantSession({ register: false })` creates an ephemeral session without a KeyStore registry entry. Its permissions and expiry are still enforced by the wallet, but third parties cannot discover or verify the key through the registry. `registerSessionKey(session)` can register it later.

## ERC-8004 and ERC-8183

The provider supports all write intents currently emitted by the TypeScript SDK:

| Protocol | Intents |
| --- | --- |
| ERC-8004 | `register`, `set_metadata`, `set_agent_uri` |
| ERC-8183 Commerce | `create_job`, `create_job_with_token`, `set_provider`, `set_budget`, `fund`, `submit`, `complete`, `reject`, `claim_refund` |
| ERC-8183 Router | `register_job`, `settle`, `mark_expired` |
| ERC-8183 Policy | `dispute`, `vote_reject` |

Reads continue through the SDK's public RPC client. Writes are encoded by the protocol client and submitted through the Altana relay. Before handing the session to the agent, the admin must provision a bounded Commerce allowance no larger than the session token cap:

```ts
await admin.setErc8183Allowance(paymentToken, commerceAddress, sessionTokenCap);
```

每个 token 都需由 admin 对同一 Commerce 单独设置 bounded allowance；`erc8183.fund` 使用链上 job token 检查对应 allowance，然后只 relay Commerce `fund` 调用。session 撤销时应将这些 allowance 逐个清零。Altana session 的 B402 Permit2 路径保持原有的 delegated 安全边界；本次不为 evm-local/Turnkey 开放 Permit2 raw typed-data 签名。

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
await admin.approveX402SignatureChecker(session);
await admin.setPermit2Allowance(token, allowance);
```

The session then creates a payer with `makeX402Payer()`. Each request is pinned to the provider's chain, enforces `maxPayment`, and can also restrict the asset with `expectedAsset` and total in-process spending with `sessionBudget`.

Altana session spend limits do not cover Permit2 token pulls. The bounded allowance set by `setPermit2Allowance` is the on-chain ceiling for x402 spending; `maxPayment` and `sessionBudget` are additional in-process limits. `revokeX402SignatureChecker` disables x402 for the session, while `revokeSession` disables every session operation.

An Altana wallet can receive x402 payments without session setup. Point the challenge's `payTo` address at the wallet.

## Other provider operations

| Method | Mode | Purpose |
| --- | --- | --- |
| `grantSession` | Admin | Grant a registered or ephemeral session |
| `registerSessionKey` | Admin | Add an ephemeral session key to the KeyStore registry |
| `revokeSession` | Admin | Revoke all authority for a session |
| `approveQuoteSignatureChecker` | Admin | Allow ERC-8183 quote verification for a session |
| `approveX402SignatureChecker` | Admin | Allow x402 ERC-1271 verification for a session |
| `revokeX402SignatureChecker` | Admin | Disable the x402 checker without revoking the session |
| `setPermit2Allowance` | Admin | Set the bounded token allowance used by x402 |
| `balances` | Admin or session | Read native and optional ERC-20 balances |

## Current boundaries

- The provider is TypeScript-only.
- Passkey admin signers are not supported because the provider requires a synchronous wallet address. Use a private-key or injected EOA signer, or drive passkey flows through the Altana SDK directly.
- The relay fronts gas and recovers it from the wallet. This is not gas sponsorship, and MegaFuel is not used for Altana transactions.
- Session files contain private key material even though on-chain permissions limit their authority.
- Admin mode has full wallet authority. Keep the admin key outside the agent process and give the agent only a scoped session.

For runnable setup, fee notes, quote signing, x402, and the BSC testnet E2E, see [`typescript/examples/altana/`](../typescript/examples/altana/README.md).
