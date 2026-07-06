# TypeScript SDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `@bnb-chain/bnbagent` under `typescript/` with full feature parity to `python/bnbagent` (TWAK excluded), on viem.

**Architecture:** Same layering as Python — protocols (erc8004/erc8183/x402/signing) over wallets/storage/core/utils; arrows point downward. Single npm package with subpath exports mirroring Python Tier 1/Tier 2. All chain I/O through viem `PublicClient` (reads) + wallet-provider signing (writes). Tests mock the chain with a custom viem transport — no live RPC in CI.

**Tech Stack:** Node ≥ 20, TypeScript 5.x, viem ^2, @noble/hashes + @noble/ciphers (keystore), dotenv, pnpm, tsup (ESM+CJS), vitest, biome.

**Spec:** `docs/superpowers/specs/2026-07-06-typescript-sdk-design.md`
**Authoritative reference:** the Python source at `python/bnbagent/` and its tests at `python/tests/`. Every task names its reference files — the porter MUST read them before implementing; the Python behavior is the contract.

## Global Constraints

- Package name `@bnb-chain/bnbagent`; Node `>=20`; ESM + CJS dual output via tsup.
- Out of scope: TWAK family (`twak_provider`, `twak_custody`, `x402/twak.py`, twak examples), MPC stub, browser support, serving surfaces (`agent-server`/`a2a-agent`), `sync_utils.upload_sync` (TS is async-native).
- All env var names identical to Python (`RPC_URL`, `RPC_URL_BSC_TESTNET`, `ERC8183_COMMERCE_ADDRESS`, `STORAGE_API_KEY`, `BNBAGENT_MIN_GAS_PRICE_WEI`, ...). Empty-string env values ≡ unset.
- All network constants, contract addresses, retry counts, timeouts, gas floors copied verbatim from Python (each task lists them).
- Raw token amounts are `bigint` everywhere. Addresses are checksummed `0x${string}`.
- Naming: Python `snake_case` methods → TS `camelCase` (`to_raw`→`toRaw`, `create_job`→`createJob`, `from_env`→`fromEnv`); class names unchanged; error class names unchanged.
- No serving runtime, no HTTP framework in dependencies (INVARIANT).
- Canonical JSON must be byte-identical to Python's `json.dumps(x, sort_keys=True, separators=(",", ":"))` **including `ensure_ascii` escaping** — all hashes (manifest, negotiation, agent-URI) must interop with the Python SDK. Use `canonicalJson()` from Task 3 for every hash; never raw `JSON.stringify`.
- Every task: run `pnpm -C typescript test` (all green) and `pnpm -C typescript build` before its commit.

**Execution order** (task numbers are stable IDs, not execution sequence):
`1 → 2 → 3 → 4 → 5 → 6 → 7 → 10 (Paymaster) → 11 (wallet core) → 8 (ContractBase) → 9 (multicall) → 13 (signing policy — defer its walletPolicyGating.test.ts) → 12 (EVM wallet — then add walletPolicyGating.test.ts and re-run) → 14 → 15 → 16 → 17 → 18 → 19 → 20 → 21 → 22 → 23 → 24 → 25`.
Reason: Task 8's `ContractBase` consumes `Paymaster` (Task 10) and `Intent`/`IntentExecutor`/`ExecutionContext`/`WalletProvider` (Task 11); Task 12's default `signingPolicy` is `SigningPolicy.strictDefault()` (Task 13), while Task 13's wallet-gating tests need the Task 12 wallet — so land 13's policy core first, then 12, then 13's gating tests.

## Port Conventions (read once, applies to every task)

| Python | TypeScript |
|---|---|
| `web3.Web3(HTTPProvider)` | viem `createPublicClient({ transport: http(rpcUrl), chain })` |
| `dict[str, Any]` tx result | `TxResult` interface (Task 11, `wallets/intents.ts`) |
| `raise ValueError(msg)` | `throw new Error(msg)` (same message text) — unless a named SDK error class exists |
| `Decimal` | `bigint` arithmetic |
| `bytes` (32) | `` `0x${string}` `` hex (viem `Hex`), length-checked |
| `asyncio.Event` stop signal | `AbortSignal` |
| `threading.Lock` | not needed (JS single-threaded); keep reserve/rollback structure for reentrancy safety |
| `time.monotonic()` | `performance.now() / 1000` |
| sync methods | `async` methods (Promise) |
| `from_env()` classmethod | `static fromEnv()` |
| `@property foo` | getter `get foo()` or method — match usage ergonomics, prefer getters for cheap accessors |

Test fixtures mirror `python/tests/conftest.py`: `FAKE_ADDRESS = "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"`, `FAKE_PRIVATE_KEY = "0x" + "ab".repeat(32)`, `FAKE_TX_HASH = "0x" + "de".repeat(32)`. Create `typescript/tests/helpers/mockTransport.ts` in Task 8 — a viem `custom()` transport with a programmable `method → response` table; all later tasks reuse it.

---

### Task 1: Package scaffold + ABI codegen

**Files:**
- Create: `typescript/package.json`, `typescript/tsconfig.json`, `typescript/tsup.config.ts`, `typescript/vitest.config.ts`, `typescript/biome.json`, `typescript/src/index.ts` (placeholder export), `typescript/scripts/abi-codegen.mjs`, `typescript/src/abis/` (generated), `typescript/tests/abis.test.ts`, `typescript/.gitignore` (`dist/`, `node_modules/`)

**Interfaces:**
- Produces: `pnpm -C typescript test|build|lint|codegen` all runnable; `typescript/src/abis/{agenticCommerce,erc20,evaluatorRouter,identityRegistry,optimisticPolicy}.ts` each exporting `export const <name>Abi = [...] as const;`

- [ ] **Step 1: Write package.json / configs**

```json
{
  "name": "@bnb-chain/bnbagent",
  "version": "0.1.0",
  "type": "module",
  "engines": { "node": ">=20" },
  "files": ["dist"],
  "main": "./dist/index.cjs",
  "module": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": { "types": "./dist/index.d.ts", "import": "./dist/index.js", "require": "./dist/index.cjs" },
    "./erc8004": { "types": "./dist/erc8004/index.d.ts", "import": "./dist/erc8004/index.js", "require": "./dist/erc8004/index.cjs" },
    "./erc8183": { "types": "./dist/erc8183/index.d.ts", "import": "./dist/erc8183/index.js", "require": "./dist/erc8183/index.cjs" },
    "./x402": { "types": "./dist/x402/index.d.ts", "import": "./dist/x402/index.js", "require": "./dist/x402/index.cjs" },
    "./storage": { "types": "./dist/storage/index.d.ts", "import": "./dist/storage/index.js", "require": "./dist/storage/index.cjs" },
    "./wallets": { "types": "./dist/wallets/index.d.ts", "import": "./dist/wallets/index.js", "require": "./dist/wallets/index.cjs" },
    "./signing": { "types": "./dist/signing/index.d.ts", "import": "./dist/signing/index.js", "require": "./dist/signing/index.cjs" },
    "./networks": { "types": "./dist/networks/index.d.ts", "import": "./dist/networks/index.js", "require": "./dist/networks/index.cjs" },
    "./utils": { "types": "./dist/utils/index.d.ts", "import": "./dist/utils/index.js", "require": "./dist/utils/index.cjs" }
  },
  "scripts": {
    "codegen": "node scripts/abi-codegen.mjs",
    "build": "tsup",
    "test": "vitest run",
    "lint": "biome check src tests"
  },
  "dependencies": {
    "viem": "^2.21.0",
    "@noble/hashes": "^1.4.0",
    "@noble/ciphers": "^1.0.0",
    "dotenv": "^16.4.0"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "tsup": "^8.0.0",
    "vitest": "^2.0.0",
    "@biomejs/biome": "^1.8.0",
    "@types/node": "^20.0.0"
  }
}
```

tsup entry: `["src/index.ts", "src/erc8004/index.ts", "src/erc8183/index.ts", "src/x402/index.ts", "src/storage/index.ts", "src/wallets/index.ts", "src/signing/index.ts", "src/networks/index.ts", "src/utils/index.ts"]`, `format: ["esm", "cjs"]`, `dts: true`, `clean: true`. tsconfig: `"strict": true`, `"module": "NodeNext"`, `"target": "ES2022"`.

- [ ] **Step 2: Write abi-codegen.mjs**

Reads every `../abis/*.json` (repo root), emits `src/abis/<camelCaseName>.ts`:

```js
import { readdirSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, basename } from "node:path";

const ABIS_DIR = join(import.meta.dirname, "..", "..", "abis");
const OUT_DIR = join(import.meta.dirname, "..", "src", "abis");
mkdirSync(OUT_DIR, { recursive: true });
const names = [];
for (const file of readdirSync(ABIS_DIR).filter((f) => f.endsWith(".json")).sort()) {
  const raw = JSON.parse(readFileSync(join(ABIS_DIR, file), "utf8"));
  const abi = Array.isArray(raw) ? raw : raw.abi; // tolerate {abi: [...]} artifacts
  const base = basename(file, ".json");
  const ident = base[0].toLowerCase() + base.slice(1) + "Abi";
  const out = `// GENERATED from abis/${file} — do not edit. Run: pnpm codegen\nexport const ${ident} = ${JSON.stringify(abi, null, 2)} as const;\n`;
  writeFileSync(join(OUT_DIR, `${base[0].toLowerCase() + base.slice(1)}.ts`), out);
  names.push({ ident, mod: base[0].toLowerCase() + base.slice(1) });
}
writeFileSync(join(OUT_DIR, "index.ts"),
  names.map((n) => `export { ${n.ident} } from "./${n.mod}.js";`).join("\n") + "\n");
