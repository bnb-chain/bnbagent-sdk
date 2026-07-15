# Altana wallet provider — examples & testnet E2E

[Altana](https://docs.altana.network) (formerly Functor) is non-custodial
agentic-wallet infrastructure on EIP-7702: your EOA **is** the wallet, an
admin key grants **session keys** whose call whitelist + spend caps +
expiry are enforced by the on-chain account validator, and authorization
state lives in a public KeyStore registry anyone can verify with one free
`eth_call`. `AltanaWalletProvider` plugs that model into this SDK's wallet
seam — the ERC-8004/8183 protocol clients run over it **unchanged**.

## Install

The Altana SDK is an **optional peer dependency** (GPL-3.0-or-later,
ESM-only). Nothing GPL ships inside `@bnb-chain/bnbagent`; the provider
lazily `import()`s it on first backend use:

```bash
pnpm add @altananetwork/sdk        # only if you use AltanaWalletProvider
```

## Two modes

```ts
import {
  AltanaWalletProvider,
  defaultAgentPermissions,
  serializeSession,
} from "@bnb-chain/bnbagent/wallets";

// ADMIN (holds the wallet's EOA key; grants/revokes sessions) —
// run this wherever the admin key lives, NOT in the agent process.
const admin = new AltanaWalletProvider({ privateKey: process.env.PRIVATE_KEY! });
const session = await admin.grantSession({
  permissions: defaultAgentPermissions({
    chainId: 56,
    tokenSpend: { limit: 10n ** 18n }, // 1 token / day
  }),
  expiry: Math.floor(Date.now() / 1000) + 86_400,
});
// Persist BYTE-EXACTLY (the chain hash-commits the granted bytes):
fs.writeFileSync(".session.json", serializeSession(session), { mode: 0o600 });

// AGENT (session mode; executes only inside the grant)
const wallet = await AltanaWalletProvider.sessionFromEnv(); // ALTANA_SESSION / ALTANA_SESSION_FILE
const jobs = await ERC8183Client.create({ walletProvider: wallet, network: "bsc-mainnet" });
```

`defaultAgentPermissions` whitelists the five protocol targets (registry,
commerce, router, policy, payment token) and **always** includes a small
native spend cap — a session pays its own relay-recovered gas, and a grant
without a native allowance reverts `NoSpendPermissions` on-chain
(field-tested).

Session persistence env vars:

| var | meaning |
|---|---|
| `ALTANA_SESSION` | the serialized session JSON itself (wins when both set) |
| `ALTANA_SESSION_FILE` | path to a file holding it |

The serialized payload **contains the session private key** — mode 0600,
never commit, never log. Its blast radius is capped by the on-chain grant.

## Fees (field-measured, oracle-priced)

| action | protocol fee | gas |
|---|---|---|
| register admin key (auto, first execute ever) | ~$0.50 in native BNB | self-paid via relay |
| register session key (each `grantSession`) | ~$0.50 in native BNB | self-paid |
| every `execute` | 0 | self-paid |
| `revokeSession` / registry reads | 0 | gas / free |

"Gasless" means the relay **fronts** gas and recovers it from the wallet
in the same transaction — nothing is sponsored today, and MegaFuel does
not participate in Altana-routed transactions.

## Why there is no `x402.pay` capability

x402/EIP-3009 signatures are verified by the **token contract**, outside
the wallet's `execute()` path: `ecrecover` on a session signature yields
the session key (not the wallet), and the Porto account's ERC-1271
rejects all raw-digest signatures by anti-replay design. Both dead ends
were confirmed on-chain. So `makeX402Payer()` throws with guidance:

- **Paying**: use a separate dedicated low-balance EOA
  (`EVMWalletProvider` + `X402Signer`), the same pattern as before.
- **Receiving**: works perfectly — point `payTo` at the Altana wallet and
  income lands under the strongest custody you have.

## The testnet shim (`shim.ts` / `testnet.ts`)

The official Altana stack has **no testnet today**: the SDK exports
mainnets only and relay.altana.network does not serve chain 97. The
pre-rename Functor deployment on 97 still works (old contracts + old
`relay.functor.sh`) with exactly one ABI drift — the new SDK calls
`getKeys(address)` where the legacy KeyStore has `getActiveKeys(address)`
(same signature/return, different selector). `startGetKeysShim` runs a
localhost RPC proxy translating that single selector; everything else
forwards verbatim. Legacy infrastructure may disappear — these files are
deliberately outside `src/` and outside the npm package. Mainnet needs no
shim.

## Running the E2E

Spends real testnet funds (~$0.50-equiv tBNB per run for the session
registration + dust gas; step 9 escrows 0.1 testnet U when available).

```bash
# typescript/.env  (gitignored — never commit)
PRIVATE_KEY=0x...   # funded BSC-testnet key, DEDICATED to testing
ALTANA_E2E=1

pnpm -C typescript run e2e:altana
```

12 steps: admin provider → 7702 bootstrap (idempotent) → grantSession
(default agent permissions) → byte-exact serde round-trip → session
provider → ERC-8004 register → ERC-8183 createJob → cancelOpen →
bundled approve+fund (skipped if U < 0.1) → revoke + negative
verification → fee accounting → cleanup. Exit 0 only when everything
passes.
