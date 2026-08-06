# TWAK (Trust Wallet Agent Kit) - capability reference

What the TWAK wallet supports through bnbagent-sdk, method by method, for **ERC-8004** (identity), **ERC-8183** (commerce), and **x402** (payments).

TWAK (Trust Wallet Agent Kit) is a self-custody wallet CLI whose encrypted mnemonic lives under `~/.twak` (password from the OS keychain or `TWAK_WALLET_PASSWORD`, never on argv). The SDK drives it through `TWAKProvider`, which shells out to a stateless `twak … --json` subprocess. It is **self-broadcasting**: `TWAKProvider.make_executor()` returns the provider itself, so every high-level operation is built, signed, and broadcast inside twak (the SDK holds no key and sends no transaction).

- **Minimum twak version: v0.20.0.** (The SDK forwards `--paymaster-url` on every write whenever a paymaster is configured - the default `bsc-testnet` config is - and the flag first shipped in v0.20.0. Without a paymaster v0.19.1 still works; v0.19.0 signed `0x`-shaped messages over the wrong bytes; older CLIs lack the v0.19.0 command surface.)
- **Networks:** `bsc` (mainnet) and `bsctestnet`. The SDK network name `bsc-testnet` maps to the CLI key `bsctestnet` via `bnbagent.wallets.TWAK_CHAIN_FOR_NETWORK`.
- **Construct (Python):** `TWAKProvider(chain="bsc")`, or `WALLET_KIND=twak` on any `AgentConfig` / `*Config.from_env()` (chain auto-pinned to the network).
- **Construct (TypeScript):** `new TWAKProvider({ chain: "bsc" })`, or `walletKind: "twak"` / `WALLET_KIND=twak` on `ERC8183Config` (chain auto-pinned). twak is an npm CLI - install `@trustwallet/cli` as a project dependency and the provider resolves `node_modules/.bin/twak` automatically.

## Capability model

`TWAKProvider.capabilities()` returns:

| Capability | Backed by |
| --- | --- |
| `sign.message` | `twak wallet sign-message` (EIP-191) |
| `broadcast.self` | self-broadcasting executor (CLI → RPC) |
| `intents.erc8004` | the 3 ERC-8004 write intents below, on the canonical registry or matching `ERC8004_REGISTRY_ADDRESS` override |
| `intents.erc8183` | the 13 ERC-8183 write intents below, on the canonical contract stack |
| `x402.pay` | the delegated `TwakX402Payer` (`make_x402_payer()`) |

**Not supported:** `sign.transaction` and `sign.typed_data` - twak exposes no raw-transaction or generic EIP-712 primitive (it signs ERC-8004/8183/x402 payloads internally via its own commands). Calls raise `UnsupportedWalletOperation`; use `WALLET_KIND=evm` when you need them.

For canonical deployments, callers never special-case the wallet kind. `ERC8183Client` / `ERC8004Agent` route every write through `wallet.make_executor()`, and x402 goes through `wallet.make_x402_payer()` - so swapping EVM ↔ twak is a construction-time choice with no flow changes. `fund_bundles_approval = True`: twak's `erc8183 fund` does `approve` + `deposit` itself, so the SDK skips its own allowance top-up.

twak v0.20.0 honors `ERC8004_REGISTRY_ADDRESS`; `TWAKProvider` allows a custom ERC-8004 intent only when its target matches that environment override. It also rejects a canonical intent when a stale override would redirect it. ERC-8183 has no equivalent override: non-canonical Commerce, Router, or Policy intents fail before the wallet is probed or the CLI is invoked. Use an EVM wallet for custom ERC-8183 contracts until twak exposes an explicit target option.

## ERC-8004 - identity registry

Three write intents dispatch to twak; **reads** (`get-metadata`, `show`) are served by the SDK over its own RPC, not through twak.

| SDK intent | twak command | Solidity |
| --- | --- | --- |
| `erc8004.register` | `erc8004 register --uri <url> [--metadata k=v]…` | `register(string agentURI, MetadataEntry[] metadata) → uint256 agentId` |
| `erc8004.set_metadata` | `erc8004 set-metadata <id> --key <k> --value <hex\|str>` | `setMetadata(uint256 agentId, string key, bytes value)` |
| `erc8004.set_agent_uri` | `erc8004 set-uri <id> --uri <url>` | `setAgentURI(uint256 agentId, string newURI)` |

