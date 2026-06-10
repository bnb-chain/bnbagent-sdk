# Paymaster setup — `setup_paymaster.py`

Sponsor-side maintenance script for a [NodeReal MegaFuel](https://docs.nodereal.io/docs/megafuel-policy-management)
gasless-transaction policy. It manages the **whitelists** on a paymaster policy
so that the contract calls our agents / voters / clients make are gas-sponsored.

> This is an operator tool, not part of the SDK runtime. You run it once when
> setting up a policy (and again whenever the whitelisted surface changes).

## 1. Prerequisites

- A NodeReal MegaFuel **policy** per network (created in the
  [MegaNode console](https://meganode.nodereal.io/)). You need its `policyUuid`.
- A NodeReal **API key** with access to that policy.
- `uv` (the repo's runner). Dependencies (`requests`, `web3`, `python-dotenv`)
  come from the project's `pyproject.toml`.

## 2. Configure secrets (`.env`)

Secrets live in the project-root `.env` (git-ignored). Copy the keys from
`.env.example` (section *6. SCRIPTS*) and fill them in:

```dotenv
# NodeReal API key (https://meganode.nodereal.io/).
PAYMASTER_API_KEY=your_nodereal_api_key

# Per-network MegaFuel paymaster policy UUIDs.
PAYMASTER_TESTNET_POLICY_UUID=...
PAYMASTER_MAINNET_POLICY_UUID=...
```

The contract **addresses** and **method selectors** are not secrets — they live
in the script's `NETWORKS` / `CONTRACT_METHODS` blocks (see below).

## 3. Usage

```bash
uv run python scripts/setup_paymaster.py <network> <action>
```

Both args are optional — defaults are `testnet` / `list`.

| arg       | values                              | default   |
|-----------|-------------------------------------|-----------|
| `network` | `testnet`, `mainnet`                | `testnet` |
| `action`  | `list`, `add`, `remove`, `clear`    | `list`    |

| action   | effect                                                                       |
|----------|------------------------------------------------------------------------------|
| `list`   | Print policy info + every whitelist entry. Selectors are annotated with their method signature so you can eyeball them. Read-only. |
| `add`    | Add the configured targets (addresses + selectors) to their whitelists.      |
| `remove` | Remove the configured targets from their whitelists.                         |
| `clear`  | Remove **every** entry currently on the `CLEAR_TYPES` whitelists (`ToAccountWhitelist`, `ContractMethodSigWhitelist`). Does **not** touch the From-account whitelist. |

```bash
# inspect what's currently whitelisted on testnet
uv run python scripts/setup_paymaster.py testnet list

# push the configured whitelist to testnet
uv run python scripts/setup_paymaster.py testnet add

# wipe the to-account + method-sig whitelists on mainnet
uv run python scripts/setup_paymaster.py mainnet clear
```

Recommended flow for a fresh policy: `list` → `add` → `list` (verify).

## 4. What gets configured

`add` / `remove` operate on two whitelist types, both driven from the script's
config blocks.

### `ToAccountWhitelist` — sponsored contract addresses

Sourced from `NETWORKS[<network>]` (mirrors `bnbagent/config.py`).

| Contract                    | testnet                                      | mainnet                                      |
|-----------------------------|----------------------------------------------|----------------------------------------------|
| ERC-8004 IdentityRegistry   | `0x8004A818BFB912233c491871b3d84c89A494BD9e` | `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` ¹ |
| ERC-8183 AgenticCommerce    | `0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de` | `0xea4daa3100a767e86fded867729ae7446476eba6` |
| ERC-8183 EvaluatorRouter    | `0xd7d36d66d2f1b608a0f943f722d27e3744f66f25` | `0x51895229e12f9876011789b04f8698af06ccd6da` |
| ERC-8183 OptimisticPolicy   | `0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6` | `0x9c01845705b3078aa2e8cff7520a6376fd766de5` |

¹ Mainnet ERC-8004 registry is a placeholder until ERC-8004 ships on BSC mainnet.

### `ContractMethodSigWhitelist` — sponsored method selectors

Sourced from `CONTRACT_METHODS` (selector → signature). **19** selectors,
covering only methods a user / agent / voter / client actually sends as a
transaction. Selectors are computed from the ABIs via
`bytes4(keccak256(bytes(signature)))`.

**ERC-8004 IdentityRegistry** — agent self-management (6)

| selector     | method                                          |
|--------------|-------------------------------------------------|
| `0x1aa3a008` | `register()`                                    |
| `0xf2c298be` | `register(string)`                              |
| `0x8ea42286` | `register(string,(string,bytes)[])`             |
| `0x466648da` | `setMetadata(uint256,string,bytes)`             |
| `0x0af28bd3` | `setAgentURI(uint256,string)`                   |
| `0x2d1ef5ae` | `setAgentWallet(uint256,address,uint256,bytes)` |

**ERC-8183 AgenticCommerce** — full job lifecycle (8)

| selector     | method                                            |
|--------------|---------------------------------------------------|
| `0x41528812` | `createJob(address,address,uint256,string,address)` |
| `0xc9a84bb9` | `setProvider(uint256,address,bytes)`              |
| `0xdd4ae9d4` | `setBudget(uint256,uint256,bytes)`                |
| `0xd2e13f50` | `fund(uint256,uint256,bytes)`                     |
| `0x9e63798d` | `submit(uint256,bytes32,bytes)`                   |
| `0xd75bbdf3` | `complete(uint256,bytes32,bytes)`                 |
| `0x41dd26f5` | `reject(uint256,bytes32,bytes)`                   |
| `0x5b7baf64` | `claimRefund(uint256)`                            |

**ERC-8183 OptimisticPolicy** — dispute / voting (2)

| selector     | method               |
|--------------|----------------------|
| `0x86d6282c` | `dispute(uint256)`   |
| `0xfedf9462` | `voteReject(uint256)`|

**ERC-8183 EvaluatorRouter** — job routing / settlement (3)

| selector     | method                       |
|--------------|------------------------------|
| `0x51d5456d` | `registerJob(uint256,address)` |
| `0x39c2ebb9` | `settle(uint256,bytes)`      |
| `0x77fd00c5` | `markExpired(uint256)`       |

### Intentionally **not** whitelisted

Owner-only / DAO-ops governance methods are excluded — they are not
gas-sponsored end-user actions:

- OptimisticPolicy: `addVoter`, `removeVoter`, `setQuorum`, `transferAdmin`, `acceptAdmin`
- EvaluatorRouter / AgenticCommerce: `pause`, `unpause`, `setPlatformFee`,
  `setCommerce`, `setPolicyWhitelist`, `transferOwnership`, `renounceOwnership`,
  `initialize`, `upgradeToAndCall`
- Contract-internal hooks (called by the contracts, never by an EOA):
  `onSubmitted`, `afterAction`, `beforeAction`

## 5. Extending the whitelist

- **New sponsored contract** → add its address to each network in `NETWORKS`,
  then include it in `build_targets()`'s `ToAccountWhitelist` list.
- **New sponsored method** → add a `selector: "signature"` entry to
  `CONTRACT_METHODS`. Compute the selector with
  `bytes4(keccak256(bytes(signature)))` (e.g. `eth_utils.function_abi_to_4byte_selector`).
  Keep this README's tables in sync.
- **Sponsor specific sender addresses** → uncomment `FromAccountWhitelist` in
  `build_targets()` and enable `fromWhitelistEnabled` on the policy.

After any change, run `... list` to confirm the live policy matches.

## Reference

- pm_addToWhitelist — https://docs.nodereal.io/reference/pm-addtowhitelist
- pm_rmFromWhitelist — https://docs.nodereal.io/reference/pm-rmfromwhitelist
- pm_getWhitelist — https://docs.nodereal.io/reference/pm-getwhitelist
- pm_getPolicyByUuid — https://docs.nodereal.io/reference/pm-getpolicybyuuid
- MegaFuel policy management — https://docs.nodereal.io/docs/megafuel-policy-management
