# TypeScript SDK Design

**Date:** 2026-07-06
**Status:** Approved
**Goal:** Add a TypeScript SDK under `typescript/` with full feature parity to
the Python SDK (`python/bnbagent`), excluding the TWAK wallet family.

## Scope

### In scope (parity with Python)

- **Foundation**: error hierarchy, `NetworkConfig`/`NETWORKS`/`resolveNetwork`
  with env overrides, networks deployment registry, `toRaw`/`fromRaw`,
  `SlidingWindowLimiter`, `loadEnv`, multicall, `Paymaster`, nonce management,
  contract base with retry/backoff.
- **Wallets**: `WalletProvider` interface, capability model (auto-derived
  `sign.*` capabilities), intents (`Intent`/`IntentExecutor`/
  `ExecutionContext`), `EVMWalletProvider` (Keystore V3), `LocalExecutor`.
- **Signing policy**: `SigningPolicy` + named type-set checks (EIP-3009,
  PERMIT, PERMIT2).
- **Storage**: `StorageProvider` interface, `LocalStorageProvider`,
  `IPFSStorageProvider` (each owning its `fromEnv()`).
- **Protocols**: `MinimalERC20Client`; ERC-8004 (`ERC8004Agent`,
  `AgentEndpoint` + `.a2a()`/`.mcp()` constructors, agent URI codec);
  x402 (`X402Signer`, `X402Payer` types, `SessionBudgetTracker`);
  ERC-8183 (`CommerceClient`/`RouterClient`/`PolicyClient`, `ERC8183Client`
  facade, `ERC8183Config`, types/schema).
- **Provider primitives**: `ERC8183JobOps`, `fundedJobWatcher`,
  `NegotiationHandler`, `DeliverableManifest`/`JobDescription` schema.
- **Examples**: `client/` (5 lifecycle scripts), `voter/`, `x402/`,
  `security/`.
- **Tests**: port the Python suite module-by-module (~20 modules, TWAK tests
  excluded).

### Out of scope (v1)

- TWAK family: `twak_provider`, `twak_custody`, `TwakX402Payer`, twak
  examples. Interfaces leave room for a later `TWAKProvider` (it is an
  `IntentExecutor` + `WalletProvider`, same as Python).
- MPC provider stub (YAGNI).
- Browser compatibility (Node-first; no design work spent on it).
- Serving surfaces (`agent-server`/`a2a-agent` equivalents) — application
  layer, separate project, consistent with the SDK layering rule.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Node.js ≥ 20 | Same positioning as Python SDK: server-side agent runtime |
| Web3 library | viem | TS-first, strongest type inference from `as const` ABIs, tree-shakeable, ecosystem default |
| Package shape | Single package `@bnb-chain/bnbagent`, subpath exports | Main entry = Tier 1 (mirrors `bnbagent/__init__.py`); `./erc8183`, `./x402`, `./storage`, … = Tier 2 |
| Module format | ESM + CJS dual output via tsup | Maximize consumer compatibility |
| Tooling | pnpm, tsup, vitest, biome | biome plays the role ruff plays on the Python side |
| API shape | Fully async | Idiomatic TS; semantics stay equal to Python's sync methods |
| Amounts | `bigint` + decimal-string parsing | Exact, no float round-trips — parity with Python's `Decimal` |
| ABIs | Codegen from repo-root `abis/*.json` → `typescript/src/abis/*.ts` (`as const`) | Repo-root `abis/` is the single source shared by both SDKs; `as const` gives full viem type inference. CI drift check regenerates and diffs |
| Keystore V3 | Implement with `@noble/hashes` (scrypt) + `@noble/ciphers` (AES-128-CTR) | Already in viem's dependency tree; keystore files interoperate with the Python side |
| Nonce management | Port `NonceManager` (per-account singleton) | Keeps chain re-sync-on-nonce-error semantics; viem's experimental nonceManager doesn't cover it |
| Test mocking | Custom viem transport returning canned responses | Cleaner than Python's monkeypatching; no live chain in CI |

## Architecture

Mirrors the Python layering exactly — arrows point downward, upper layers
depend on lower layers, never the reverse:

