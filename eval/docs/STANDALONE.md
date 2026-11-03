# Using bnbagent-eval without BNB

The evaluation engine does not depend on BNB Chain, the bnbagent SDK or any blockchain: it
evaluates any agent, model or framework, on any EVM chain or none. The [user guide](USER_GUIDE.md)
covers evaluation itself and applies unchanged; this guide covers only what is specific to use
without BNB: what works, the install, signed results, on-chain metrics and paid evaluation on
other EVM chains, and the ERC-8004 / ERC-8183 workflows without the SDK.

## 1. What works without BNB

| Feature | Without BNB | Notes |
|---|---|---|
| All 99 metrics, judges (OpenAI-compatible, Anthropic, Bedrock), third-party methods | yes | judge metrics need an LLM ([JUDGES.md](JUDGES.md)) |
| Live targets (HTTP, A2A, MCP, Python function), trace import, reports and exporters | yes | |
| Test generation, simulated users, red-teaming, eval maker, recommendation, calibration | yes | |
| Reproducible hashes and signed results (EIP-712 attestations) | yes | signing needs `[evm]`; no chain, no network (§3) |
| On-chain metrics (`onchain.*`) for **any EVM chain** | yes | through plain JSON-RPC (§4) |
| HTTP evaluation service, free or paid with x402 on **any EVM chain** | yes | `[server]`; your own token list (§5) |
| ERC-8004 / ERC-8183 workflows | with `[bnbagent]` on BNB Chain | or your own adapter (§6) |

CI checks this separation: `scripts/check_core_only.sh` runs the whole test suite in an install
without extras and checks that neither `bnbagent` nor `web3` is imported; a second job installs
`[standalone]`, checks that neither is installed, and runs `scripts/check_standalone.py`, one
example per row above.

## 2. Install

```bash
pip install bnbagent-eval                 # core: httpx, pyyaml, eth-hash
pip install 'bnbagent-eval[standalone]'   # evm, bedrock, format, server, crypto
```

