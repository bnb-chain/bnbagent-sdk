# On-chain workflows

bnbagent-eval connects evaluations to the two BNB Chain agent standards that the bnbagent SDK
implements, plus paid evaluation over HTTP:

| Scenario | Standard | What happens | Entry points |
|---|---|---|---|
| S1 | ERC-8004 (identity) | a registration-stage evaluation is published in the agent's registration file | `MetricsPublisher`, `bnbagent-eval publish` |
| S2 | ERC-8183 (commerce) | the provider checks its deliverable before submitting; the client and voters re-evaluate it with the negotiated suite | `EvalGate`, `DisputeAdvisor`, `VoterAssistant`, `bnbagent-eval watch / review` |
| S3 | x402 | another agent pays for an evaluation on an eval service | `bnbagent-eval serve`, `EvalClient.remote` |

All three use the same call, `EvalClient.evaluate`, and the same `EvalResponse`; only the
`stage` and the `subject` (what is evaluated) differ. Opt-in testnet scripts for S1 and S2 are
in [TESTNET.md](TESTNET.md).

## Ports and adapters

The evaluation core never imports the SDK or web3. Chain access goes through small protocols
in `bnbagent_eval.ports`:

| Port | Used for |
|---|---|
| `RegistryPort` | ERC-8004: read the registration file, `set_agent_uri`, `set_metadata`, `owner_of` |
| `CommercePort` | ERC-8183: read jobs and deliverables (with the manifest hash check), `submit_result` |
| `PolicyPort` | OptimisticPolicy: dispute and vote windows, `dispute`, `vote_reject`, voter checks |
| `StoragePort` | upload / download JSON documents (suites, reports, manifests) |
| `SignerPort` | EIP-712 signatures for attestations |
| `ChainReaderPort` | receipts, transactions, balances and calls for `onchain.*` metrics |
| `SettlementPort`, `AssetCatalogPort`, `PayerPort` | x402 settlement, payment assets and the paying client |

Adapters implement them:

- `bnbagent_eval.integrations.bnbagent` (the `[bnbagent]` extra) wraps the SDK. `ports(network,
  wallet=None, storage=None)` returns a `ChainPorts` bundle with every port; without a wallet
  the bundle is read-only and writes fail with the error code `read_only`. `ports_from_env()`
  in `integrations.bnbagent.env` builds the same bundle from the SDK's environment variables
  (`NETWORK`, `WALLET_KIND`, `PRIVATE_KEY`, `WALLET_PASSWORD`, `STORAGE_PROVIDER`, ...), which is
  what the CLI uses. The SDK reads the wallet variables only in a private helper
  (`AgentConfig._wallet_kwargs_from_env`); `integrations.bnbagent.compat.wallet_env_kwargs`
  wraps it, and an SDK release without it fails with the code `sdk_api_missing` (then build the
  wallet yourself and pass `ports(network, wallet=...)`). SDK calls run in a thread and return
  neutral value types; SDK errors become `ChainIntegrationError` with a `code` and a
  `retryable` flag.
- `bnbagent_eval.integrations.jsonrpc` is a read-only JSON-RPC adapter without web3.
- `bnbagent_eval.testing.fakes` holds in-memory ports (`FakeRegistry`, `FakeCommerce`,
  `FakePolicy`, `MemoryStorage`, `FakeChainReader`, `FakeSigner`, ...). The examples below use
  them, so they run without a chain; swap in `ports(...)` for real use.

```python
from bnbagent import EVMWalletProvider
from bnbagent.storage import LocalStorageProvider

from bnbagent_eval.integrations.bnbagent import ports

wallet = EVMWalletProvider(password=...)          # see the SDK wallet docs
chain = ports("bsc-testnet", wallet=wallet, storage=LocalStorageProvider(...))
chain.registry, chain.commerce, chain.policy, chain.storage, chain.signer, chain.reader
```

Pass the bundle to the client to let `onchain.*` metrics read receipts and `erc8004` targets
resolve agents: `EvalClient(ports=chain, signer=chain.signer)`.

## Attestations and evidence