```

- [ ] **Step 3: Write failing test** (`tests/abis.test.ts`): imports all five generated ABIs, asserts each is a non-empty array and `agenticCommerceAbi` contains a `createJob` function entry.
- [ ] **Step 4: Run** `cd typescript && pnpm install && pnpm codegen && pnpm test` → PASS; `pnpm build` → dist emitted.
- [ ] **Step 5: Commit** `git add typescript && git commit -m "feat(ts): scaffold @bnb-chain/bnbagent package with ABI codegen"`

---

### Task 2: Error hierarchy

**Files:**
- Create: `typescript/src/errors.ts`
- Test: `typescript/tests/errors.test.ts`

**Interfaces (Produces):**

```ts
export class BNBAgentError extends Error {}
export class ContractError extends BNBAgentError {}
export class StorageError extends BNBAgentError {}
export class ConfigurationError extends BNBAgentError {}
export class ABILoadError extends BNBAgentError {}
export class NetworkError extends BNBAgentError {}
export class RpcRangeLimitError extends NetworkError {}   // retryable range-limit; NOT "event not found"
export class JobError extends BNBAgentError {}
export class NegotiationError extends BNBAgentError {}
export class TransactionPendingError extends BNBAgentError {
  constructor(public readonly txHash: string, public readonly timeoutSeconds: number, message?: string)
  // default message: `Transaction ${txHash} broadcast but not confirmed within ${timeoutSeconds}s; check later or retry safely.`
}
export class ERC8004PartialRegistrationError extends BNBAgentError {
  constructor(public readonly agentId: number, public readonly agentUri: string | null,
              public readonly cause: unknown, public readonly txHash: string | null = null,
              public readonly retryable: boolean = true)
}
```

Each subclass sets `this.name = "<ClassName>"`.

**Reference:** `python/bnbagent/exceptions.py`

- [ ] **Step 1: Failing test** — hierarchy (`new StorageError("x") instanceof BNBAgentError`), `TransactionPendingError` default message + `txHash`/`timeoutSeconds` fields, `ERC8004PartialRegistrationError` message contains `agent_id=1` and appends `pending tx_hash=` only when txHash given.
- [ ] **Step 2: Run** `pnpm -C typescript test errors` → FAIL (module not found).
- [ ] **Step 3: Implement** `src/errors.ts` per interface above (messages copied from Python docstrings).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ts): error hierarchy`

---

### Task 3: Canonical JSON + hash helpers

**Files:**
- Create: `typescript/src/core/canonicalJson.ts`
- Test: `typescript/tests/canonicalJson.test.ts`

**Interfaces (Produces):**

```ts
export function canonicalJson(value: unknown): string;          // sorted keys, compact, ensure_ascii
export function keccakOfText(text: string): `0x${string}`;      // viem keccak256(toBytes(text))
export function keccakOfCanonicalJson(value: unknown): `0x${string}`;
```

**Reference:** every Python `json.dumps(x, sort_keys=True, separators=(",", ":"))` call site; Python `json` uses `ensure_ascii=True` — escapes every char > 0x7E as `\uXXXX` lowercase hex (astral → surrogate pairs).

- [ ] **Step 1: Failing test**

```ts
import { canonicalJson, keccakOfCanonicalJson } from "../src/core/canonicalJson.js";
test("sorts keys recursively and compacts", () => {
  expect(canonicalJson({ b: 1, a: { d: 2, c: [3, { f: 4, e: 5 }] } }))
    .toBe('{"a":{"c":[3,{"e":5,"f":4}],"d":2},"b":1}');
});
test("ensure_ascii parity with Python", () => {
  expect(canonicalJson({ a: "中" })).toBe('{"a":"\\u4e2d"}');   // python: json.dumps({"a":"中"}, ...)
  expect(canonicalJson({ a: "\u007f" })).toBe('{"a":"\\u007f"}'); // python escapes 0x7f too
  expect(canonicalJson({ a: "😀" })).toBe('{"a":"\\ud83d\\ude00"}'); // surrogate pair
});
test("keccak matches Web3.keccak(text=...)", () => {
  // python: Web3.keccak(text='{"a":1}').hex() == "0x08b1732..." — compute the real value with:
  //   python3 -c "from web3 import Web3; print(Web3.keccak(text='{\"a\":1}').hex())"
  // and pin it here as the expected constant.
});
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

```ts
import { keccak256, toBytes } from "viem";
function sortValue(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortValue);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(v as object).sort()) out[k] = sortValue((v as Record<string, unknown>)[k]);
    return out;
  }
  return v;
}
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value)).replace(/[\u007f-\uffff]/g,
    (ch) => "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0"));
}
export function keccakOfText(text: string): `0x${string}` { return keccak256(toBytes(text)); }
export function keccakOfCanonicalJson(value: unknown): `0x${string}` { return keccakOfText(canonicalJson(value)); }
```

Run the Python one-liner from Step 1 to pin the cross-language keccak constant.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ts): canonical JSON with Python hash interop`

---

### Task 4: utils — amounts + rate limiter

**Files:**
- Create: `typescript/src/utils/amounts.ts`, `typescript/src/utils/rateLimit.ts`, `typescript/src/utils/index.ts`
- Test: `typescript/tests/amounts.test.ts`, `typescript/tests/rateLimit.test.ts`

**Interfaces (Produces):**

```ts
export function toRaw(amount: string | number | bigint, decimals: number): bigint;
export function fromRaw(raw: bigint | number, decimals: number): string;
export class RateLimitExceeded extends Error {}                    // NOT a BNBAgentError
export class SlidingWindowLimiter {
  constructor(maxRequests: number, windowSeconds: number, maxKeys?: number /* =10_000 */);
  get maxRequests(): number; get windowSeconds(): number; get maxKeys(): number;
  check(key: string): void;                                        // throws RateLimitExceeded("Too many requests")
}
```

**Reference:** `python/bnbagent/utils/amounts.py`, `python/bnbagent/utils/rate_limit.py`; tests `test_amounts.py`, `test_rate_limit.py`.

- [ ] **Step 1: Failing tests** — port the Python assertions verbatim:

```ts
expect(toRaw("1.5", 18)).toBe(1_500_000_000_000_000_000n);
expect(toRaw(2, 18)).toBe(2n * 10n ** 18n);
expect(toRaw(1.1, 18)).toBe(1_100_000_000_000_000_000n);   // float-precision invariant
expect(toRaw("0", 18)).toBe(0n);
expect(toRaw("0.000000000000000001", 18)).toBe(1n);
expect(toRaw("1.5", 6)).toBe(1_500_000n);
expect(fromRaw(2n * 10n ** 18n, 18)).toBe("2");
expect(fromRaw(1_500_000_000_000_000_000n, 18)).toBe("1.5");
expect(fromRaw(1n, 18)).toBe("0.000000000000000001");      // no scientific notation
expect(fromRaw(0n, 18)).toBe("0");
for (const s of ["0.1", "1", "1.5", "123.456789", "0.000000000000000001"])
  expect(fromRaw(toRaw(s, 18), 18)).toBe(s);               // round trip
```

Rate limiter: allows up to limit / throws over limit / independent keys / window recovery (inject a fake clock — make the time source an optional constructor param `now?: () => number` defaulting to `() => performance.now() / 1000`) / ctor validation messages (`"max_requests must be > 0"` etc.) / LRU bounded at maxKeys, eviction resets budget, access refreshes LRU position.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `toRaw`: parse `^(-?)(\d+)(?:\.(\d*))?$` (reject otherwise), pad/truncate frac to `decimals` digits (truncation = Python `int()` semantics), bigint math. `fromRaw`: integer/remainder split, trim trailing zeros, drop dot if empty frac. Limiter: `Map<string, number[]>` (insertion order = LRU; `delete`+`set` to touch), prune `<= now - windowSeconds`, reject BEFORE recording the hit.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(ts): amounts + sliding-window rate limiter`

---

### Task 5: NetworkConfig + networks registry

**Files:**
- Create: `typescript/src/config.ts`, `typescript/src/networks/addresses.ts`, `typescript/src/networks/index.ts`, `typescript/src/core/envUtil.ts`
- Test: `typescript/tests/resolveNetwork.test.ts`, `typescript/tests/networksAddresses.test.ts`

**Interfaces (Produces):**

```ts
// core/envUtil.ts
export function getEnv(key: string, defaultValue?: string, prefix?: string): string | undefined; // "" ≡ unset

// config.ts
export interface NetworkConfig {
  name: string; chainId: number; rpcUrl: string;
  paymasterUrl?: string; usePaymaster: boolean;
  registryContract: string; commerceContract: string; routerContract: string; policyContract: string;
}
export const NETWORKS: Record<string, NetworkConfig>;   // "bsc-testnet", "bsc-mainnet" — values verbatim below
export function resolveNetwork(network?: string | NetworkConfig): NetworkConfig; // default "bsc-testnet"

// networks/addresses.ts
export const BSC_MAINNET_CHAIN_ID = 56; export const BSC_TESTNET_CHAIN_ID = 97;
export const PAYMENT_TOKEN_EIP712_NAME = "United Stables"; export const PAYMENT_TOKEN_EIP712_VERSION = "1";
export interface DeployedAddresses { paymentToken: `0x${string}`; treasury: `0x${string}`;
  commerceProxy: `0x${string}`; commerceImpl: `0x${string}`; routerProxy: `0x${string}`;
  routerImpl: `0x${string}`; policy: `0x${string}`; }
export const BNB_CHAIN_ADDRESSES: Record<number, DeployedAddresses>;   // frozen, checksummed
export function getAddress(chainId: number): DeployedAddresses;       // throws on unknown chain, message lists known ids
export function knownPaymentTokens(): ReadonlySet<string>;            // keys "chainId:checksumAddress"
```

Verbatim constants (from `python/bnbagent/config.py` + `networks/addresses.py`):

| | bsc-testnet (97) | bsc-mainnet (56) |
|---|---|---|
| rpcUrl | `https://data-seed-prebsc-2-s2.binance.org:8545` | `https://bsc-dataseed.binance.org` |
| paymasterUrl | `https://bsc-megafuel-testnet.nodereal.io` | `https://bsc-megafuel.nodereal.io/` |
| usePaymaster | true | true |
| registryContract | `0x8004A818BFB912233c491871b3d84c89A494BD9e` | `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` |
| commerceContract | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` | `0xea4daa3100a767e86fded867729ae7446476eba6` |
| routerContract | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` | `0x51895229e12f9876011789b04f8698af06ccd6da` |
| policyContract | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` | `0x9c01845705b3078aa2e8cff7520a6376fd766de5` |
| paymentToken | `0xc70B8741B8B07A6d61E54fd4B20f22Fa648E5565` | `0xcE24439F2D9C6a2289F741120FE202248B666666` |
| treasury | `0x1001b2C085345f388778A975648aA50bcfd0D134` | `0x000000000000000000000000000000000000dEaD` |
| commerceImpl | `0xc0b74dc6b1c95b1452f678741e7907290587d69b` | `0x2788d06576ef83fdbeb00fb848e9fd896fc259e6` |
| routerImpl | `0x9f42b71ae5990e6f5bb58a935fffe32b29a5374a` | `0xf0cf8f47e5c035f16247ff16e9f367e477ee5007` |

`resolveNetwork` semantics (INVARIANTS from `test_resolve_network.py`): NetworkConfig object → returned identity-same, env never applied; unknown string → `Error("Unknown network: <name>")`; RPC override precedence `RPC_URL_<NAME upper, - → _>` > `RPC_URL` > preset; when overridden, new object with `usePaymaster = !rpcOverride.startsWith("http://localhost")`, all other fields preserved. `BNB_CHAIN_ADDRESSES` checksummed via viem `getAddress()` at module load, `Object.freeze`d.

**Reference:** `python/bnbagent/config.py`, `python/bnbagent/networks/addresses.py`, `python/bnbagent/core/config.py` (`get_env`); tests `test_resolve_network.py`, `test_networks_addresses.py`.

- [ ] **Step 1: Failing tests** — port every case from the two Python test modules (precedence trio, per-network scoping, both-pinned-simultaneously, localhost-disables-paymaster, metadata preserved, object-ignores-env, unknown-raises; addresses: both payment tokens, unknown chain error, all-checksummed, knownPaymentTokens has exactly 2 entries, registry frozen). Use `vi.stubEnv`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ts): network config + deployment address registry`