`--uri` accepts `https://`, `ipfs://`, or inline `data:`. `--metadata` is repeatable and **atomic with the mint** (all entries ride the register tx). `--value` is taken as hex when `0x…`, else UTF-8.

## ERC-8183 - agentic commerce

Lifecycle (a **client** funds, a **provider** delivers, disputes resolve through the Router-routed policy):

```
create-job → set-budget → register-job → fund → submit → settle
```

Constraints: (1) `register-job` must precede `fund` or the Router-as-hook reverts `PolicyNotSet()`; (2) `--evaluator` and `--hook` must both be the Router (`RouterNotEvaluator()` / `HookRequired()`); (3) `submit` requires `now ≤ expiredAt − disputeWindow` (`SubmissionTooLate()`) - pick ~30 days.

All 13 write intents dispatch to twak on the canonical contract stack. Every kernel write carries a trailing `bytes optParams`, passed through raw via `--opt-params` (v0.19.0); on `submit` the SDK encodes `{"deliverable_url": …}` there, which the policy re-emits in `JobInitialised` for off-chain evaluators.

| SDK intent | twak command | Solidity |
| --- | --- | --- |
| `erc8183.create_job` | `create-job --provider --evaluator --expires-at --description [--hook]` | `AgenticCommerce.createJob(...) → uint256 jobId` |
| `erc8183.set_provider` | `set-provider <id> --provider [--opt-params]` | `setProvider(uint256, address, bytes)` |
| `erc8183.set_budget` | `set-budget <id> --amount [--opt-params]` | `setBudget(uint256, uint256, bytes)` |
| `erc8183.fund` | `fund <id> --expected-budget [--opt-params]` | ERC-20 `approve` **then** `fund(uint256, uint256 expectedBudget, bytes)` |
| `erc8183.submit` | `submit <id> --deliverable <bytes32> [--opt-params]` | `submit(uint256, bytes32, bytes)` |
| `erc8183.complete` | `complete <id> [--reason <bytes32>] [--opt-params]` | `complete(uint256, bytes32, bytes)` |
| `erc8183.reject` | `reject <id> [--reason <bytes32>] [--opt-params]` | `reject(uint256, bytes32, bytes)` |
| `erc8183.claim_refund` | `claim-refund <id>` | `claimRefund(uint256)` _(permissionless after expiry)_ |
| `erc8183.register_job` | `register-job <id> --policy` | `EvaluatorRouter.registerJob(uint256, address)` _(client)_ |
| `erc8183.settle` | `settle <id> [--evidence <hex>]` | `EvaluatorRouter.settle(uint256, bytes)` _(permissionless)_ |
| `erc8183.mark_expired` | `mark-expired <id>` | `EvaluatorRouter.markExpired(uint256)` _(permissionless)_ |
| `erc8183.dispute` | `dispute <id>` | `OptimisticPolicy.dispute(uint256)` _(client, in window)_ |
| `erc8183.vote_reject` | `vote-reject <id>` | `OptimisticPolicy.voteReject(uint256)` _(whitelisted voter; reject quorum is policy-configured per deployment)_ |

`fund` pins the amount with `--expected-budget` - the contract reverts atomically with `BudgetMismatch()` if the on-chain budget drifted. `--reason` and `--evidence` default to zero / `0x` and are omitted unless set.

**Custom-error selectors** (4-byte; the SDK maps them client-side from the `error` field, which carries `execution reverted: 0x<selector>`):

| Selector | Error | On | Cause |
| --- | --- | --- | --- |
| `0x55c45de1` | `HookRequired()` | create-job | zero `--hook` |
| `0xf7a0748c` | `ExpiryTooShort()` | create-job | `--expires-at` too soon |
| `0xb40b2a0e` | `ExpiryTooLong()` | create-job | `--expires-at` > now + 365d |
| `0x99b0fc87` | `BudgetMismatch()` | fund | budget changed after set-budget |
| `0xec43ea50` | `RouterNotEvaluator()` | register-job | `--evaluator` ≠ Router |
| `0x32d53d69` | `PolicyNotSet()` | fund | fund before register-job |
| `0x15e5dd74` | `SubmissionTooLate()` | submit | past `expiredAt − disputeWindow` |

Job status ordinals (raw `getJob` / event logs): `0 Open · 1 Funded · 2 Submitted · 3 Completed · 4 Rejected · 5 Expired`.

## x402 - micropayments

