# Testnet flows (opt-in)

The chain workflows are fully tested against in-memory ports and SDK mocks. These scripts run the
same flows against **BSC testnet** once you have a funded testnet wallet. They are skipped by
default and never run in CI.

## Setup

1. Install the SDK adapter: `pip install -e 'eval[bnbagent,evm]'` (or the PyPI `bnbagent`).
2. Put credentials in the git-ignored `.env` at the repository root (never commit it):

   ```bash
   NETWORK=bsc-testnet
   WALLET_KIND=evm            # SDK convention (evm | twak | turnkey)
   PRIVATE_KEY=...            # testnet key only
   WALLET_PASSWORD=...        # keystore password (SDK EVMWalletProvider)
   STORAGE_PROVIDER=ipfs      # needed by the S2 flows: ipfs (Pinata-compatible) | local
   STORAGE_API_KEY=...        # if STORAGE_PROVIDER=ipfs
   ```

3. Get testnet BNB from a faucet and register an agent with the SDK
   (`ERC8004Agent.register_agent`, gas-free on BSC testnet via the MegaFuel paymaster).

## Flows

| Test | Needs | What it does |
|---|---|---|
| `test_s1_evaluate_publish_read_back` | `EVAL_TESTNET_AGENT_ID` (an agent you own) | Evaluate → publish the metrics entry with `set_agent_uri` → read it back |
| `test_s2_client_review_dry_run` | `EVAL_TESTNET_JOB_ID` (a submitted job) | DisputeAdvisor dry run (no transaction) |
| `test_s2_provider_gate_on_funded_job` | `EVAL_TESTNET_PROVIDER_JOB_ID` (a funded job you provide, whose description pins `eval_suite:<uri>#<hash>` in `success_criteria`) | EvalGate: work → evaluate → submit with evidence |

From the package directory:

```bash
set -a; source ../.env; set +a
EVAL_TESTNET=1 EVAL_TESTNET_AGENT_ID=<id> pytest -m testnet tests/testnet -v
```

## Attesting with SDK wallets

The SDK's default signing policy refuses unknown EIP-712 domains. To sign evaluation
attestations with an SDK wallet, extend the policy for the attestation domain
(`name="bnbagent-eval"`, `version="1"`, the commerce or registry contract), e.g.
`SigningPolicy.strict_default().extend(domain_allowlist={(chain_id, contract)},
primary_type_allowlist={"Attestation"})`. SDK wallets cannot sign raw digests, so the EIP-191
fallback is unavailable with them; EIP-712 (the default) works.

The same flows from the command line (`run --stage registration`, `publish`, `review`, `watch`)
are in [ONCHAIN.md](ONCHAIN.md).