---

### Task 6: loadEnv

**Files:**
- Create: `typescript/src/core/env.ts`
- Test: `typescript/tests/loadEnv.test.ts`

**Interfaces (Produces):** `export function loadEnv(root?: string): string[];` — loads `.env.local` then `.env` from `root` (default `process.cwd()`), each via `dotenv.config({ path, override: false })`; precedence **real env > .env.local > .env**; no upward search; returns list of files actually loaded, in load order; never auto-invoked at import time.

**Reference:** `python/bnbagent/core/env.py`; tests `test_load_env.py` (5 cases: local-wins, real-env-wins-over-both, env-only, missing-files-empty-list, no-upward-search).

- [ ] **Steps 1–5:** failing tests in a `mkdtemp` sandbox (snapshot/restore `process.env` around each test) → implement → pass → commit `feat(ts): opt-in .env.local-first loadEnv`.

---

### Task 7: NonceManager

**Files:**
- Create: `typescript/src/core/nonceManager.ts`
- Test: `typescript/tests/nonceManager.test.ts`

**Interfaces (Produces):**

```ts
export class NonceManager {
  static forAccount(client: PublicClient, account: `0x${string}`): NonceManager; // singleton per (rpcUrl, checksumAddr)
  static _clearAll(): void;                                                      // tests only
  async getNonce(): Promise<number>;   // seed once from getTransactionCount(addr, "pending"), then local increment
  async handleError(error: unknown, usedNonce: number): Promise<boolean>;        // pattern match → re-sync + true
  reset(): void;                                                                 // force re-seed
}
export const NONCE_ERROR_PATTERNS = ["nonce too low", "already known", "replacement transaction underpriced"] as const;
```

RPC key: `client.transport.url ?? String(client.uid)`. Async mutex (promise chain) guards seed-then-increment so concurrent `getNonce()` calls get unique values.

**Reference:** `python/bnbagent/core/nonce_manager.py`; tests `test_nonce_manager.py`.

- [ ] **Steps 1–5:** port tests (singleton keying incl. lowercase→checksum normalization; seed-once-with-pending then zero further RPC over 10 calls; 50 concurrent `getNonce()` → 50 unique; the 3 error patterns re-sync and return true, `"out of gas"` → false; reset re-seeds). Mock client = `{ transport: { url }, getTransactionCount: vi.fn() }` cast. Commit `feat(ts): per-account singleton nonce manager`.

---

### Task 8: tx config knobs + ContractBase + mock transport helper

**Files:**
- Create: `typescript/src/core/txConfig.ts`, `typescript/src/core/contractBase.ts`, `typescript/src/core/clients.ts`, `typescript/tests/helpers/mockTransport.ts`
- Test: `typescript/tests/txConfig.test.ts`, `typescript/tests/contractBase.test.ts`

**Interfaces (Produces):**

```ts
// txConfig.ts — constants verbatim from python core/contract_mixin.py
export const MAX_RETRIES = 5; export const RETRY_BASE_DELAY = 1.0;
export const MIN_GAS_PRICE_WEI = 100_000_000n;
export const MIN_GAS_PRICE_WEI_PER_CHAIN: Record<number, bigint> = { 56: 100_000_000n, 97: 1_000_000_000n };
export const DEFAULT_GAS_FALLBACK = 2_000_000n; export const DEFAULT_RECEIPT_TIMEOUT = 300;
export function setMinGasPriceWei(wei: bigint): void;         // <=0 → Error("min gas price must be positive")
export function minGasPriceWei(chainId: number): bigint;      // override > env BNBAGENT_MIN_GAS_PRICE_WEI > per-chain > default
export function setDefaultReceiptTimeout(seconds: number): void; // <=0 → Error("receipt timeout must be positive")
export function getDefaultReceiptTimeout(): number;           // override > env BNBAGENT_RECEIPT_TIMEOUT > 300
export function _resetTxConfigOverrides(): void;              // tests only

// clients.ts
export function createPublicClientFor(rpcUrl?: string): PublicClient;  // "" → BSC testnet default URL

// contractBase.ts — TxResult is imported from wallets/intents.ts (Task 11), not declared here
export interface ContractBaseOpts { client: PublicClient; address: `0x${string}`; abi: Abi;
  walletProvider?: WalletProvider | null; paymaster?: Paymaster | null; }
export class ContractBase {
  readonly address: `0x${string}`;
  protected callWithRetry<T>(fn: () => Promise<T>): Promise<T>;
  protected executeIntent(intent: Intent): Promise<TxResult>;   // read-only → RuntimeError-equivalent Error; executor cached
  protected sendTx(req: { functionName: string; args: readonly unknown[]; value?: bigint;
    gas?: bigint; skipPreflight?: boolean }): Promise<TxResult>;
  protected readEvents(opts: { eventName: string; fromBlock: bigint; toBlock?: bigint | "latest";
    args?: Record<string, unknown> }): Promise<DecodedEventLog[]>;
}

// tests/helpers/mockTransport.ts
export function mockTransport(handlers: Record<string, (params: unknown[]) => unknown>): Transport;
export function mockPublicClient(handlers?: ...): PublicClient;  // defaults: chainId 12345, gasPrice, blockNumber 1000n, estimateGas, ethCall ok, sendRawTransaction → FAKE_TX_HASH, receipt status success
```