twak is a full x402 HTTP client: it discovers the 402 challenge, signs the payment authorization **internally** (EIP-3009 `transferWithAuthorization`, gasless, or Permit2 `permit2-exact` on the payment asset), and retries with the payment header. There is no separate signer primitive.

The SDK wraps this as the delegated `TwakX402Payer` (`wallet.make_x402_payer(**kwargs)`):

| twak command | Parameters | Role |
| --- | --- | --- |
| `x402 quote <url>` | `--method`, `--body` | read-only challenge discovery (no wallet, no chain) |
| `x402 request <url>` | `--method`, `--body`, `--max-payment`, `--prefer-network`, `--prefer-method <eip3009\|permit2-exact>`, `--prefer-asset`, `--yes`, `--auto-approve` | discover → pay → return endpoint body |

Because the EIP-712 payload is built, signed, and discarded inside the twak process, `SigningPolicy` cannot run on this path. `TwakX402Payer` enforces an equivalent **five-point precheck** on the quoted terms before paying:

1. `payTo` byte-equals the caller's committed recipient;
2. `asset` equals the expected token (the asset address _is_ the EIP-712 `verifyingContract` for EIP-3009 - the domain allowlist relocated to the quote);
3. `amount ≤ max_payment` (re-enforced below by twak's `--max-payment`);
4. claimed `maxTimeoutSeconds ≤ 3600` (`DEFAULT_MAX_TIMEOUT_SECONDS`);
5. route pinned via `--prefer-network` / `--prefer-asset` (narrows the TOCTOU window where the server could swap terms after the quote).

An optional `SessionBudgetTracker` reserves the quoted amount before the call and rolls back on failure (the CLI surfaces no settlement receipt).

## Current boundaries

- **x402 `request` is mainnet-only** so far - testnet routes are rejected as "no supported route" (`quote` works on both).
- **Custom targets:** ERC-8004 supports a registry override through `ERC8004_REGISTRY_ADDRESS`, and the SDK requires it to match the intent target. Custom ERC-8183 contracts remain unsupported because twak v0.20.0 has no Commerce/Router/Policy target option; those intents fail closed.
- **Paymaster:** twak v0.20.0 added `--paymaster-url <url>` on every erc8004/erc8183 write (the URL is used verbatim as the MegaFuel endpoint, on `bsc` and `bsctestnet` alike). When the SDK context carries a paymaster - `ERC8183Client` builds one on sponsored testnet networks, `ERC8004Agent` per its network config - `TWAKProvider` forwards its URL and twak attempts sponsorship. The paymaster still decides eligibility per sender, target, and method; rejection or RPC failure falls back to self-paid gas. Without an explicit endpoint, twak keeps its own defaults: mainnet uses the Trust gateway, while `bsctestnet` has no default paymaster - pre-fund it with testnet BNB.
- **No generic EIP-712 / raw-tx signing** (`sign.typed_data` / `sign.transaction`) - use `WALLET_KIND=evm` for those.
- **`sign-message`** pins `--chain bsc` (the CLI rejects `bsctestnet` there; EIP-191 is chain-agnostic and the address is identical on both networks) and signs the message as **text** (twak ≥ v0.19.1).
- **No key import/export:** twak mints its own mnemonic; switching an existing identity into twak means a new address + ERC-8004 re-register.

## Deployed contracts

| Standard | Contract | BSC mainnet | BSC testnet |
| --- | --- | --- | --- |
| ERC-8004 | Identity Registry | `0x8004a169fb4a3325136eb29fa0ceb6d2e539a432` | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| ERC-8183 | AgenticCommerce | `0xea4daa3100a767e86fded867729ae7446476eba6` | `0xa206c0517B6371C6638CD9e4a42Cc9f02A33B0DE` |
| ERC-8183 | EvaluatorRouter | `0x51895229e12f9876011789b04f8698af06ccd6da` | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` |
| ERC-8183 | OptimisticPolicy | `0x9c01845705b3078aa2e8cff7520a6376fd766de5` | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` |

Payment token `AgenticCommerce.paymentToken()`: **U** (United Stables, `0xce24439f2d9c6a2289f741120fe202248b666666`, 18 dp) on mainnet; `0xc70b8741b8b07a6d61e54fd4b20f22fa648e5565` on testnet. x402 has no contract of ours - it pays through the asset's own EIP-3009 / Permit2 methods. EVM wallets may override all module-specific contract addresses; TWAK v0.20.0 supports only the ERC-8004 registry override and rejects custom ERC-8183 targets as described above.