```
typescript/
├── package.json          # @bnb-chain/bnbagent
├── src/
│   ├── index.ts          # Tier 1 API
│   ├── config.ts         # NetworkConfig / NETWORKS / resolveNetwork + env overrides
│   ├── errors.ts         # BNBAgentError hierarchy
│   ├── core/             # ContractBase, NonceManager, multicall, Paymaster, loadEnv
│   ├── networks/         # BNB_CHAIN_ADDRESSES deployment registry
│   ├── utils/            # toRaw / fromRaw, SlidingWindowLimiter
│   ├── wallets/          # WalletProvider, capabilities, intents, EVMWalletProvider, LocalExecutor
│   ├── signing/          # SigningPolicy + checks
│   ├── storage/          # StorageProvider, Local, IPFS
│   ├── erc20/            # MinimalERC20Client
│   ├── erc8004/          # agent, models, agentUri, constants
│   ├── erc8183/          # client, commerce, router, policy, jobOps, watcher,
│   │                     #   negotiation, schema, types, config
│   ├── x402/             # X402Signer, X402Payer types, SessionBudgetTracker
│   └── abis/             # generated `as const` modules (do not edit by hand)
├── scripts/abi-codegen.ts
├── tests/                # vitest
└── examples/             # client/ voter/ x402/ security/
```

### Key mappings (web3.py → viem)

- `Web3(HTTPProvider)` → `PublicClient` (reads) + `WalletClient` (writes).
  The split matches the SDK's keyless read path: constructing clients without
  a wallet yields a working read-only client; any signing call throws.
- `ContractClientMixin` → `ContractBase` class holding both clients, the
  nonce manager, and retry with exponential backoff on 429s and nonce
  conflicts (up to 5 retries, nonce errors trigger chain re-sync).
- Chain-id assertion at client init (`w3.eth.chain_id == nc.chain_id`) is
  preserved: wrong RPC → error at construction.

## Invariants (carried over from Python)

- No plaintext secrets in config after construction: config wraps
  `privateKey` into an `EVMWalletProvider` and clears both the key and
  password fields.
- Capability declaration cannot drift from behavior: `sign.*` capabilities
  are derived from method overrides.
- The SDK ships no serving runtime; no HTTP framework in dependencies.
- Storage providers are async (natural in TS — no sync bridge needed).
- Nonce management is a per-account singleton.
- Payment token is fetched at runtime from the Commerce kernel, never
  configured.
- Env override surface is identical: `RPC_URL_BSC_TESTNET` /
  `RPC_URL_BSC_MAINNET` / `RPC_URL`, `ERC8183_COMMERCE_ADDRESS` /
  `ERC8183_ROUTER_ADDRESS` / `ERC8183_POLICY_ADDRESS`,
  `ERC8004_REGISTRY_ADDRESS`; overrides do not apply when a `NetworkConfig`
  object is passed directly.

## Implementation order (layered, bottom-up)

Each layer lands source + its ported tests together; the tree stays green at
every step:

1. **Foundation** — errors, config, networks, utils, core (loadEnv,
   multicall, Paymaster, NonceManager, ContractBase), ABI codegen script.
2. **Wallets + signing** — WalletProvider, capabilities, intents,
   EVMWalletProvider, LocalExecutor, SigningPolicy.
3. **Storage** — StorageProvider, Local, IPFS.
4. **Protocols** — erc20 → erc8004 → x402 → erc8183 (low-level clients →
   `ERC8183Client` facade → `ERC8183Config`).
5. **Provider primitives** — JobOps, fundedJobWatcher, NegotiationHandler,
   schema.
6. **Examples** — client lifecycle scripts, voter, x402 buyer, security.

## Testing

- vitest; Python's `pytest` modules ported one-to-one (TWAK tests excluded).
- Chain interactions mocked via a custom viem transport with canned
  responses — no live chain calls in CI.
- `test_wallet_conformance` becomes a conformance suite currently exercised
  by the single `EVMWalletProvider` implementation.
- Error-envelope and retry semantics get dedicated tests, mirroring the
  Python suite.

## Error handling

Same hierarchy, as classes extending `BNBAgentError`:
`ConfigurationError`, `ContractError`, `NetworkError`, `ABILoadError`,
`StorageError`, `JobError`, `NegotiationError`. `RateLimitExceeded` and
wallet/x402 error families stay outside the hierarchy, as in Python.