`sendTx` semantics — port every numbered rule from `python/bnbagent/core/contract_mixin.py::_send_tx` (the port inventory in this plan's spec-research is authoritative): no wallet → `Error("wallet_provider is required for write operations (client is read-only)")`; gas = `estimateGas * 1.2` (bigint: `gas * 12n / 10n`), fallback `DEFAULT_GAS_FALLBACK`, revert during estimation → `Error("Transaction would revert: ...")` unless opaque `'0x'`; gasPrice = `max(gasPrice * 12n / 10n, floor)` per attempt; preflight `eth_call` with 10s timeout (timeout/opaque → warn+proceed, real revert → throw before broadcast); sign via `walletProvider.signTransaction`, broadcast raw; receipt wait with `getDefaultReceiptTimeout()`, timeout → `TransactionPendingError` (NEVER retried); status 0 (viem `"reverted"`) → `Error("Transaction reverted on-chain: <hash>")`; retry loop MAX_RETRIES with nonce-error immediate retry via `NonceManager.handleError`, 429/"too many requests" exponential backoff `RETRY_BASE_DELAY * 2**attempt` s, other errors → `nonceMgr.reset()` + rethrow. Legacy `gasPrice` txs (BSC), `chainId` from client.

**Reference:** `python/bnbagent/core/contract_mixin.py`; tests `test_tx_config.py`, `test_contract_mixin.py`.

- [ ] **Step 1: Failing tests** — port: gas floors per chain (56→0.1 gwei, 97→1 gwei, unknown→0.1); precedence setter>env>table, invalid env ignored, setter rejects ≤0; receipt timeout default 300/env/setter/lazy resolution; 20% gas buffer (100k→120k); explicit gas skips estimation; estimation transport error → fallback 2M; revert string → "Transaction would revert" and no broadcast; gasPrice floored (chain 97 reports 100 wei → tx uses 1 gwei; 5 gwei → 6 gwei); receipt timeout → `TransactionPendingError` with `txHash === FAKE_TX_HASH` and exactly 1 broadcast; nonce-too-low → re-sync + retry succeeds (2 broadcasts); 429 → one backoff sleep (fake timers) + retry; `"insufficient funds"` → immediate throw, 1 broadcast; `executeIntent` read-only guard + executor cached across two writes.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (txConfig first, then mockTransport, then ContractBase). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(ts): ContractBase with retry/backoff, gas floors, pending-tx semantics`

---

### Task 9: multicall

**Files:**
- Create: `typescript/src/core/multicall.ts`
- Test: `typescript/tests/multicall.test.ts`

**Interfaces (Produces):**

```ts
export const MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11";
export const DEFAULT_BATCH_SIZE = 100;
export function multicallRead(client: PublicClient, opts: {
  address: `0x${string}`; abi: Abi; functionName: string;
  callArgsList: readonly (readonly unknown[])[]; batchSize?: number; allowFailure?: boolean;
}): Promise<Array<[boolean, unknown]>>;   // order preserved; failures → [false, null]
```

Implement over viem's `publicClient.multicall({ contracts, allowFailure, batchSize })` (viem handles aggregate3 + decode); wrap each batch in the same 5×/exponential-backoff rate-limit retry as `callWithRetry`. Empty input → `[]` with zero RPC.

**Reference:** `python/bnbagent/core/multicall.py`; tests `test_multicall.py` (single batch, 250@100 → 3 batches, partial failure ordering, empty list, batch exception propagates).

- [ ] **Steps 1–5:** port tests using mockTransport (count `eth_call`s to Multicall3 to assert batching) → implement → pass → commit `feat(ts): multicall3 batch reads`.

---

### Task 10: Paymaster

**Files:**
- Create: `typescript/src/core/paymaster.ts`
- Test: `typescript/tests/paymaster.test.ts`

**Interfaces (Produces):**

```ts
export class Paymaster {
  constructor(paymasterUrl: string, debug?: boolean);
  async ethGetTransactionCount(address: string, block?: string): Promise<number>;  // default "latest"
  async ethSendRawTransaction(signedTransaction: string, txOptions?: Record<string, string>): Promise<string>;
  async isSponsorable(tx: { to?: string; from?: string; value?: bigint; data?: string; gas?: bigint }): Promise<boolean>;
}
export function toHex(value: bigint | number | string | Uint8Array | null | undefined, defaultValue?: string): string;      // default "0x0"
export function toAddressHex(address: string | null | undefined, defaultValue?: string): string;
```

JSON-RPC 2.0 POST via `fetch`, 30s `AbortSignal.timeout`; `txOptions` → extra HTTP headers; body `error` → `Error("RPC error [<code>]: <message>")`; missing `result` → `ethGetTransactionCount`/`ethSendRawTransaction` throw `Error("Failed to ... missing 'result' field")`, `isSponsorable` logs + returns false; `isSponsorable` method `pm_isSponsorable`, params `[{to, from, value, data, gas}]` hex-encoded with `"0x0"` defaults; `0x` auto-prefixed on inputs.

**Reference:** `python/bnbagent/core/paymaster.py`; tests `test_paymaster.py`. Mock `fetch` with `vi.stubGlobal`.

- [ ] **Steps 1–5:** port tests (`toHex`/`toAddressHex` table, `"0xa"`→10, missing-result errors, sponsorable bool, RPC error object, network error propagates) → implement → pass → commit `feat(ts): MegaFuel paymaster JSON-RPC client`.

---

### Task 11: Wallet core — WalletProvider, capabilities, intents, errors

**Files:**
- Create: `typescript/src/wallets/capabilities.ts`, `typescript/src/wallets/intents.ts`, `typescript/src/wallets/errors.ts`, `typescript/src/wallets/walletProvider.ts`, `typescript/src/wallets/index.ts`
- Test: `typescript/tests/walletConformance.test.ts` (part 1: base-class behavior)

**Interfaces (Produces):**

```ts
// capabilities.ts — verbatim strings
export const SIGN_MESSAGE = "sign.message"; export const SIGN_TRANSACTION = "sign.transaction";
export const SIGN_TYPED_DATA = "sign.typed_data"; export const CALLS_ARBITRARY = "calls.arbitrary";
export const BROADCAST_SELF = "broadcast.self"; export const INTENTS_ERC8004 = "intents.erc8004";
export const INTENTS_ERC8183 = "intents.erc8183"; export const X402_PAY = "x402.pay";
export const PAYMASTER_SPONSOR = "paymaster.sponsor";

// intents.ts — names verbatim (erc8004.register, erc8183.create_job, ... all 16 from python wallets/intents.py)
export interface Intent { name?: string; kwargs?: Record<string, unknown>;
  call?: ContractCall | null;   // { address, abi, functionName, args, value?, gas? } — the mechanical form
  value?: bigint; gas?: bigint | null; description?: string; }
export interface ContractCall { address: `0x${string}`; abi: Abi; functionName: string; args: readonly unknown[]; }
export interface ExecutionContext { client: PublicClient; paymaster?: Paymaster | null; receiptTimeout?: number | null; }
export interface TxResult { transactionHash: `0x${string}`; status: number;
  receipt: TransactionReceipt | null; [k: string]: unknown; }   // receipt may be null for backends without full receipts
export interface IntentExecutor { execute(intent: Intent): Promise<TxResult>; }

// errors.ts
export class UnsupportedWalletOperation extends Error {   // message assembly identical to Python
  constructor(capabilityOrOperation: string, opts?: { reason?: string; alternative?: string; ref?: string });
}
export class WalletIdentityMismatch extends Error {
  constructor(opts: { expected: string; actual: string });
  readonly expected: string; readonly actual: string;
}

// walletProvider.ts
export abstract class WalletProvider {
  static readonly kind: string = "custom"; get kind(): string;   // instance accessor reads constructor static
  readonly fundBundlesApproval: boolean = false;
  protected readonly extraCapabilities: ReadonlySet<string> = new Set();
  abstract get address(): `0x${string}`;
  get keyLocation(): string | null;                 // default null
  exists(): boolean;                                // default true, MUST NOT throw
  capabilities(): ReadonlySet<string>;              // auto-derived sign.* ∪ extraCapabilities
  supports(capability: string): boolean;
  describe(): { kind: string; address: string | null; keyLocation: string | null; exists: boolean; capabilities: string[] };
  makeExecutor(context: ExecutionContext): IntentExecutor;   // default: requires sign.transaction → LocalExecutor (Task 14 wires it); else throws UnsupportedWalletOperation at construction
  makeX402Payer(payerKwargs?: Record<string, unknown>): never; // default throws UnsupportedWalletOperation(X402_PAY, ...)
  signTransaction(tx: TransactionRequestLegacy): Promise<SignedTx>;      // default throws
  signMessage(message: string): Promise<SignatureResult>;               // default throws (EIP-191 TEXT semantics)
  signTypedData(domain: TypedDataDomain, types: Record<string, {name: string; type: string}[]>,
                message: Record<string, unknown>): Promise<SignatureResult>;  // default throws
}
export interface SignedTx { rawTransaction: `0x${string}`; hash: `0x${string}`; r: `0x${string}`; s: `0x${string}`; v: bigint; }
export interface SignatureResult { messageHash: `0x${string}`; r: `0x${string}`; s: `0x${string}`; v: bigint; signature: `0x${string}`; }
```

Capability auto-derivation (the Python `getattr(type(self), name) is not getattr(WalletProvider, name)` mechanism):

```ts
capabilities(): ReadonlySet<string> {
  const derived = new Set<string>();
  const pairs: Array<[string, "signMessage" | "signTransaction" | "signTypedData"]> = [
    [SIGN_MESSAGE, "signMessage"], [SIGN_TRANSACTION, "signTransaction"], [SIGN_TYPED_DATA, "signTypedData"]];
  for (const [cap, method] of pairs)
    if (this[method] !== WalletProvider.prototype[method]) derived.add(cap);
  for (const extra of this.extraCapabilities) derived.add(extra);
  return derived;
}
```

**Reference:** `python/bnbagent/wallets/{capabilities,intents,errors,wallet_provider}.py`; tests `test_wallet_conformance.py`, `test_wallet_factory.py` (base-default `kind === "custom"`).

- [ ] **Step 1: Failing tests** — address-only subclass → empty capability set; subclass overriding `signMessage` → exactly `{sign.message}`; override-to-raise still claims capability (documents the "never override to raise" rule); `extraCapabilities` unions (vendor `"acme.batch_sign"` accepted); `supports("unknown.cap")` → false never throws; `describe()` capabilities sorted, address null when getter throws; `makeExecutor` on signless subclass throws `UnsupportedWalletOperation` mentioning `sign.transaction` at construction; all three default sign methods throw `UnsupportedWalletOperation` that is `instanceof Error` and names the capability; `UnsupportedWalletOperation` message assembly (`"cap: reason. Alternative: alt. (ref: r)"`).
- [ ] **Steps 2–5:** run FAIL → implement → PASS → commit `feat(ts): wallet provider base with capability auto-derivation and intent seam`.

---

### Task 12: EVMWalletProvider (Keystore V3)

**Files:**
- Create: `typescript/src/wallets/keystore.ts`, `typescript/src/wallets/evmWalletProvider.ts`
- Test: `typescript/tests/wallet.test.ts`, fixture `typescript/tests/fixtures/keystore-interop.json`

**Interfaces (Produces):**

```ts
// keystore.ts — Keystore V3, eth-account/Geth-compatible
export interface KeystoreV3 { version: 3; id: string; address: string; crypto: {...} }
export function encryptKeystoreV3(privateKey: Uint8Array, password: string): KeystoreV3;
  // scrypt n=262144, r=8, p=1, dklen=32, salt=16 random bytes, cipher "aes-128-ctr",
  // mac = keccak256(dk[16..32] || ciphertext); via @noble/hashes/scrypt + @noble/ciphers/aes (ctr)
export function decryptKeystoreV3(keystore: KeystoreV3, password: string): Uint8Array;
  // supports kdf "scrypt" (required) and "pbkdf2" (hmac-sha256); wrong MAC → Error("Failed to decrypt keystore (wrong password?): MAC mismatch")

// evmWalletProvider.ts
export class EVMWalletProvider extends WalletProvider {
  static readonly kind = "evm";
  // extraCapabilities = {CALLS_ARBITRARY, PAYMASTER_SPONSOR}; sign.* auto-derive (all 3 overridden)
  constructor(opts: { password: string; privateKey?: string; address?: string;
                      persist?: boolean /* =true */; walletsDir?: string;
                      signingPolicy?: SigningPolicy /* =SigningPolicy.strictDefault() */ });
  static keystoreExists(address?: string, walletsDir?: string): boolean;
  static listWallets(walletsDir?: string): string[];
  get source(): "" | "imported" | "loaded_keystore" | "created_new";
  get address(): `0x${string}`;
  get signingPolicy(): SigningPolicy;
  signTransaction(tx: TransactionRequestLegacy): Promise<SignedTx>;
  signMessage(message: string): Promise<SignatureResult>;                    // EIP-191 personal-sign, TEXT semantics
  signTypedData(domain, types, message): Promise<SignatureResult>;          // runs check(policy, ...) FIRST, strips EIP712Domain from types
  _DANGEROUS_signTypedDataNoPolicy(domain, types, message): Promise<SignatureResult>; // console.warn "POLICY BYPASS"
  exportPrivateKey(): `0x${string}`; exportKeystore(): KeystoreV3;
  get keyLocation(): string | null; exists(): boolean;
}
```

Resolution order and every error message identical to Python (`Password is required...`, `Private key must be 64 hex characters (32 bytes)`, `Invalid private key: ...`, `Keystore not found: ...`, `Failed to decrypt keystore (wrong password?): ...`, `Multiple wallets found in ...`, `private_key is required when persist=False (in-memory-only mode)`). Wallets dir default `~/.bnbagent/wallets/`, file `<checksum-address>.json`, dir chmod 0700, atomic write (tmp + rename) chmod 0600. Internally hold a viem `privateKeyToAccount` for signing. Signing is delegated to `account.signTransaction` (legacy type, gasPrice), `account.signMessage({message})`, `account.signTypedData` — derive `{messageHash, r, s, v, signature}` from the 65-byte signature (`hashMessage`/`hashTypedData` from viem for the digests).

**Reference:** `python/bnbagent/wallets/evm_wallet_provider.py`; tests `test_wallet.py`, `test_wallet_policy_gating.py` (gating cases land in Task 13's test run too).

- [ ] **Step 1: Generate interop fixture** (one-time, committed):
`cd python && uv run python -c "from eth_account import Account; import json; print(json.dumps(Account.encrypt('0x'+'ab'*32, 'test-password')))" > ../typescript/tests/fixtures/keystore-interop.json` (use the repo's Python env; any invocation that resolves `eth_account` works).
- [ ] **Step 2: Failing tests** — decrypt the eth-account fixture with password `test-password` → recovers key `0xab…` (THE interop invariant); own encrypt→decrypt round-trip; create-new writes `<addr>.json` + `source === "created_new"`; import with/without `0x`; invalid key / empty password / persist=false-without-key error messages; load by address / wrong password / multiple keystores / unknown address; `keystoreExists` + `listWallets`; signTransaction → rawTransaction+hash; signMessage EIP-191 recover round-trip (viem `recoverMessageAddress`); typed-data sign → `recoverTypedDataAddress` recovers wallet (use `SigningPolicy.permissive()` — Task 13's policy core is already landed per the execution order; after this task, add Task 13's deferred `walletPolicyGating.test.ts`); EIP712Domain-in-types stripped → identical signature; exportPrivateKey format; persist=false creates no dir.
- [ ] **Steps 3–5:** implement → PASS → commit `feat(ts): EVM keystore-v3 wallet provider with eth-account interop`.

---

### Task 13: SigningPolicy + checks

**Files:**
- Create: `typescript/src/signing/policy.ts`, `typescript/src/signing/checks.ts`, `typescript/src/signing/errors.ts`, `typescript/src/signing/index.ts`
- Test: `typescript/tests/signingPolicy.test.ts`, `typescript/tests/walletPolicyGating.test.ts`

**Interfaces (Produces):**

```ts
export const EIP3009_TYPES: ReadonlySet<string>;   // {"TransferWithAuthorization","ReceiveWithAuthorization"}
export const PERMIT_UNBOUNDED_TYPES: ReadonlySet<string>;  // {"Permit","PermitSingle","PermitBatch"}
export const PERMIT2_SIGNATURE_TRANSFER_TYPES: ReadonlySet<string>; // {"PermitTransferFrom","PermitBatchTransferFrom"}
export class PolicyViolation extends Error {
  constructor(reason: string, opts?: { primaryType?: string; chainId?: number; verifyingContract?: string });
  readonly reason: string; readonly primaryType?: string; readonly chainId?: number; readonly verifyingContract?: string;
  // message = "; ".join(reason, "primary_type=...", "chain_id=..." (when not undefined, so 0 included), "verifyingContract=..." (truthy))
}
export class SigningPolicy {           // immutable (Object.freeze in ctor)
  readonly domainAllowlist: ReadonlySet<string>;   // "chainId:checksumAddress" keys
  readonly primaryTypeAllowlist: ReadonlySet<string>;
  readonly primaryTypeDenylist: ReadonlySet<string>;
  readonly validityRequiredPrimaryTypes: ReadonlySet<string>;
  readonly maxValidityWindowSeconds: number;       // 600
  readonly maxFutureValiditySeconds: number;       // 900
  readonly allowUnknownDomain: boolean;            // false
  static strictDefault(): SigningPolicy;           // domainAllowlist = knownPaymentTokens(); allow EIP3009; deny PERMIT_UNBOUNDED; validity-require EIP3009
  static permissive(opts?: { allowInProduction?: boolean }): SigningPolicy;  // ENV/ENVIRONMENT ∈ {prod,production,live,mainnet-prod} → Error unless allowInProduction; always console.warn "POLICY DISABLED"
  extend(opts: { domainAllowlist?: Iterable<[number, string]>; primaryTypeAllowlist?: Iterable<string>;
    primaryTypeDenylist?: Iterable<string>; validityRequiredPrimaryTypes?: Iterable<string>;
    maxValidityWindowSeconds?: number; maxFutureValiditySeconds?: number; allowUnknownDomain?: boolean }): SigningPolicy;
    // set args UNION; scalars REPLACE
  toDict(): Record<string, unknown>;   // sorted lists; domain pairs as [chainId, address] arrays
  static fromDict(d: Record<string, unknown>): SigningPolicy;  // malformed domain entry → Error(`domain_allowlist[i] must be a [chain_id, address] pair, got ...`)
  toString(): string;                  // operator summary with (none)/(any)
}
export function inferPrimaryType(types: Record<string, unknown>): string;  // single non-EIP712Domain key; 0 or >1 → PolicyViolation
export function check(policy: SigningPolicy, domain, types, message, opts?: { now?: number }): string;
```

`check()` ordering is LOAD-BEARING — port exactly: infer → structure (chainId present / verifyingContract present) → chainId int-coercible → verifyingContract checksummable → **denylist** → allowlist (skipped when empty) → domain allowlist (skipped when `allowUnknownDomain`) → validity window (validBefore/validAfter required, coercible, `before > after`, `before - after ≤ 600`, `before > now`, `before - now ≤ 900`). Every violation message text copied from Python.

**Reference:** `python/bnbagent/signing/{policy,checks,errors}.py`; tests `test_signing_policy.py`, `test_wallet_policy_gating.py`.

- [ ] **Steps 1–5:** port both test modules in full (strict-default allows U-token TWA both chains; unknown contract/chain rejected; Permit + PermitSingle denylisted, denylist beats allowlist; window 1200s / future 1000s / expired / swapped / missing validity messages; extend union + immutability; permissive passes anything; production guard incl. case/whitespace-insensitivity + break-glass; toDict/fromDict round-trip + defaults + malformed entry; PolicyViolation structured fields; wallet gating: strict wallet signs U-token mainnet TWA, rejects unknown contract with fields populated, rejects Permit on allowlisted token, `_DANGEROUS_` bypass signs + warns "POLICY BYPASS", extended policy signs custom contract, `signingPolicy` getter) → implement → PASS → commit `feat(ts): EIP-712 signing policy with strict default`.

---

### Task 14: LocalExecutor (+ paymaster sponsored path)

**Files:**
- Create: `typescript/src/wallets/localExecutor.ts`; Modify: `typescript/src/wallets/walletProvider.ts` (wire default `makeExecutor` to return `new LocalExecutor(...)`)
- Test: `typescript/tests/localExecutorPaymaster.test.ts`

**Interfaces (Produces):**

```ts
export class LocalExecutor implements IntentExecutor {
  constructor(opts: { client: PublicClient; walletProvider: WalletProvider;
                      paymaster?: Paymaster | null; receiptTimeout?: number | null });
  async execute(intent: Intent): Promise<TxResult>;   // intent.call required else Error("LocalExecutor requires Intent.call ...")
}
```

Flow (port from `python/bnbagent/wallets/local_executor.py`): estimate gas ×1.2; sponsored path when paymaster present — paymaster nonce via `ethGetTransactionCount(addr, "pending")` (failure → self-pay), preflight sim, `isSponsorable` (false/throw → self-pay), sponsored tx has `gasPrice = 0n`, broadcast via `paymaster.ethSendRawTransaction(raw, { UserAgent: "bnbagent/v1.0.0" })`, sponsored send NEVER retried into self-pay; genuine preflight revert → throw (no send by either path). Self-pay: NonceManager + gas floor + preflight + retry loop, identical to `sendTx` semantics from Task 8 (share the helper — extract the common broadcast/retry core into `src/core/txSender.ts` if duplication exceeds ~40 lines; both ContractBase and LocalExecutor call it). Receipt timeout → `TransactionPendingError`; status reverted → `Error("Transaction reverted on-chain: <hash>")`.

**Reference:** `python/bnbagent/wallets/local_executor.py`; tests `test_local_executor_paymaster.py`, `test_tx_config.py` (lazy receipt-timeout resolution case).

- [ ] **Steps 1–5:** port tests (sponsorable → paymaster send, no client broadcast, gasPrice 0; not-sponsorable → self-pay + info log; isSponsorable throws → self-pay; nonce fetch fails → self-pay without isSponsorable; no paymaster → self-pay; sponsored preflight revert → throws, zero sends; ctor timeout beats global, `receiptTimeout: null` resolves `setDefaultReceiptTimeout` lazily) → implement → PASS → commit `feat(ts): local executor with MegaFuel sponsorship fallback`.

---

### Task 15: Storage — provider + Local + IPFS

**Files:**
- Create: `typescript/src/storage/storageProvider.ts`, `typescript/src/storage/localStorageProvider.ts`, `typescript/src/storage/ipfsStorageProvider.ts`, `typescript/src/storage/index.ts`
- Test: `typescript/tests/localStorage.test.ts`, `typescript/tests/ipfsStorage.test.ts`, `typescript/tests/storageFromEnv.test.ts`

**Interfaces (Produces):**

```ts
export abstract class StorageProvider {
  readonly usesFileUrl: boolean = false;
  abstract upload(data: Record<string, unknown>, filename?: string): Promise<string>;
  abstract download(url: string): Promise<Record<string, unknown>>;
  abstract exists(url: string): Promise<boolean>;
  static computeHash(data: Record<string, unknown>): Uint8Array;      // keccak256(canonicalJson)
  static computeContentHash(content: string): Uint8Array;
}
export class LocalStorageProvider extends StorageProvider {
  readonly usesFileUrl = true;
  constructor(baseDir?: string /* ".agent-data" */);    // mkdir -p + chmod 0700; OSError → StorageError
  static fromEnv(): LocalStorageProvider;               // STORAGE_LOCAL_PATH
}
export class IPFSStorageProvider extends StorageProvider {
  constructor(pinningApiUrl: string, pinningApiKey: string, gatewayUrl?: string /* pinata, trailing / stripped */);
  static fromEnv(): IPFSStorageProvider;                // STORAGE_API_KEY required → Error("STORAGE_API_KEY required for IPFSStorageProvider"); STORAGE_API_URL, STORAGE_GATEWAY_URL defaults
  getGatewayUrl(ipfsUrl: string): string;
}
```

Local semantics: filename → append `.json` unless present; else `job-<data.job.id>.json`; else `<keccakHex-no-0x>.json`; **path-traversal guard** on upload AND download AND exists (resolved path must stay inside baseDir, incl. symlink escape — use `fs.realpath` on the base and `path.resolve` check) → `StorageError("Path traversal blocked: path is outside storage directory")`; file chmod 0600; returns `file://<absolute>`; download strips `file://` then re-joins remainder against baseDir. IPFS: Pinata `pinJSONToIPFS` POST (Bearer JWT, 60s timeout), pin name = filename minus `.json` else `erc8183-job-<id>` else `"deliverable"`, body `{pinataContent, pinataMetadata:{name}}`, CID from `IpfsHash` or `cid` else `StorageError("Unexpected pinning response: ...")`; download GET `<gateway>/<cid>` 30s; exists HEAD 10s status-200; CID regex `^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58,})$` else `StorageError("Invalid IPFS CID format: ...")`.

**Reference:** `python/bnbagent/storage/*.py`; tests `test_local_storage.py`, `test_ipfs_storage.py`, `test_storage_from_env.py`. Mock `fetch` for IPFS.

- [ ] **Steps 1–5:** port all cases (esp. the 7 traversal cases incl. symlink + `file:///etc/passwd`, and "escape file never created") in `mkdtemp` sandboxes → implement → PASS → commit `feat(ts): local + IPFS storage providers`.

---

### Task 16: MinimalERC20Client

**Files:**
- Create: `typescript/src/erc20/client.ts`, `typescript/src/erc20/index.ts`
- Test: `typescript/tests/erc20.test.ts`

**Interfaces (Produces):**

```ts
export class MinimalERC20Client extends ContractBase {
  constructor(client: PublicClient, tokenAddress: string, walletProvider?: WalletProvider | null);
  decimals(): Promise<number>; symbol(): Promise<string>;
  balanceOf(account: string): Promise<bigint>; allowance(owner: string, spender: string): Promise<bigint>;
  approve(spender: string, amount: bigint): Promise<TxResult>;
}
```

Uses `erc20Abi` from `src/abis`. Views through `callWithRetry`; `approve` through `sendTx`; read-only write → the ContractBase error.

**Reference:** `python/bnbagent/erc20/client.py`.

- [ ] **Steps 1–5:** tests (decimals/symbol/balanceOf/allowance decode via mockTransport; approve broadcasts and returns TxResult; write without wallet throws read-only message) → implement → PASS → commit `feat(ts): minimal ERC20 client`.

---

### Task 17: ERC-8004 — models, agentUri, contract, agent

**Files:**
- Create: `typescript/src/erc8004/models.ts`, `typescript/src/erc8004/agentUri.ts`, `typescript/src/erc8004/contract.ts`, `typescript/src/erc8004/agent.ts`, `typescript/src/erc8004/constants.ts`, `typescript/src/erc8004/index.ts`, `typescript/src/constants.ts` (`SCAN_API_URL = "https://www.8004scan.io/api/v1"`), `typescript/src/version.ts`
- Test: `typescript/tests/models.test.ts`, `typescript/tests/agentUri.test.ts`, `typescript/tests/erc8004Contract.test.ts`, `typescript/tests/erc8004Agent.test.ts`

**Interfaces (Produces):**

```ts
// models.ts
export class AgentEndpoint {
  constructor(opts: { name: string; endpoint: string; version?: string; capabilities?: string[] });
  static readonly A2A_WELL_KNOWN_PATH = "/.well-known/agent-card.json";
  static a2a(baseUrl: string, opts?: { version?: string; capabilities?: string[] }): AgentEndpoint;
  static mcp(url: string, opts?: { version?: string; capabilities?: string[] }): AgentEndpoint;
  toDict(): Record<string, unknown>;  static fromDict(d: Record<string, unknown>): AgentEndpoint;
}
// agentUri.ts
export const AgentURIGenerator: {
  generateRegistrationFile(opts: { name; description; endpoints; image?; agentId?; identityRegistry?; chainId?; supportedTrust? }): Record<string, unknown>;
  calculateFileHash(file: Record<string, unknown>): `0x${string}`;
  generateAgentUri(opts): string;                        // data:application/json;base64,<b64(canonicalJson)>
  encodeRegistrationFileToBase64(file): string;
  decodeRegistrationFileFromBase64(s: string): Record<string, unknown>;
};
// contract.ts
export class ContractInterface extends ContractBase {
  constructor(opts: { client; contractAddress; walletProvider; paymaster?; receiptTimeout? });
  registerAgent(agentUri: string, metadata?: Array<{key: string; value: string}>): Promise<{success: true; transactionHash: string; agentId: number | null; receipt}>;
  getAgentInfo(agentId: number): Promise<{agentId; agentAddress; agentWallet; owner; agentURI}>;
  getMetadata(agentId: number, key: string): Promise<string>;
  setMetadata(agentId: number, key: string, value: string): Promise<{success: true; transactionHash; receipt}>;
  setAgentUri(agentId: number, agentUri: string): Promise<{success: true; transactionHash; receipt}>;
}
// agent.ts
export class ERC8004Agent {
  static create(opts: { walletProvider: WalletProvider; network?: string | NetworkConfig; debug?: boolean }): Promise<ERC8004Agent>;
  // async factory (chain-id assertion needs RPC); ctor private
  generateAgentUri(opts): string;
  registerAgent(agentUri: string, metadata?): Promise<{success; transactionHash; agentId; receipt; agentURI}>;
  getAgentInfo(agentId: number): Promise<...>; getAllAgents(limit?, offset?): Promise<...>;   // 8004scan REST
  getLocalAgentInfo(name: string): Promise<{name; agentId; agentUri; ownerAddress} | null>;
  getMetadata / setMetadata / setAgentUri;
  static parseAgentUri(agentUri: string): Promise<Record<string, unknown> | null>;  // base64 + http(s) with SSRF guard
  get walletAddress(): string; get contractAddress(): string; get network(): Record<string, unknown>;
}
export function getErc8004Config(network?: string): Record<string, unknown>;  // ERC8004_REGISTRY_ADDRESS override
```

Key semantics (port exactly): `a2a()` appends well-known path unless already present, trims trailing `/`; endpoint must start `http(s)://`; registration file `type: "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"`, `registrations` only when agentId+registry+chainId all present, `agentRegistry: "eip155:<chainId>:<registry>"`; base64 payload is **canonicalJson**; `registerAgent` two-phase flow with `built_with` auto-injection (`https://github.com/bnb-chain/bnbagent-sdk#v<version>` — TS uses its own package version), metadata → `{metadataKey, metadataValue: utf8Bytes}`, agentId from executor result else `Registered` event; phase-2 failure → `ERC8004PartialRegistrationError` (txHash only when cause is `TransactionPendingError`); `parseAgentUri` HTTP path SSRF: blocked hostnames `metadata.google.internal|metadata.goog|169.254.169.254`, `dns.lookup` then reject private/loopback/link-local/reserved/CGNAT `100.64.0.0/10` (use `node:net.BlockList`), request the resolved IP with original Host header, no redirects, 10s timeout, 1 MB streamed cap; ANY failure → null, never throws. `getAllAgents`: GET `${SCAN_API_URL}/agents?chain_id=&limit=&offset=`, limit cap 100, 30s timeout, failure → `Error("8004scan API request failed: ...")`.

**Reference:** `python/bnbagent/erc8004/*.py`, `python/bnbagent/constants.py`; tests `test_models.py`, `test_agent_uri.py`, `test_erc8004_contract.py`, `test_sdk.py`.

- [ ] **Steps 1–5:** port the four test modules (incl. DoS cap >1 MB → null, CGNAT `100.100.100.200` → null, partial-registration with/without pending txHash, chain-id mismatch at create, built_with injection + user-override respected, nonce-retry and preflight cases run through a REAL LocalExecutor over mockTransport) → implement → PASS → commit `feat(ts): ERC-8004 identity registry client`.

---

### Task 18: x402 — budget, payer types, signer

**Files:**
- Create: `typescript/src/x402/budget.ts`, `typescript/src/x402/payer.ts`, `typescript/src/x402/signer.ts`, `typescript/src/x402/errors.ts`, `typescript/src/x402/index.ts`
- Test: `typescript/tests/x402Signer.test.ts`

**Interfaces (Produces):**

```ts
export class X402RecipientMismatchError extends Error {} export class X402AmountExceededError extends Error {}
export class X402BudgetExhaustedError extends Error {} export class X402PolicyError extends Error { cause?: unknown }
export class SessionBudgetTracker {
  constructor(caps?: Record<string, bigint>);
  capFor(token: string): bigint | null; spent(token: string): bigint;
  reserve(token: string, amount: bigint): void;      // throws X402BudgetExhaustedError; atomic (sync)
  rollback(token: string, amount: bigint): void;     // floored at 0, never throws
}
export interface TypedDataSigner { readonly address: string;
  signTypedData(domain, types, message): Promise<SignatureResult>; supports?(cap: string): boolean; }
export class X402Signer {
  constructor(wallet: TypedDataSigner, opts?: { maxValuePerCall?: Record<string, bigint>; sessionBudget?: Record<string, bigint> });
  get walletAddress(): string; get budget(): SessionBudgetTracker;
  signPayment(opts: { domain; types; message; expectedTo: string }): Promise<SignatureResult>;
}
export interface X402PaymentOption { network: string; scheme: string; asset: string; tokenName?: string;
  amount: bigint; payTo: string; transferMethod?: string; maxTimeoutSeconds?: number;
  preferred: boolean; requiresApproval: boolean; description?: string; }
export function paymentOptionFromCli(entry: Record<string, unknown>): X402PaymentOption;  // camelCase → fields
export interface X402Quote { url: string; description?: string; mimeType?: string;
  accepts: readonly X402PaymentOption[]; summary?: string; raw: Record<string, unknown>; }
export function quoteFromCli(data: Record<string, unknown>): X402Quote;
export interface X402PaymentResult { success: boolean; response: unknown; amount?: bigint;
  asset?: string; network?: string; payTo?: string; transaction?: string; }
export interface X402Payer {
  quote(url: string, opts?: { method?: string; body?: string }): Promise<X402Quote>;
  request(url: string, opts: { maxPayment: bigint; method?: string; body?: string }): Promise<X402PaymentResult>;
}
```

`signPayment` guard ORDER (port exactly): construction-time `supports(SIGN_TYPED_DATA)` gate (duck-typed absent `supports` passes) → L0 recipient (`message.to` string, case-insensitive vs expectedTo) → L1 per-call cap keyed by checksummed `domain.verifyingContract` → L1.5 `message.from` === wallet address (before reserve) → L2 `budget.reserve` → L3 sign; `PolicyViolation` → rollback + `X402PolicyError` with `cause`; ANY other throw → rollback + rethrow.

**Reference:** `python/bnbagent/x402/{signer,payer,budget,errors}.py`; tests `test_x402_signer.py`.

- [ ] **Steps 1–5:** port tests (happy path increments spent; mismatch cases budget-untouched; cap exceeded; budget accumulates then exhausts at exactly cap; PolicyViolation → X402PolicyError with cause + budget 0; non-policy error also rolls back; capability gate throws UnsupportedWalletOperation at construction; duck-typed signer without `supports` passes; interleaved-async budget correctness: fire two `signPayment` promises with a slow mock signer — exactly one succeeds, spent === cap, wallet invoked once) → implement → PASS → commit `feat(ts): x402 payment signer with layered guards`.

---

### Task 19: erc8183 — types + schema

**Files:**
- Create: `typescript/src/erc8183/types.ts`, `typescript/src/erc8183/schema.ts`
- Test: `typescript/tests/erc8183Schema.test.ts`

**Interfaces (Produces):**

```ts
export enum JobStatus { OPEN = 0, FUNDED = 1, SUBMITTED = 2, COMPLETED = 3, REJECTED = 4, EXPIRED = 5 }
export enum Verdict { PENDING = 0, APPROVE = 1, REJECT = 2 }
export const REASON_APPROVED: `0x${string}`;    // keccakOfText("OPTIMISTIC_APPROVED")
export const REASON_REJECTED: `0x${string}`;    // keccakOfText("OPTIMISTIC_REJECTED")
export const ZERO_REASON: `0x${string}`;        // 0x + 64 zeros
export const ZERO_ADDRESS: `0x${string}`;
export interface Job { id: bigint; client: `0x${string}`; provider: `0x${string}`; evaluator: `0x${string}`;
  description: string; budget: bigint; expiredAt: bigint; status: JobStatus; hook: `0x${string}`;
  deliverable: `0x${string}`; submittedAt: bigint; }
export const SCHEMA_VERSION = 1;
export class DeliverableManifest {
  constructor(opts: { version: number; jobId: number; chainId: number;
    contracts: Record<string, string>; response: { content: string; contentType: string };
    metadata?: Record<string, unknown> });
  manifestHash(): `0x${string}`;                  // keccakOfCanonicalJson(toDict()) — cross-SDK invariant
  verify(onChainHash: `0x${string}` | Uint8Array): boolean;
  toDict(): Record<string, unknown>;              // keys: version, job_id, chain_id, contracts, response, metadata (snake_case — wire format shared with Python!)
  static fromDict(d: Record<string, unknown>): DeliverableManifest;
}
export class JobDescription {
  constructor(opts: { version; negotiatedAt; task; terms; price; currency;
    quoteExpiresAt?; negotiationHash?; providerSig? });
  toDict(): Record<string, unknown>;              // wire keys snake_case: negotiated_at, quote_expires_at, negotiation_hash, provider_sig
  static fromDict(d): JobDescription;             // negotiated_at must be integer (reject string AND boolean); quote_expires_at int-or-null
  static fromStr(description: string): JobDescription | null;  // null for plain text / no version / bad JSON; throws on unsupported version
}
```

**CRITICAL:** wire-format dict keys stay `snake_case` (`job_id`, `chain_id`, `negotiated_at`, ...) — the JSON travels between SDKs and its canonical hash must match Python byte-for-byte. TS property names are camelCase; `toDict`/`fromDict` do the mapping.

**Reference:** `python/bnbagent/erc8183/{types,schema}.py`; tests `test_erc8183_schema.py`.

- [ ] **Steps 1–5:** port tests (round-trips, manifestHash === keccak of canonical JSON — pin one cross-checked constant computed via the Python one-liner, verify(), unsupported version, missing fields, type-strictness incl. boolean rejection, fromStr fallback-friendly nulls) → implement → PASS → commit `feat(ts): erc8183 types and deliverable/job schemas`.

---

### Task 20: erc8183 low-level clients — Commerce / Router / Policy

**Files:**
- Create: `typescript/src/erc8183/commerce.ts`, `typescript/src/erc8183/router.ts`, `typescript/src/erc8183/policy.ts`
- Test: `typescript/tests/erc8183Intents.test.ts`, `typescript/tests/erc8183Router.test.ts`, `typescript/tests/erc8183Policy.test.ts`

**Interfaces (Produces):** three classes extending `ContractBase`, common ctor `(client, contractAddress, walletProvider?, { paymaster? })`.

CommerceClient — writes (each builds `Intent{ name: <constant>, kwargs, call }` → `executeIntent`): `createJob({provider, evaluator, expiredAt, description, hook = ZERO_ADDRESS})` (post: fill `jobId` from result else decode `JobCreated` from receipt logs), `setProvider(jobId, provider, optParams = "0x")`, `setBudget(jobId, amount, optParams)`, `fund(jobId, expectedBudget, optParams)`, `submit(jobId, deliverable /* 32 bytes enforced */, optParams)`, `complete(jobId, reason = ZERO_REASON, optParams)`, `reject(...)`, `claimRefund(jobId)`. Views via `callWithRetry`: `getJob → Job` (tuple decode: index 9 = submittedAt, 10 = deliverable), `jobCounter`, `paymentToken`, `platformFeeBp`, `platformTreasury`, `jobHasBudget`, `getJobsBatch(jobIds) → (Job | null)[]` (multicall). Events: `getJobFundedEvents(fromBlock, toBlock?, provider?)`, `getJobCreatedEvents(...)`.

RouterClient — `registerJob(jobId, policy)`, `settle(jobId, evidence = "0x")`, `markExpired(jobId)`; views `commerce()`, `inflightJobCount()`, `jobPolicy(jobId)`, `policyWhitelist(addr)`, `paused()`; events `getJobRegisteredEvents`, `getJobSettledEvents` (int → Verdict), `getJobFinalisedEvents` (int → JobStatus).

PolicyClient — writes `dispute(jobId)`, `voteReject(jobId)` via intents; **admin ops `addVoter`/`removeVoter`/`setQuorum` deliberately via `sendTx`, never intents** (INVARIANT); views `check(jobId, evidence) → [Verdict, Hex]`, `submittedAt`, `disputed`, `rejectVotes`, `hasVoted`, `isVoter`, `disputeWindow`, `voteQuorum`, `disputeQuorumSnapshot`, `activeVoterCount`, `admin`, `commerce`, `router`; `getDeliverableUrl(jobId, { hintBlock? })` — tight ±10-block window with hint, 1000-block fallback, `JobInitialised` filtered by jobId, `optParams` UTF-8 JSON → `deliverable_url`; error containing `-32005`/`limit exceeded` → `RpcRangeLimitError`, others → warn + null.

Intent name constants: the 13 `erc8183.*` strings from Task 11's intents.ts.

**Reference:** `python/bnbagent/erc8183/{commerce,router,policy}.py`; tests `test_erc8183_intents.py`, `test_erc8183_router.py`, `test_erc8183_policy.py`.

- [ ] **Steps 1–5:** port tests using a `RecordingExecutor` injected via a stub wallet's `makeExecutor` (assert intent name + kwargs + call.functionName per write; jobId dual-sourcing; admin-ops-bypass-intents invariant; view decodes; event flattening; RpcRangeLimitError classification) → implement → PASS → commit `feat(ts): erc8183 commerce/router/policy contract clients`.

---

### Task 21: ERC8183Client facade + ERC8183Config

**Files:**
- Create: `typescript/src/erc8183/client.ts`, `typescript/src/erc8183/config.ts`, `typescript/src/erc8183/constants.ts`, `typescript/src/erc8183/index.ts`
- Test: `typescript/tests/erc8183Client.test.ts`, `typescript/tests/erc8183Config.test.ts`

**Interfaces (Produces):**

```ts
export const DEFAULT_APPROVE_FLOOR_UNITS = 100n;
export const ERC8183_PAYMASTER_CHAIN_IDS: ReadonlySet<number>;  // {97}
export class ERC8183Client {
  static create(opts: { walletProvider?: WalletProvider | null; network?: string | NetworkConfig; debug?: boolean }): Promise<ERC8183Client>;
  // async factory: builds clients, asserts RPC chainId === config chainId → Error("... chain_id mismatch ...")
  readonly commerce: CommerceClient; readonly router: RouterClient; readonly policy: PolicyClient;
  readonly address: `0x${string}` | null; readonly network: NetworkConfig;
  paymentToken(): Promise<`0x${string}`>;          // cached forever
  tokenDecimals(): Promise<number>; tokenSymbol(): Promise<string>;    // cached
  tokenBalance(address?: string): Promise<bigint>; tokenAllowance(owner, spender): Promise<bigint>;
  approvePaymentToken(spender: string, amount: bigint): Promise<TxResult>;
  createJob(opts: { provider?: string; expiredAt: bigint; description?: string; hook?: string; skipExpiryCheck?: boolean }): Promise<TxResult>;
  registerJob(jobId: bigint, policy?: string): Promise<TxResult>;
  setProvider(jobId, provider): Promise<TxResult>; setBudget(jobId, amount): Promise<TxResult>;
  fund(jobId: bigint, amount: bigint, opts?: { approveFloor?: bigint }): Promise<TxResult>;
  submit(jobId: bigint, deliverable: `0x${string}`, optParams: { deliverable_url: string; [k: string]: unknown }): Promise<TxResult>;
  cancelOpen(jobId, reason?): Promise<TxResult>; claimRefund(jobId): Promise<TxResult>;
  settle(jobId, evidence?): Promise<TxResult>; markExpired(jobId): Promise<TxResult>;
  dispute(jobId): Promise<TxResult>; voteReject(jobId): Promise<TxResult>;
  getJob(jobId): Promise<Job>; getJobStatus(jobId): Promise<JobStatus>;
  getDeliverableUrl(jobId, opts?: { hintBlock?: bigint }): Promise<string | null>;
  getVerdict(jobId, evidence?): Promise<[Verdict, `0x${string}`]>;
  inflightJobCount(): Promise<bigint>; disputeQuorumSnapshot(jobId): Promise<bigint>;
}
export class ERC8183Config { /* mirrors AgentConfig + storage/servicePrice/agentUrl; static fromEnv(storage?), fromEnvOptional() */ }
export function getErc8183Config(network?: string): Record<string, unknown>;
```

Port exactly: createJob dispute-window foot-gun guard (read `policy.disputeWindow()`; `expiredAt - now <= window` → Error mentioning `dispute_window`; read failure → warn + proceed; `skipExpiryCheck` bypass); evaluator AND default hook = router address; fund floor logic (`fundBundlesApproval === true` literal check skips allowance mgmt; allowance < amount → approve `max(amount, floor)` where floor = `100n * 10n**decimals` or caller's `approveFloor` (negative → Error, 0 → exact)); submit requires non-empty `optParams.deliverable_url`, encodes optParams as canonicalJson UTF-8 bytes; paymaster only when `usePaymaster && paymasterUrl && chainId ∈ {97}` (mainnet never sponsored). ERC8183Config: `ERC8183_{COMMERCE,ROUTER,POLICY}_ADDRESS` overlays only when network is a preset string; wallet convenience wrap — `privateKey` + `walletPassword` → `EVMWalletProvider`, then **both fields cleared to ""** (INVARIANT: no plaintext secrets after construction; also assert key not in `JSON.stringify(config)`); `privateKey` without password → `Error("wallet_password is required when using private_key. Pass wallet_provider= directly or set wallet_password.")`; password-only path loads-or-creates keystore; `fromEnv` reads `NETWORK`/`PRIVATE_KEY`/`WALLET_PASSWORD`/`WALLET_ADDRESS`/`ERC8183_SERVICE_PRICE` (default `"1000000000000000000"`)/`ERC8183_AGENT_URL`, defaults storage to `LocalStorageProvider.fromEnv()`; `WALLET_KIND` accepts `""`/`"evm"` only (others → `Error("Unknown wallet kind: ...")` — TWAK/MPC out of scope).

**Reference:** `python/bnbagent/erc8183/{client,config,constants}.py`; tests `test_erc8183_client.py`, `test_erc8183_config.py` (skip WALLET_KIND=twak cases).

- [ ] **Steps 1–5:** port tests (read-only construction; missing commerce → Error; chain-id mismatch at create; paymentToken cached single call; createJob defaults + window guard + bypass; fund matrix: sufficient/below-floor/above-floor/exact-0/custom/negative + fundBundlesApproval literal-true; delegation table; submit optParams compact-JSON bytes; config secret-clearing + repr + env matrix + fromEnvOptional) → implement → PASS → commit `feat(ts): ERC8183Client facade and config`.

---

### Task 22: Negotiation

**Files:**
- Create: `typescript/src/erc8183/negotiation.ts`
- Test: `typescript/tests/negotiation.test.ts`

**Interfaces (Produces):**

```ts
export const MAX_DESCRIPTION_BYTES = 4096;
export class DescriptionTooLongError extends Error {}
export const ReasonCode: { PRICE_TOO_LOW: "0x01"; DEADLINE_TOO_TIGHT: "0x02"; INCAPABLE: "0x03";
  AMBIGUOUS_TERMS: "0x04"; BUSY: "0x05"; UNSUPPORTED: "0x06"; TASK_TOO_LONG: "0x07" };
export class TermSpecification { /* deliverables, qualityStandards, successCriteria?, price?, currency?,
  evaluationRequired = true, evaluatorType = "uma_oov3"; toDict/fromDict (wire snake_case) */ }
export class NegotiationRequest { /* taskDescription, terms, contextUrls?, requestId?;
  toDict/computeHash (0x-keccak of canonicalJson)/toEnvelope/fromDict/fromEnvelope */ }
export class NegotiationResponse { /* accepted, terms?, estimatedCompletionSeconds?, quoteExpiresAt?,
  reasonCode?, reason?; computeHash EXCLUDES reason fields */ }
export class NegotiationResult { /* request, requestHash, response, responseHash, negotiationHash = "",
  providerSig = "", chainId?, verifyingContract?; get accepted(); toDict() omits empty sig fields */ }
export function buildJobDescription(negotiationResult: Record<string, unknown>, maxLength?: number): string;
export function parseJobDescription(description: string): JobDescription | null;
export interface MessageSigner { readonly address: string; signMessage(message: string): Promise<SignatureResult>; }
export class NegotiationHandler {
  static readonly MAX_QUOTE_TTL_SECONDS = 900;
  constructor(opts: { servicePrice: string; currency: string; estimatedCompletionSeconds?: number /* 120 */;
    requireQualityStandards?: boolean /* true */; walletProvider?: MessageSigner | null;
    quoteTtlSeconds?: number /* 300, int in (0,900] */; chainId?: number | null; verifyingContract?: string | null });
  static fromErc8183Client(client: ERC8183Client, opts: { servicePrice: string; ... }): Promise<NegotiationHandler>;
  negotiate(requestData: Record<string, unknown>, opts?: { price?: string; estimatedCompletionSeconds?: number }): Promise<NegotiationResult>;
}
```

Port exactly: `sanitizeForClaim` (`[`→`(`, `]`→`)`, strip control chars < 0x20 except `\t\n`); `_buildDescriptionContent` field set + checksummed verifyingContract; chain-binding anti-replay (chainId + verifyingContract INSIDE the keccak'd content); negotiationHash = `0x` + keccak of canonical content; providerSig = EIP-191 `signMessage(negotiationHash)`; signing failure → unsigned quote + warning (non-fatal); TTL validation (int-only — reject boolean, (0, 900]); final dry-run `buildJobDescription` → over-length rejects with TASK_TOO_LONG; buildJobDescription NEVER truncates (throws `DescriptionTooLongError`); rejected/missing price/currency → Error. Round-trip invariant test: strip `negotiation_hash`/`provider_sig` from the built description JSON, re-keccak → equals the signed hash.

**Reference:** `python/bnbagent/erc8183/negotiation.py`; tests `test_negotiation.py`.

- [ ] **Steps 1–5:** port full test module (incl. different-chainId → different hash; `fromErc8183Client` pulls paymentToken + commerce address; sign_message-failure warning path; malformed price rejections; quality-standards gate) → implement → PASS → commit `feat(ts): wallet-signed negotiation with chain-bound anti-replay`.

---

### Task 23: JobOps + fundedJobWatcher

**Files:**
- Create: `typescript/src/erc8183/jobOps.ts`
- Test: `typescript/tests/erc8183JobOps.test.ts`

**Interfaces (Produces):**

```ts
export const ERR_BUDGET_TOO_LOW = "budget_too_low"; /* + the other 10 error-code constants verbatim */
export interface OpResult { success: boolean; error?: string; error_code?: string;
  retryable?: boolean; tx_hash?: string; [k: string]: unknown }  // wire keys snake_case
export class ERC8183JobOps {
  static create(opts: { walletProvider?: WalletProvider | null; network?: string | NetworkConfig;
    providerAddress?: string; storageProvider?: StorageProvider | null;
    servicePrice?: bigint; agentUrl?: string | null }): Promise<ERC8183JobOps>;
  get agentAddress(): `0x${string}`; get erc8183Client(): ERC8183Client;
  submitResult(jobId: number, responseContent: string, metadata?: Record<string, unknown>): Promise<OpResult>;
  getJob(jobId: number): Promise<OpResult>; getJobStatus(jobId: number): Promise<OpResult>;
  getResponse(jobId: number): Promise<OpResult>; verifyJob(jobId: number): Promise<OpResult>;
  getPendingJobs(): Promise<OpResult>; getSubmittedJobs(): Promise<OpResult>;
}
export function fundedJobWatcher(jobOps: ERC8183JobOps,
  onFunded: (job: Record<string, unknown>) => unknown | Promise<unknown>,
  opts?: { interval?: number /* 30s */; stop?: AbortSignal }): Promise<void>;
```

Port exactly: keyless read path (`providerAddress` only → reads/poll work, `submitResult` → `Error("submit_result requires a signing wallet_provider")`); verifyJob gate order (FUNDED → assigned → not expired → submit-deadline vs disputeWindow → description parse fails closed → budget vs servicePrice → CLIENT_AS_EVALUATOR warning; expired quote does NOT block a funded job); size caps 5 MB / 256 KB with `ERC8183_MAX_RESPONSE_BYTES`/`ERC8183_MAX_METADATA_BYTES` env; `file://` URL rewrite to `<agentUrl>/job/<id>/response` (missing agentUrl → error naming `ERC8183_AGENT_URL`; `ipfs://` passthrough); error envelope sanitization (transient keyword list verbatim → `chain_unavailable` retryable; URL redaction `\S+:\/\/\S+` → `<redacted>`; `TransactionPendingError` → `tx_pending`, `retryable: false`, carries txHash; permanent rejections carry NO `retryable` key); pending-jobs cursor scan (startup full scan, then new-ids ∪ pendingOpenIds; OPEN tracked for re-scan); getResponse resolution chain + SUBMITTED/COMPLETED-vs-FUNDED classification (`chain_unavailable` vs `not_found`). Watcher: seen/retry sets; callback sync-or-async; retry on throw / `false` / `{retry: true}`; re-validate retries against chain before re-fire, drop when no longer FUNDED or expired; fire-once on any other return; poll errors logged and loop continues; stop via AbortSignal (resolve promptly on abort).

**Reference:** `python/bnbagent/erc8183/job_ops.py`; tests `test_erc8183_job_ops.py`.

- [ ] **Steps 1–5:** port full test module (largest suite — use fake timers for the watcher) → implement → PASS → commit `feat(ts): headless job ops + funded-job watcher`.

---

### Task 24: Public API assembly — Tier 1 index + subpath indexes + README

**Files:**
- Modify: `typescript/src/index.ts`; Create: any missing `src/*/index.ts`; Create: `typescript/README.md`
- Test: `typescript/tests/publicApi.test.ts`

**Interfaces (Produces):** Tier 1 (mirror `python/bnbagent/__init__.py`): `NetworkConfig, NETWORKS, resolveNetwork`, all error classes, `ERC8004Agent, AgentEndpoint`, `WalletProvider, EVMWalletProvider`, `ERC8183Client, JobStatus, Verdict`, `SigningPolicy, PolicyViolation`, `X402Signer`, `loadEnv`, `setMinGasPriceWei, minGasPriceWei, setDefaultReceiptTimeout, getDefaultReceiptTimeout`, `Paymaster, NonceManager, SCAN_API_URL`. Tier 2 subpaths re-export their package's full surface (erc8183 adds `CommerceClient, RouterClient, PolicyClient, Job, ERC8183JobOps, fundedJobWatcher, NegotiationHandler, ERC8183Config, DeliverableManifest, JobDescription, SCHEMA_VERSION`; utils adds `toRaw, fromRaw, SlidingWindowLimiter, RateLimitExceeded`; networks adds `getAddress, BNB_CHAIN_ADDRESSES, knownPaymentTokens, DeployedAddresses` + the 4 constants; storage adds the three providers; x402 adds `SessionBudgetTracker` + payer types + errors).

- [ ] **Steps 1–5:** test imports every Tier-1 name from `../src/index.js` and spot-checks each subpath index; `pnpm build` then `node -e "require('./typescript/dist/index.cjs')"` and `node -e "import('./typescript/dist/index.js')"` both load (dual-format smoke); README: install, quickstart (client-side job lifecycle + provider earn loop code), env var table, pointer to Python docs parity. Commit `feat(ts): public API surface + README`.

---

### Task 25: Examples

**Files:**
- Create: `typescript/examples/client/{happy,disputeReject,stalemateExpire,neverSubmit,cancelOpen}.ts`, `typescript/examples/client/_helpers.ts`, `typescript/examples/voter/{voteReject,watch}.ts`, `typescript/examples/x402/buyerDemo.ts`, `typescript/examples/security/e2e.ts`, `typescript/examples/README.md`

Direct ports of the Python examples (reference `python/examples/<same-path>.py` file-by-file; twak/ and serving examples excluded). `_helpers.ts`: `loadSettings` (loadEnv + env reads), `makePrimaryClient` (EVM wallet only), `expiryFor(client)` = disputeWindow + slack. `buyerDemo.ts`: in-process `node:http` server returning 402 with `accepts[]` → EIP-712 TWA → `X402Signer` → base64 `X-PAYMENT` retry → exit 0 on "ALL STEPS OK" (fully offline — this one is CI-runnable: add `pnpm -C typescript example:x402` script and run it in the task). `security/e2e.ts`: the 6 sign/recover assertion chains against the real testnet U-token domain (offline, CI-runnable). Client/voter examples hit live testnet — not run in CI; verify they typecheck via `tsc --noEmit`.

- [ ] **Steps 1–5:** port → `pnpm -C typescript exec tsc --noEmit -p examples` clean → run the two offline examples to completion → commit `docs(ts): example flows (client lifecycle, voter, x402 buyer, security)`.

---

## Final verification (after Task 25)

- [ ] `pnpm -C typescript test` — full suite green.
- [ ] `pnpm -C typescript build && pnpm -C typescript lint` — clean.
- [ ] Cross-SDK interop spot-checks (manual, offline):
  1. keystore: TS-encrypted keystore decrypts via Python `eth_account.Account.decrypt` and vice versa.
  2. hash parity: same `DeliverableManifest` dict → identical `manifest_hash` in both SDKs (incl. one manifest containing non-ASCII content).
  3. negotiation: Python `NegotiationHandler` quote verifies through the TS round-trip re-keccak (and vice versa).
- [ ] Update root `README.md` + `ARCHITECTURE.md` with the `typescript/` package (one section each).
- [ ] Invoke superpowers:finishing-a-development-branch.