Add other extras as needed ([USER_GUIDE.md](USER_GUIDE.md#4-install)), but not `[bnbagent]` or
`[all]`: both install the bnbagent SDK. The package contains no paths into the rest of its
repository, so a copy of the package directory installs on its own (`pip install ./eval`).

## 3. Results you can verify

Every response carries `result_hash`, a keccak hash over the canonical result. Anyone with the
same suite, cases and judge outputs gets the same hash, on any machine and supported Python
version. With a key, the result can also be signed with an EIP-712 signature; no chain or
network is involved:

<!-- doctest: requires=eth_account -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.chain.attestation import LocalKeySigner, sign, verify

key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # a public dev key; secret-scan: allow
suite = Suite(name="v", version="1", metrics=(MetricConfig(id="text.exact_match"),))
response = EvalClient().evaluate_sync(suite=suite, cases=[EvalCase(id="1", output="4", expected="4")])
signed = sign(response, LocalKeySigner(key))
print(verify(signed) == signed.attestation.evaluator)  # True: signature and hashes check out
```

`EvalClient(signer=...)` signs every response automatically. Never put a real key in code; load
it from your secret store.

## 4. On-chain metrics on any EVM chain

The `onchain.*` metrics (`tx_success`, `gas_efficiency`, `approval_safety`, `spend_policy`,
`balance_delta`, `intent_match`) read transaction steps in a trajectory. With a chain reader the
client fetches receipts first. The built-in reader speaks plain JSON-RPC to any EVM node, such as
Ethereum, Base, Arbitrum or a local Anvil:

```python
from bnbagent_eval import EvalClient
from bnbagent_eval.integrations.jsonrpc.client import JsonRpcClient
from bnbagent_eval.integrations.jsonrpc.reader import JsonRpcReader
from bnbagent_eval.ports import ChainPorts

reader = JsonRpcReader(JsonRpcClient("https://ethereum-rpc.example.com"), chain_id=1)
client = EvalClient(ports=ChainPorts(reader=reader))
```

Offline, or in tests, use the in-memory reader:

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.ports import ChainPorts
from bnbagent_eval.testing.fakes import FakeChainReader
from bnbagent_eval.types import Session, Trace, TxStep

tx = "0x" + "ab" * 32
reader = FakeChainReader()
reader.receipts[tx] = {"status": 1, "gasUsed": 21000, "blockNumber": 7, "logs": []}
step = TxStep(id="1", chain_id=1, to="0x" + "12" * 20, tx_hash=tx)
suite = Suite(name="o", version="1", metrics=(MetricConfig(id="onchain.tx_success"),))
response = EvalClient(ports=ChainPorts(reader=reader)).evaluate_sync(
    suite=suite, cases=[EvalCase(id="1", trajectory=Session(id="s", traces=(Trace(id="t", steps=(step,)),)))]
)
print(response.results[0].metrics[0].score)  # 1.0
assert response.results[0].metrics[0].score == 1.0
```

## 5. A paid service on any EVM chain

The free service needs nothing chain-specific ([SERVICE.md](SERVICE.md)). For payments, list the
payment tokens in a file and point the service at an x402 facilitator. Example: USDC on Base:

```yaml
# assets.yaml: tokens that support EIP-3009 transferWithAuthorization
- chain_id: 8453
  address: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
  symbol: USDC
  decimals: 6
  methods: [eip3009]
  eip712_domain: {name: USD Coin, version: "2"}
```

```bash
bnbagent-eval serve --pay-to 0x<your address> --chain-id 8453 --assets assets.yaml \
  --facilitator-url https://<x402 facilitator> --price-base 10000 --price-per-judge-call 1000
```

Prices are in the token's smallest unit: 10000 = 0.01 USDC. Unpaid requests get an HTTP 402
challenge that standard x402 clients understand. For full control, pass your own gate with
`--gate my_module:make_gate`. A gate is any object implementing the `PaymentGate` protocol in
`bnbagent_eval.server.payment_base`, for example an `X402Gate` with your own settlement.

## 6. ERC-8004 / ERC-8183 outside the bnbagent SDK

The workflows behind these standards are written against small interfaces
(`bnbagent_eval.ports`), not against BNB:
- `MetricsPublisher` publishes metrics to an agent registration;
- `EvalGate` checks a job deliverable before submission;
- `DisputeAdvisor` and `VoterAssistant` advise clients and voters.

The bnbagent SDK provides the implementations for BNB Chain. Elsewhere:

- **Read-only** (advisors, reading published metrics): `bnbagent_eval.integrations.jsonrpc.ports(rpc_url,
  chain_id=..., addresses={...})` works against any deployment of the contracts over plain
  JSON-RPC.
- **Writing** (publishing, submitting): implement `RegistryPort` / `CommercePort` / `StoragePort`
  for your stack. They are a handful of async methods each. Pass them as
  `ChainPorts(...)`, or on the command line as `--ports my_module:make_ports` for `publish` and
  `review`. The port contract tests in `tests/contract/test_ports.py` show what each method must
  do.

## 7. In CI

```yaml
# .github/workflows/agent-eval.yml (example)
- run: pip install 'bnbagent-eval[standalone]'
- run: bnbagent-eval run --suite evals/suite.yaml --cases evals/cases.jsonl --format md --out eval.md
  env:
    EVAL_JUDGE_PROVIDER: openai
    EVAL_JUDGE_MODEL: ${{ vars.JUDGE_MODEL }}
    EVAL_JUDGE_API_KEY: ${{ secrets.JUDGE_API_KEY }}
    EVAL_JUDGE_CACHE: sqlite:///.eval-cache/judge.db
```

The step fails when the verdict is `fail`. `eval.md` can be posted as a PR comment. `--csv` and
`--export otel|prometheus|mlflow` feed dashboards ([REPORTING.md](REPORTING.md)).

## 8. Good to know

- **The name.** The package is called `bnbagent-eval` because it ships with the bnbagent SDK.
  Nothing in the standalone features above contacts BNB Chain or requires a BNB account.
- **Defaults.** Network defaults such as `NETWORK=bsc-testnet` apply only to the bnbagent
  adapter. Everything else is configured with `EVAL_*` variables.