With a signer (`EvalClient(signer=...)`) every response gets an EIP-712 `attestation` over
`result_hash`, `suite_hash`, the subject, the verdict status, the aggregate score (basis points)
and the evaluator address (domain `bnbagent-eval`, version `1`, the commerce or registry
contract). Signing the verdict means a provider cannot turn a signed `fail` into `pass` in the
evidence; the advisors also flag evidence whose summary contradicts the signed verdict.
`RemoteBackend` and the advisors verify attestations with the `[evm]` extra. SDK wallets refuse
unknown EIP-712 domains by default; see [TESTNET.md](TESTNET.md#attesting-with-sdk-wallets) for
the signing-policy extension.

The `subject` binds a result to what was evaluated:

- `AgentSubject(chain_id, registry, agent_id, agent_version)` for ERC-8004;
- `JobSubject(chain_id, commerce, job_id, provider, output_hash)` for ERC-8183, where
  `output_hash` is the keccak of the delivered content.

Both are inside `result_hash` and the attestation, so a result cannot be reused for another
agent, job, chain or output.

## S1: publish metrics to ERC-8004

1. Evaluate with `stage="registration"` and an `AgentSubject` for your agent. The suite usually
   includes `ops.latency`, `ops.cost`, `ops.reliability` and an accuracy metric, often with
   `options.runs > 1` against a target so consistency and latency percentiles are measured.
2. Publish. `MetricsPublisher.publish` refuses anything but a passing registration evaluation
   (unless `force=True`) and checks that the subject is this agent on this registry. It uploads
   the full report to storage, adds or replaces the entry for this `suite_hash` in the
   registration file's `metrics` list (all other keys are kept), writes the file with
   `set_agent_uri`, and sets a metadata pointer `eval.metrics.<suite hash prefix>` to the report.

Each entry holds stable public names (`overall`, `pass_rate`, `accuracy`, `consistency`,
`latency_p95_ms`, `cost_per_call_usd`, `reliability`, `safety`), the verdict, the case count,
`suite_hash`, `result_hash`, the report URI and hash, `evaluated_at` / `expires_at` (90 days by
default) and the attestation. Only the agent owner can write the registration file.

<!-- doctest -->
```python
import asyncio

from bnbagent_eval import AgentSubject, EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.chain.erc8004 import MetricsPublisher
from bnbagent_eval.testing.fakes import FakeRegistry, MemoryStorage
from bnbagent_eval.types.case import AppMetrics

storage = MemoryStorage()
registry = FakeRegistry(storage=storage)  # chain 97, a synthetic registry address
registry.register(7, {"name": "price-agent", "description": "Quotes BNB prices."})

suite = Suite(
    name="price-agent-registration",
    version="1",
    metrics=(
        MetricConfig(id="text.exact_match", required=True),
        MetricConfig(id="ops.latency", value_target=1000, value_max=5000),
    ),
)
cases = [
    EvalCase(id=f"q{i}", output="612.40", expected="612.40", app_metrics=AppMetrics(latency_ms=800))
    for i in range(3)
]
subject = AgentSubject(chain_id=97, registry=registry.registry_address, agent_id=7)


async def main() -> None:
    response = await EvalClient().evaluate(
        stage="registration", suite=suite, cases=cases, subject=subject
    )
    assert response.verdict.action == "publishable"
    publisher = MetricsPublisher(registry, storage)
    result = await publisher.publish(7, response)
    print(result.success, result.entry["metrics"])
    # True {'overall': 1.0, 'pass_rate': 1.0, 'accuracy': 1.0, 'latency_p95_ms': 800.0}
    entries = await publisher.read(7, verify_reports=True)
    assert entries[0]["suite_hash"] == response.suite_hash and entries[0]["report_ok"]
    assert (await registry.read_registration(7))["name"] == "price-agent"  # other keys kept


asyncio.run(main())
```

From the command line, with a saved `EvalResponse` and the SDK environment variables:

```bash
bnbagent-eval run --suite suite.yaml --cases cases.jsonl --stage registration \
  --subject agent:97:<registry address>:7:v1.2 --out response.json
bnbagent-eval publish response.json --agent-id 7          # --force, --no-metadata
bnbagent-eval publish response.json --agent-id 7 --encrypt-to <x25519 public key hex>
```

`--subject agent:<chain_id>:<registry>:<agent_id>[:<agent_version>]` binds the response to the
agent; `publish` refuses a response without it, or for another agent, chain or registry
(`subject_mismatch`). `run` also accepts `--subject job:<chain_id>:<commerce>:<job_id>:<provider>`
for an ERC-8183 job: its `output_hash` is computed from the output of the single case, as
`EvalGate` does, and the option is rejected with several cases or a non-text output.

`--encrypt-to` (repeatable; `[crypto]` extra) encrypts the uploaded report to X25519 public
keys; the public metric entry stays readable.

## S2: evaluate ERC-8183 jobs

### Pin the suite at negotiation

The client and provider agree on a suite before the job is funded. Upload the suite and put one
entry in the job's `terms.success_criteria`:

```text
eval_suite:<suite uri>#<suite_hash>
```

`suite_criterion(uri, suite_hash)` builds it and `parse_suite_criterion(description)` reads it.
The SDK signs `success_criteria` with the provider's signature, so the suite cannot be swapped
later. Every party fetches the suite and checks it against the pinned hash
(`suite_from_job`); a mismatch is `suite_hash_mismatch`. Lint the suite with `--use negotiated`
and lock it first (see [SUITES.md](SUITES.md#lint)).

### Provider: `EvalGate`

`EvalGate` is an `on_funded` callback for the SDK's `funded_job_watcher`. For each funded job it
runs your work function in a background task, evaluates the deliverable with
`stage="pre_submit"` against the negotiated suite and a `JobSubject`, and submits only when the
verdict action is `submit`. The evidence (verdict, scores, hashes, subject and attestation,
at most 64 KB) goes into the deliverable metadata under `eval`; the full report goes to
`report_storage` when set.

- The work function receives a `JobView` and returns the content string, or
  `{"content": str, "trajectory": ..., "metadata": {...}}`. It may be sync or async.
- While the task runs the gate returns `{"retry": True}`; when it has submitted or given up it
  returns `None`, so the watcher never resubmits.
- Per-job state (`MemoryGateState` or `SqliteGateState`) survives restarts: work that was
  evaluated is never redone, and `max_attempts` (default 2) bounds retries after `fail` or
  `inconclusive`, and after a transient failure (suite or report storage unreachable). A
  `decline` action or a rejected suite abandons the job.
- The evaluation deadline is the job's `expired_at` minus `submit_margin_s` (300 s); jobs too
  close to expiry are abandoned.
- The SDK watcher counts every poll during the task as a retry, so give it
  `max_attempts=required_watcher_attempts(gate_attempts, work_seconds, interval)`.
- A retryable `submit_result` failure (e.g. a nonce clash) resends the same evaluated
  deliverable on the next poll; it does not run the work again or use up an attempt.
- `watch(gate, ...)` below wraps the gate for the SDK watcher. Passing the gate straight to the
  SDK's `funded_job_watcher` works on Python 3.12+; on 3.10 and 3.11 pass `gate.__call__` (the
  watcher awaits only coroutine functions and would drop the job otherwise).

```python
from bnbagent_eval import EvalClient
from bnbagent_eval.chain.erc8183 import EvalGate, SqliteGateState, required_watcher_attempts
from bnbagent_eval.integrations.bnbagent import ports, watch

chain = ports("bsc-testnet", wallet=wallet, storage=storage)
gate = EvalGate(
    chain.commerce,
    chain.storage,
    EvalClient(signer=chain.signer),
    produce_deliverable,                       # your work function
    state=SqliteGateState(".eval-cache/gate.db"),
    report_storage=chain.storage,
)
await watch(
    gate,
    network="bsc-testnet",
    wallet=wallet,
    interval=30,
    max_attempts=required_watcher_attempts(2, work_seconds=120, interval=30),
)
```

The CLI runs the same loop from environment variables:

```bash
bnbagent-eval watch --work mypkg.agent:produce_deliverable --max-attempts 2 --work-seconds 120
```

### Client: `DisputeAdvisor`

After submission the client asks whether to dispute. `DisputeAdvisor.advise(job_id)` checks the
job status and the dispute window (`submitted_at + dispute_window`), verifies the deliverable
manifest against the on-chain hash (a tampered or missing deliverable means `dispute`),
re-evaluates the content with `stage="review"` against the negotiated suite, and checks the
provider's evidence: same suite, bound to this job and output, valid attestation. The `Advice`
has `action` (`dispute`, `no_action`, `too_late`, `not_submitted`), `reasons`, `deadline`, the
`response` and `provider_evidence`.

### Voter: `VoterAssistant`

For a disputed job, `VoterAssistant.review(job_id)` checks that the signer is a whitelisted voter
who has not voted yet (otherwise the advice is `not_eligible`), re-evaluates with `stage="vote"`
and the negotiated suite, and returns `vote_reject` or `abstain`. The OptimisticPolicy has no approve vote: silence approves.
Problems with the provider's evidence (different suite, wrong binding, invalid attestation) are added to
the reasons.

`Advice.deadline` is `submitted_at + dispute_window + vote_window`. `PolicyPort` exposes no
dispute timestamp, so this is the latest time a vote can count (an upper bound). After it the
advice is `too_late` and nothing is evaluated. The deadline is `None` when the policy has no vote
window: the SDK 0.5 `OptimisticPolicy` has none.

<!-- doctest: requires=eth_account -->
```python
import asyncio
import json

from eth_account import Account

from bnbagent_eval import EvalClient, MetricConfig, Suite
from bnbagent_eval.chain.attestation import LocalKeySigner
from bnbagent_eval.chain.erc8183 import (
    DisputeAdvisor,
    EvalGate,
    VoterAssistant,
    suite_criterion,
)
from bnbagent_eval.engine.resolve import resolve_suite
from bnbagent_eval.ports import JobView
from bnbagent_eval.testing.fakes import FakeCommerce, FakePolicy, FakeSigner, MemoryStorage

storage = MemoryStorage()
commerce = FakeCommerce(storage=storage)
provider_key = LocalKeySigner(Account.create().key)  # throwaway key, never a real one

# Negotiation: the suite is uploaded and pinned in success_criteria.
suite = Suite(
    name="summary-job",
    version="1",
    metrics=(
        MetricConfig(id="format.json_valid", required=True),
        MetricConfig(
            id="format.json_schema",
            params={"schema": {"type": "object", "required": ["block", "summary"]}},
            required=True,
        ),
    ),
)


async def main() -> None:
    uri = await storage.upload(suite.to_dict(), "suite.json")
    criterion = suite_criterion(uri, resolve_suite(suite).suite_hash)
    description = {"task": "Summarise block 1 as JSON.", "terms": {"success_criteria": [criterion]}}
    commerce.add_job(
        JobView(
            job_id=1,
            client="0x" + "c1" * 20,
            provider=provider_key.address,
            status="funded",
            description=json.dumps(description),
        )
    )

    def produce(job: JobView) -> str:
        return '{"block": 1, "summary": "ok"}'

    gate = EvalGate(commerce, storage, EvalClient(signer=provider_key), produce)
    assert await gate(1) == {"retry": True}  # work + evaluation run in the background
    await gate.wait()
    print(gate.state.get(1))  # GateRecord(status='submitted', attempts=1, ...)
    assert (await commerce.get_job(1)).status == "submitted"

    advice = await DisputeAdvisor(commerce, FakePolicy(), storage, EvalClient()).advise(1)
    print(advice.action, advice.provider_evidence["attestation_ok"])  # no_action True
    assert advice.action == "no_action"
    assert advice.provider_evidence["suite_match"] and advice.provider_evidence["binding_ok"]

    voter = FakeSigner()
    policy = FakePolicy(voters=[voter.address], caller=voter.address)
    await policy.dispute(1)
    vote = await VoterAssistant(commerce, policy, storage, EvalClient(), voter).review(1)
    print(vote.action)  # abstain: the deliverable passes the negotiated suite
    assert vote.action == "abstain"


asyncio.run(main())
```

From the command line (dry run by default; `--act` sends the `dispute` or `vote_reject`
transaction when the advice says so):

```bash
bnbagent-eval review 42                 # client advice
bnbagent-eval review 42 --as voter      # voter advice (needs a wallet)
bnbagent-eval review 42 --as voter --act
```

The output is JSON with `action`, `reasons`, `deadline`, `provider_evidence`, `result_hash` and
`tx_hash`.

## S3: evaluation as a service with x402

An agent without its own judge, or one that wants an independent evaluator, can pay an eval
service per request. The service runs the same engine under an untrusted-suite policy and signs
its responses; see [SERVICE.md](SERVICE.md) for running one.

```bash
bnbagent-eval serve --pay-to 0x... --chain-id 97 --facilitator-url https://facilitator.example \
  --signer mypkg.keys:service_signer
```

The caller uses the same API with a remote backend. With the SDK's x402 payer the 402 challenge
is paid automatically, up to `max_payment` (atomic units of the payment token):

```python
from bnbagent_eval import EvalClient

client = EvalClient.remote(
    "https://eval.example",
    payer=wallet.make_x402_payer(),   # SDK X402Payer (PayerPort)
    max_payment=50_000,
)
response = await client.evaluate(stage="review", suite=suite, cases=cases, subject=subject)
```

`make_x402_payer()` is available on wallets that pay x402 themselves (the SDK's twak wallet);
other wallets raise `UnsupportedWalletOperation`. Any object with the SDK's synchronous
`request(url, *, max_payment, method, body)` works as a payer: it may return the SDK's
`X402PaymentResult` (the response body in `.response`) or an HTTP response. A payer around a
local key signs the challenge with the SDK's `X402Signer.sign_payment` (EIP-3009
`TransferWithAuthorization`) and resends the request with `X-PAYMENT`, as in the SDK's
`examples/x402/buyer_demo.py`.

Without a payer, a priced request raises `PaymentRequired` (with `.quote` and `.challenge`);
`await client.quote(request)` asks for the price first. Every remote response is checked:
`result_hash` and `response_hash`, the binding to this request, stage and subject, and (with
`[evm]`) the attestation. `EvalClient.remote(..., expected_evaluator="0x...")` also requires
the attestation to be signed by that address (an unattested response is then rejected). A
mismatch raises `AttestationError`. A response with an `attestation`
and a `JobSubject` is valid evidence for S2: a client or voter can use a paid evaluation from a
third party instead of their own judge.
