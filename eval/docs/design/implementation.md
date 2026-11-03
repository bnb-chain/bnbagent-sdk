# Implementation design

How the package is built: repository placement, package layout, dependencies, the core types and
the design of the metric framework, judges, engine, client, chain workflows and HTTP service. It
implements [architecture.md](architecture.md) and [metric-catalog.md](metric-catalog.md). User-facing
behaviour is in the guides ([USER_GUIDE.md](../USER_GUIDE.md) and the topic guides); maintainer
workflows are in [MAINTAINER_GUIDE.md](../MAINTAINER_GUIDE.md).

## 1. Repository placement

**Development** happens in the BNB-agent-eval repository:

```
BNB-agent-eval/
├── eval/                       ← the package; exactly what becomes bnbagent-sdk/eval/
│   └── ci/github-eval-ci.yml   ← becomes bnbagent-sdk/.github/workflows/eval-ci.yml
├── third_party/bnbagent-sdk/   ← the SDK as a pinned git submodule (reference, adapter tests)
├── docs/                       ← requirements, research, history, MR text
├── .env.example                ← local configuration template (.env is git-ignored)
├── ci/gitlab-ci.example.yml    ← inactive GitLab CI example
└── scripts/                    ← verify.sh (full local gate), check_secrets.py (secret / PII scan), audit_catalog.py
```

The SDK is consumed as a dependency (`bnbagent` from PyPI through the `[bnbagent]` extra), never
copied. The submodule serves as source reference and for testing the adapter against the pinned SDK
(`[tool.uv.sources]` in `pyproject.toml`).

**Destination:**

```
bnbagent-sdk/
├── python/  typescript/  abis/       (existing, untouched)
├── eval/                              ← bnbagent-eval distribution (Apache-2.0, NOTICE)
│   ├── pyproject.toml  README.md  LICENSE  NOTICE  CHANGELOG.md
│   ├── bnbagent_eval/  schemas/  tests/  examples/  benchmarks/  scripts/  ci/
│   └── docs/
└── .github/workflows/eval-ci.yml      ← new (existing workflows untouched)
```

## 2. Package layout

```
bnbagent_eval/
├── __init__.py              # Tier-1 exports (§8); importing it pulls in no extra, no web3, no network
├── _version.py  _optional.py (require(module, extra))  _jsonschema.py  config.py  exceptions.py
├── hashing.py  logging_utils.py  privacy.py (redaction, hash-only reports, X25519 sealed-box encryption, [crypto])
├── net.py                   # shared egress guard: public-IP check, pinned transport, response size caps, data: URIs
├── ports.py                 # L1: port Protocols + neutral value types + the ChainPorts bundle (§9.0)
├── testing/                 # public test helpers: fakes.py (in-memory ports), FakeJudge
├── types/
│   ├── case.py              # EvalCase, Message, ContentPart, Artifact, Context, AppMetrics
│   ├── trajectory.py        # Session, Trace, LlmStep / ToolStep / AgentStep / DelegationStep / HandoffStep / TxStep
│   ├── subject.py           # AgentSubject, JobSubject
│   ├── target.py            # TargetRef: callable | http | a2a | mcp | erc8004 (+ simulated_user)
│   ├── options.py           # RunOptions, Budget, Privacy
│   ├── judge.py             # JudgeConfig, default model ids per provider
│   ├── simulation.py        # StressAxis, FailureMode, CompetencyLevel, Persona, Scenario
│   ├── result.py            # MetricResult, ErrorInfo, RunResult, CaseResult, Aggregate, Verdict, Cost
│   ├── request.py           # EvalRequest, EvalResponse, Provenance, Attestation, Stage
│   ├── suite.py             # Suite, MetricConfig, PassRule, SuiteLock, Benchmark
│   └── migrate.py           # schema_version migrations
├── metrics/
│   ├── base.py              # MetricSpec, MethodSpec, MetricContext, BaseMetric, Metric protocol
│   ├── judge_base.py        # JudgeMetric, native_method, truncation, judge limits
│   ├── registry.py          # register / get / list / describe, aliases, register_method, entry-point plugins (local only)
│   ├── tokens.py            # language-aware tokenization and normalization (CJK per character)
│   ├── text/ format/ ops/ quality/ safety/ rag/ conversation/ agent/ onchain/ code/ redteam/ custom/
│   └── vendors/             # ragas, deepeval, fmeval, agentcore, agentic, local_models (lazy, behind extras)
├── judges/
│   ├── base.py              # JudgeModel protocol, JudgeRequest, JudgeResponse
│   ├── providers/           # bedrock.py, openai_compat.py, anthropic.py, fake.py, _common.py (retries, in-flight limit)
│   ├── structured.py  fencing.py  cache.py  sampling.py
│   └── prompts/<category>/<metric>.py   # versioned PromptTemplates (VERSIONS = {"v1": ...}), shipped in the wheel
├── adapters/traces/         # base.py (TraceAdapter, registry, detect), native, otel_genai, agentcore_adot, langfuse,
│                            #   langsmith, smolagents, taubench, cx0, eb_habitat; render.py (trajectories for judges)
├── engine/
│   ├── resolve.py           # suite / dataset resolution, local $ref, lock, suite_hash, policy hook
│   ├── dataset.py           # load_cases, dataset_ref
│   ├── runner.py            # Runner: targets → metrics, concurrency, budget, deadline, cancellation
│   ├── targets.py           # Target protocol, CallableTarget, HttpTarget, from_ref(), erc8004 resolution
│   ├── agent_targets.py     # A2ATarget, McpTarget
│   ├── simulated.py         # SimulatedUserTarget
│   ├── budget.py            # BudgetTracker (atomic reservations)
│   ├── aggregate.py         # Welford, aggregation modes, pass rules, verdict precedence, stage actions
│   ├── seal.py              # result_hash / response_hash
│   └── compare.py           # compare(a, b, tolerances), aggregate_evaluations(...)
├── client.py                # EvalClient, EvalBackend, LocalBackend, RemoteBackend
├── analysis/                # confidence.py (shared with agent.confidence), insights.py, diagnosis.py, calibration.py
├── authoring/               # lint.py, eval_maker.py, recommend.py, catalog_doc.py (METRICS.md)
├── generation/              # rag.py, agentic/, filters.py, simulation/, error_injection.py, redteam.py
├── chain/                   # L4 workflows: pure logic over ports.py; no bnbagent / web3 imports
│   ├── attestation.py       # EIP-712 sign / verify, EIP-191 fallback, LocalKeySigner ([evm])
│   ├── manifest.py          # core re-implementation of the SDK DeliverableManifest hash (parity-tested)
│   ├── evidence.py          # EvalEvidenceV1 JSON + ABI encode / decode, budget trimming
│   ├── erc8004.py           # MetricsPublisher, RegistrationMetricsV1
│   ├── erc8183.py           # EvalGate, gate state stores, DisputeAdvisor, VoterAssistant, suite pin helpers
│   └── onchain.py           # TxStep enrichment through ChainReaderPort at a pinned block, calldata decoding
├── integrations/            # the only place that knows concrete chain SDKs
│   ├── bnbagent/            # [bnbagent]: ports over the SDK (registry, commerce, reader, signer, storage, watch,
│   │                        #   env) + compat.py (supported SDK range, feature detection)
│   └── jsonrpc/             # read-only: receipts / balances / logs over httpx (core); eth_call reads and calldata
│                            #   decoding need [evm] (eth-abi; minimal ABIs vendored as JSON)
├── server/                  # [server] (+[evm] for x402 verification)
│   ├── app.py               # create_app(...), EvalService
│   ├── config.py  runner.py  schemas.py
│   ├── policy.py            # UntrustedSuitePolicy (allow-list, limits)
│   ├── payment_base.py  payment.py   # PaymentGate, X402Gate (eip3009 + permit2-exact), pricing, quotes
│   ├── settlement.py        # facilitator settlement
│   └── store.py             # sqlite: jobs, idempotency, credits
├── reporting/               # json_report.py, markdown.py, console.py, csv.py, exporters/{otel,prometheus,mlflow}.py
└── cli.py                   # bnbagent-eval metrics | run | make | lint | publish | review | watch | serve | calibrate | schemas
```

Next to the package, `eval/schemas/` holds the language-neutral API link: `openapi.json` (service)
and one JSON Schema per contract (`EvalRequest`, `EvalResponse`, `Suite`, `RegistrationMetricsV1`,
`EvalEvidenceV1`, `Attestation`). `bnbagent-eval schemas` generates them from the dataclasses;
`bnbagent-eval schemas --check` fails when the committed copies are stale.

## 3. Dependencies and extras

The core depends on `httpx`, `pyyaml` and `eth-hash[pycryptodome]`; Python ≥ 3.10. The extras are
defined in [`pyproject.toml`](../../pyproject.toml); the user-facing list is in
[USER_GUIDE.md](../USER_GUIDE.md) and the third-party ones in [THIRD_PARTY.md](../THIRD_PARTY.md).
Some features need no extra: OpenAI-compatible and Anthropic judges (httpx), the agentic judge
method (a `JudgeModel` tool loop), trace adapters, A2A / MCP targets (JSON-RPC over httpx) and the
code sandbox (subprocess, or docker / podman when configured).

**Rules:**
1. Extras are imported only inside functions, through `_optional.require(module, extra)`, which
   raises `MissingExtraError` with the install command.
2. `import bnbagent_eval` imports no extra and no `bnbagent` / `web3`, and makes no network call
   (tested). **Only `bnbagent_eval.integrations.bnbagent` may import `bnbagent` or `web3`**
   (an import-linter contract).
3. Prompt templates are Python modules inside the package, so they ship in the wheel. Test fixtures
   are not shipped.

## 4. Core types (key fields)

All types are dataclasses (frozen for value types) and implement `to_dict()` / `from_dict()`, with
migration and unknown-field preservation.

```python
@dataclass(frozen=True)
class ContentPart:              # multimodal content for cases and judge requests
    type: Literal["text", "image", "file", "json"]
    text: str | None = None
    data: Any | None = None
    uri: str | None = None
    media_type: str | None = None
    hash: str | None = None

@dataclass(frozen=True)
class Artifact:
    uri: str
    hash: str
    media_type: str
    size: int | None = None
    name: str | None = None

@dataclass(frozen=True)
class EvalCase:
    id: str
    input: str | dict | tuple[ContentPart, ...] | None = None
    output: str | dict | tuple[ContentPart, ...] | None = None
    output_content_type: str = "text/plain"
    artifacts: tuple[Artifact, ...] = ()
    expected: Any = None                     # reference answer | labels | {"tool_calls": [...], "calls": [...], ...}
    contexts: tuple[Context, ...] = ()       # Context(text, timestamp?, source?)
    conversation: tuple[Message, ...] = ()
    trajectory: Session | None = None
    app_metrics: AppMetrics | None = None    # latency_ms, ttft_ms, tokens (in / out / cache), cost_usd, error
    language: str | None = None              # BCP-47
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

```python
@dataclass(frozen=True, kw_only=True)       # every step type is kw_only; common fields: id, parent_id,
class TxStep(Step):                          #   agent_id, start_ms, end_ms, usage, error
    chain_id: int
    to: str
    value: int
    calldata: str
    intent: str | None
    decoded: DecodedCall | None              # contract, function, args (filled by chain.onchain when an ABI is given)
    tx_hash: str | None
    from_address: str | None
    status: int | None
    block_number: int | None
    gas_used: int | None
    logs: tuple[dict, ...] = ()
```

```python
@dataclass(frozen=True)
class JobSubject:
    chain_id: int
    commerce: str
    job_id: int
    provider: str
    output_hash: str                          # keccak(manifest.response.content)

@dataclass(frozen=True)
class AgentSubject:
    chain_id: int
    registry: str
    agent_id: int
    agent_version: str | None = None          # target fingerprint (e.g. hash of model + prompt + tools config)

@dataclass(frozen=True)
class RunOptions:
    runs: int = 1                             # target invocations per case (consistency / reliability)
    deadline: float | None = None             # unix seconds
    budget: Budget | None = None              # max_judge_calls, max_tokens, max_usd
    privacy: Privacy = Privacy()              # report_contents: "hashes" | "redacted" | "full"; redact: [patterns]
    language: str | None = None
    judge_override: JudgeConfig | None = None # only allowed when the suite does not pin a judge
    max_concurrency: int | None = None
```

```python
@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    name: str                                 # suite alias or metric_id
    method: str
    score: float | None
    value: float | None = None
    unit: str | None = None
    passed: bool | None = None
    threshold: float | None = None
    required: bool = False
    weight: float = 1.0
    reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None            # e.g. missing_input, missing_extra, metric_error, judge_parse_error,
                                              #   judge_unavailable, budget_exhausted, deadline, truncated,
                                              #   vendor_nan, sandbox_unavailable, target_error
    status: Literal["scored", "skipped", "error", "pending_human"] = "scored"
    provenance: MetricProvenance | None = None    # metric_version, method, prompt_hash, judge{provider, model, params},
                                                  #   samples, judge_source live | cache, truncated

@dataclass(frozen=True)
class CaseResult:
    case_id: str
    metrics: tuple[MetricResult, ...]
    score: float | None
    passed: bool | None
    runs: tuple[RunResult, ...]               # per target run: output_hash, app_metrics, error
```

```python
@dataclass(frozen=True)
class MetricConfig:
    id: str
    name: str | None = None
    method: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    threshold: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_target: float | None = None
    weight: float = 1.0
    required: bool = False
    allow_truncation: bool = False            # see §7.3

@dataclass(frozen=True)
class PassRule:
    aggregation: Literal["weighted_average", "mean", "min", "max"] = "weighted_average"
    min_aggregate: float = 0.7
    min_pass_rate: float = 1.0
    inconclusive_band: float = 0.0
    max_error_rate: float = 0.0               # share of errored / skipped metric results tolerated (required ones never)
    on_inconclusive: Literal["retry", "decline", "submit"] = "retry"   # pre_submit stage

@dataclass(frozen=True)
class Suite:
    name: str
    version: str
    metrics: tuple[MetricConfig, ...]
    judge: JudgeConfig | None = None
    pass_rule: PassRule = PassRule()
    lock: SuiteLock | None = None             # {metric_id (name if repeated): (metric_version, prompt_hash)}; `bnbagent-eval lint --lock`
    description: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)
    def suite_hash(self) -> str: ...          # over the resolved suite ($refs inlined) + lock + judge
```

## 5. Metric framework

```python
@dataclass(frozen=True)
class MethodSpec:
    name: str                                 # native | rule | ragas | deepeval | fmeval | agentcore | model | agentic | ...
    kind: Kind
    required_inputs: frozenset[str]
    optional_inputs: frozenset[str] = frozenset()
    inverted: bool = False
    raw_range: tuple[float, float] = (0.0, 1.0)
    extra: str | None = None
    extra_module: str | None = None           # the module whose absence means the extra is missing
    service_allowed: bool = True              # False for custom.python, code.*, agentic, model downloads (policy default)

@dataclass(frozen=True)
class MetricSpec:
    id: str
    category: str
    description: str
    methods: tuple[MethodSpec, ...]           # first = default
    params_schema: Mapping[str, Any]
    default_threshold: float | None
    unit: str | None = None
    version: str = "1"
    aliases: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()             # provenance ("agentsight:FactualCorrectnessEvaluator", ...)
    value_limits: bool = False                # value_min / value_max / value_target apply (lint warns otherwise)
```

**`BaseMetric.evaluate` template:**
1. **Input check** against the selected method's required inputs. A missing input gives `status=skipped, error=missing_input`.
2. **Budget and deadline check.** Before spending, it reserves the estimated judge calls from the run budget.
   If they can't be reserved, the result is `skipped` with `budget_exhausted` or `deadline`.
3. **Truncation** of oversized fields (§7.3). It sets the `truncated` provenance flag.
4. **Dispatch** to the method.
5. **Normalization:** maps `raw_range` to [0, 1], inverts if the method says so, and clamps.
6. **Threshold:** `passed` is set when a threshold applies.
7. **Exceptions** become `ErrorInfo(metric_error)`, and the result is still returned.

**`MetricContext`** (passed to every `evaluate`) carries the metric config, method and validated
params, run options, the resolved judge (§6), the shared budget, deadline, language
(`case.language` or `options.language`), the chain reader (from `EvalClient(ports=...)`, for
`onchain.*`), the judge cache, the storage port, the stage, the target runs, the `EvalConfig`, an
injectable clock and a logger.

**`custom.python`** references a callable by import path (`params: {fn: "package.module:attr"}`)
or by a name registered with `register_python_metric(name, fn)`. Local mode only; denied by the
service policy.

**`JudgeMetric`:**
- Renders the versioned template and selects the input mode (auto-detection as in AgentSight).
- Appends `domain_rubric`.
- Fences untrusted fields with a **deterministic nonce**: `keccak(case_hash, metric_id, sample_idx, salt)`, with the salt recorded in provenance.
- Makes k judge calls, then validates and maps labels to scores. It aggregates with the lower median (scores) or a majority vote (labels; ties go to the lower score).
- Records usage and cost.

Concrete metrics override the scoring hook where the source had special logic, e.g. conciseness
weights or execution-chain links.

**Registry.**
- `registry.get(id_or_alias) -> (MetricSpec, method)`.
- `registry.register_method` attaches a method (e.g. a vendor method) to a canonical metric.
- Third-party metrics register through the entry point `bnbagent_eval.metrics`. They load only in
  local mode, never on the server.

## 6. Judge layer

- **`JudgeConfig`:** provider, model, temperature (0), max_tokens, samples, base_url, region, extra.
  API keys never live here. Default model ids per provider are in `types/judge.py`.
- **Precedence:** a suite-pinned judge > `options.judge_override` > `EvalClient(judge=…)` >
  `EvalConfig` (environment, then the config file). Setting an override while the suite pins a judge
  raises `SuiteError`. The server rejects pinned providers it cannot serve (`policy_denied`).
- **Providers:** `bedrock` (boto3 converse + tool use, in a worker thread), `openai` (chat
  completions with a JSON-schema response format, any compatible `base_url`), `anthropic` (messages +
  tool use) and `fake`. They share a retry policy (backoff on 429 / 5xx, no retry on context
  overflow), a per-provider in-flight limit, fallbacks for models that reject `temperature` or a
  forced `tool_choice`, and multimodal `ContentPart` support where the provider has it.
- **Structured parsing:** native structured output → JSON extraction → schema validation → one
  repair re-ask → `JudgeParseError`.
- **Cache:** the key is `keccak(provider, model, params, template_hash, canonical(unfenced inputs), sample_idx)`.
  Because it is independent of the nonce, replays hit the cache. Responses served from the cache
  are marked `judge_source=cache:<db_hash>`. Backends: memory and sqlite (`EVAL_JUDGE_CACHE`).
- **Embeddings:** `JudgeModel.embed()` for embedding methods, through Bedrock Titan or an
  OpenAI-compatible `/embeddings` endpoint.

Provider configuration and environment variables are in [JUDGES.md](../JUDGES.md).

## 7. Engine

### 7.1 Runner
1. **Resolve.** Load the suite (local `$ref`s), apply the lock, compute `suite_hash`, and resolve metrics and methods.
   Check extras, the judge and the policy (server). Validate params. Estimate judge calls against the budget.
   Any error here raises `SuiteError` / `RequestError` before work starts.
2. **Targets.** Build them from `TargetRef`: `CallableTarget`, `HttpTarget`, `A2ATarget` (handles async tasks:
   submitted → working → completed, with polling), `McpTarget`, `SimulatedUserTarget` (wraps another target and
   drives multi-turn conversations through the simulator), and `erc8004` (resolves the endpoint from the agent's
   registration through `RegistryPort`).
   - Streaming targets record TTFT.
   - Each case runs `options.runs` times, capturing output, trajectory, artifacts and app_metrics into `RunResult`.
3. **Score.** Run metric × case tasks under the concurrency limit and the shared budget. Cancel optional metrics when
   the deadline approaches. Results are ordered by (case order, metric order).
4. **Aggregate** (§7.4) → verdict (§7.5).
5. **Seal** (`engine/seal.py`). Compute `result_hash`, then provenance, then `response_hash`.
   **No signing happens in the engine.**

### 7.2 Budgets and cost
- `Budget(max_judge_calls, max_tokens, max_usd)` is enforced by an atomic reservation per judge call (`BudgetTracker`).
- The response carries `cost{judge_calls, tokens, usd_estimate}`.
- The service's price uses the same estimate: for each judge method, cases × samples (a metric's
  own `params.samples` takes precedence over the judge's).

### 7.3 Truncation policy
- A rendered field longer than the metric's `max_field_chars` (default 24 000 characters) keeps the
  head (70 %) and the tail (30 %) with a marker, and sets `provenance.truncated=true`.
- In the negotiated stages (`pre_submit`, `review`, `vote`) a truncated **required** metric stops
  with error `truncated` (so the verdict is `inconclusive`), unless the metric config sets
  `allow_truncation: true`.

### 7.4 Aggregation
- **Per metric:** count, errors, skipped, mean, std, min and max (Welford), pass_rate; for values, p50 / p95 / p99.
- **Per case:** the suite's aggregation mode (weighted_average / mean / min / max) over the scored metrics.
- **Suite:** `aggregate.score` = mean of the case scores; `pass_rate`; `error_rate` = (errored + skipped) / total metric results.
- Errored or skipped **required** metrics never fall silently out of the aggregate. They drive the verdict (§7.5).

### 7.5 Verdict
The precedence and stage actions are those in [architecture.md §7.4](architecture.md#74-verdict-and-actions).
They are implemented as a pure function `decide(aggregate, cases, pass_rule, stage) -> Verdict`, covered by a truth table.

### 7.6 Comparison across evaluators
- `compare(a, b, tolerances={"rule": 0, "judge": 0.15, "<metric>": x}) -> Comparison`. It returns per-metric agreement,
  verdict agreement, and binding checks (same `suite_hash` and subject).
- `aggregate_evaluations([responses], rule="median" | "quorum:k")` combines several evaluators.

## 8. `EvalClient`

**Tier-1 exports** (`bnbagent_eval/__init__.py`): `EvalClient`, `evaluate`, `EvalRequest`, `EvalResponse`, `EvalCase`,
`Message`, `Artifact`, `Suite`, `MetricConfig`, `PassRule`, `RunOptions`, `Budget`, `Privacy`, `JobSubject`, `AgentSubject`,
`TargetRef`, `Verdict`, `MetricResult`, `JudgeConfig`, `register_metric`, `register_python_metric`.

```python
class EvalClient:
    def __init__(self, backend: EvalBackend | None = None, *, config: EvalConfig | None = None,
                 judge: JudgeModel | JudgeConfig | None = None,
                 signer: SignerPort | None = None,          # attest responses after evaluation (L5)
                 ports: ChainPorts | None = None): ...      # reader for onchain.*, registry for erc8004 targets, storage
    @classmethod
    def remote(cls, base_url: str, *, payer: PayerPort | None = None, max_payment: int | None = None,
               timeout: float = 120, verify_attestation: bool = True, client: httpx.AsyncClient | None = None,
               expected_evaluator: str | None = None) -> EvalClient: ...  # max_payment: token atomic units
    async def evaluate(self, request: EvalRequest | None = None, /, **fields) -> EvalResponse: ...  # unknown fields → TypeError
    def evaluate_sync(self, *a, **kw) -> EvalResponse: ...        # runs in a worker thread if a loop is already running
    async def quote(self, request: EvalRequest) -> Quote: ...
    async def metrics(self) -> list[MetricSpec]: ...
    async def compare(self, a: EvalResponse, b: EvalResponse, **tol) -> Comparison: ...
```

`RemoteBackend`:
- Without a payer, a 402 response raises `PaymentRequired` (with the quote).
- With a payer, it calls the synchronous `payer.request(url, method="POST", body=…, max_payment=max_payment)` in a
  worker thread. The SDK payer owns the 402 → sign → retry cycle; `max_payment` (an integer) is required.
- It sends an idempotency key: the `Idempotency-Key` header, or the `idempotency_key` query parameter
  when the payer cannot set headers.
- It verifies the `result_hash` recomputation, the subject and suite binding, and (with `[evm]`) the attestation.
- `payer` is any object satisfying `PayerPort` (structurally identical to the SDK's `X402Payer.request`), so the SDK
  payer works directly.

Signing is a separate step: `chain.attestation.sign(response, signer)`, or `EvalClient(..., signer=signer)`, which
signs in the L5 client after the engine has returned.

## 9. Chain workflows (`chain/`), ports and adapters

### 9.0 Ports (`bnbagent_eval/ports.py`, L1)
The workflows depend only on these Protocols. Port methods that touch the chain or storage are
async; adapters wrap sync SDK calls in worker threads.

| Port | Methods | bnbagent adapter | jsonrpc adapter |
|---|---|---|---|
| `RegistryPort` | `get_agent_uri`, `read_registration`, `set_agent_uri`, `set_metadata`, `get_metadata`, `owner_of`; `chain_id`, `registry_address` | `ERC8004Agent` (+ storage for `ipfs://`) | reads only |
| `CommercePort` | `get_job -> JobView`, `get_deliverable_url`, `get_deliverable -> DeliverableView`, `submit_result -> SubmitResult`; `chain_id`, `commerce_address` | `ERC8183Client`, `ERC8183JobOps`, `DeliverableManifest` | reads only |
| `PolicyPort` | `dispute_window`, `vote_window`, `is_disputed`, `is_voter`, `has_voted`, `dispute`, `vote_reject` | `PolicyClient`, `ERC8183Client` | reads only |
| `StoragePort` | `upload(dict, name) -> uri`, `download(uri) -> dict`, `exists(uri)` | `StorageProvider` | HTTPS / IPFS-gateway download only |
| `SignerPort` | `address`, `supports(cap)`, `sign_typed_data`, `sign_message` | `WalletProvider` | – (`LocalKeySigner` from `[evm]`) |
| `ChainReaderPort` | `block_number`, `get_transaction`, `get_receipt`, `get_balance`, `call`, `get_logs`; `chain_id` | web3 through the SDK | `eth_*` JSON-RPC over httpx |
| `SettlementPort` | `authorization_state`, `settle` | SDK wallet transaction | facilitator over HTTP |
| `AssetCatalogPort` | `assets(chain_id) -> [AssetSpec]` | `bnbagent.networks` | static list (`serve --assets`) |
| `PayerPort` | `request(url, method, body, max_payment)` | SDK `X402Payer` (used directly) | – |

Also provided:
- **Neutral value types.** The workflows never see SDK types.
  - `JobView(job_id, client, provider, status, deliverable, submitted_at, expired_at, description, budget, evaluator)`.
  - `DeliverableView(url, content, content_type, metadata, manifest, manifest_hash, verified)`. `verified` means the
    manifest hash equals the on-chain `deliverable`; the jsonrpc adapter computes it with `chain/manifest.py`.
  - `SubmitResult(success, error_code, error, retryable, tx_hash, deliverable_url)`.
  - `AssetSpec(chain_id, address, symbol, decimals, methods, eip712_domain)`.
- **`testing/fakes.py`**: in-memory implementations used by the tests and the examples.
- **`integrations/bnbagent/compat.py`**: the supported SDK range and feature detection (e.g. whether
  `success_criteria` is signed). It fails with a clear error outside the range.
- **`ChainPorts`**: a dataclass bundling `registry, commerce, policy, storage, signer, reader, settlement, assets, payer, extras`.
  Any field may be `None` when unused.
- **Composition entry points:**
  - `integrations.bnbagent.ports(network="bsc-testnet", wallet=None, storage=None, **options)`; the SDK `wallet`
    becomes the `signer` and enables writes;
  - `integrations.jsonrpc.ports(rpc_url, *, chain_id, addresses, storage=None, ...)`, for read-only use.

The adapters and their environment are described in [ONCHAIN.md](../ONCHAIN.md).

### 9.1 Attestation
- `sign(response, signer, *, expires_in=30 days, domain=None) -> EvalResponse` (with `attestation`).
  It checks `signer.supports("sign.typed_data")`; without it, it signs the 32-byte EIP-712 digest with
  EIP-191 (`scheme="eip191"`); if neither is supported it raises `AttestationError`.
- `verify(response, *, expected_evaluator=None) -> evaluator address`. It checks the digest,
  signature, expiry and that the domain matches the subject.

### 9.2 ERC-8004: `MetricsPublisher`

```python
class MetricsPublisher:
    def __init__(self, registry: RegistryPort, storage: StoragePort | None = None, *,
                 public_names: Mapping | None = None, encrypt_to: tuple[str, ...] = ()): ...
    def build_entry(self, response: EvalResponse, *, report_uri=None, report_hash=None,
                    benchmark=None, ttl_days: int | None = 90) -> dict: ...
    async def publish(self, agent_id: int, response: EvalResponse, *, force: bool = False,
                      set_metadata_pointer: bool = True, rehost: str = "auto",
                      benchmark=None, ttl_days: int | None = 90) -> PublishResult: ...
    async def read(self, agent_id: int, *, verify_reports: bool = False) -> list[dict]: ...
```

`publish` performs these steps:
1. Require `verdict.status == "pass"` at stage registration, unless `force=True`.
2. Fetch the current file with `RegistryPort.read_registration`. The bnbagent adapter decodes `data:`, reads `https://`
   through the SDK's guarded fetch, and reads `ipfs://` through storage.
3. Upload the report (honouring the privacy option and `encrypt_to`).
4. Upsert the `metrics[]` entry by `suite_hash`, preserving every other key.
5. `rehost="auto"` re-uploads the file where it was hosted (IPFS stays IPFS); otherwise the file becomes a data URI.
6. Call `RegistryPort.set_agent_uri`, and optionally
   `set_metadata(agent_id, f"eval.metrics.{suite_hash[:10]}", f"{report_uri}#{report_hash}")`.

Failures return a structured `PublishResult` and leave nothing half-written: the upload happens before the chain write,
and a failed chain write leaves the old URI in place.

### 9.3 ERC-8183

```python
def suite_criterion(suite_uri: str, suite_hash: str) -> str: ...   # "eval_suite:<uri>#<hash>" for success_criteria
def suite_from_job(job: JobView, storage: StoragePort, *, allowed_judge_base_urls=None) -> Suite: ...
                                                                   # parse the description → fetch → verify hash; loaded as untrusted

class EvalGate:
    def __init__(self, commerce: CommercePort, storage: StoragePort, client: Evaluator, work: WorkFn, *,
                 suite_resolver=suite_from_job, state: MemoryGateState | SqliteGateState | None = None,
                 max_attempts: int = 2, submit_margin_s: int = 300, signer: SignerPort | None = None,
                 report_storage: StoragePort | None = None, evidence_budget: int = 65536,
                 encrypt_to: tuple[str, ...] = (), runs: int = 1): ...
    # Used as the `on_funded` callback of the SDK's funded_job_watcher (receives the watcher's job dict and
    # returns dict | None). integrations.bnbagent.watch runs it; `bnbagent-eval watch` wraps that.
    async def __call__(self, job: dict) -> dict | None: ...
```

`EvalGate.__call__` follows this procedure:
1. Load the per-job state.
2. If a task is already running, return `{"retry": True}`.
3. If the job is already done, return `None` (fire-once).
4. If `now > expiredAt − submit_margin_s`, mark the job abandoned and return `None`.
5. Otherwise start a background task running work → evaluate (stage `pre_submit`, subject bound to `output_hash`) → decide:
   - **pass:** attest, build evidence, then `submit_result(job_id, content, metadata={"eval": evidence})`;
   - **fail / inconclusive:** apply `on_inconclusive` and the attempts left;
   - **submit result mapping:** `{success: False, retryable: True}` → retry, any other failure → abandoned.
6. Return `{"retry": True}` so the watcher polls again.

The gate state (memory or sqlite) survives restarts. `required_watcher_attempts(gate_attempts,
work_seconds, interval)` sizes the watcher's `max_attempts` (the gate's attempts plus the polls while
the task runs); `bnbagent-eval watch --work-seconds` uses it.

```python
class DisputeAdvisor:     # client
    def __init__(self, commerce: CommercePort, policy: PolicyPort, storage: StoragePort, client: Evaluator): ...
    async def advise(self, job_id: int) -> Advice:
        # commerce.get_job → commerce.get_deliverable (DeliverableView.verified must be True)
        # suite = suite_from_job(job) (negotiated); evaluate(stage="review", subject=JobSubject(..., output_hash))
        # deadline = submitted_at + dispute_window; returns dispute | no_action | too_late (+ reasons, evidence checks)

class VoterAssistant:     # voter
    def __init__(self, commerce: CommercePort, policy: PolicyPort, storage: StoragePort, client: Evaluator,
                 signer: SignerPort): ...                    # signer.address identifies the voter
    async def review(self, job_id: int) -> Advice:
        # preconditions: policy.is_disputed(job), policy.is_voter(me), not policy.has_voted(job, me), within the vote window
        # verify manifest; check provider evidence: suite_hash == negotiated, subject fields == chain + manifest,
        # attestation valid; evaluate(stage="vote", live judge); compare with provider evidence (tolerances)
        # returns vote_reject | abstain | too_late (+ reasons, comparison, deadline)
```

### 9.4 Evidence
`evidence.build(response, *, report_uri, report_hash, budget_bytes=64 KiB)` produces `EvalEvidenceV1`
JSON within the budget. Trimming order: reasons, then per-case metrics, then cases, then per-metric
aggregates. The binding fields, hashes and the report pointer are never dropped.
`to_abi(evidence) -> bytes` and `from_abi(bytes)` implement the tuple in
[architecture.md §8](architecture.md#8-data-contracts-versioned-json).

### 9.5 On-chain data
`chain.onchain.enrich(session, reader, *, abis=None, block=None)` fills in the receipts, logs and
decoded calls of `TxStep`s at a pinned block. Transaction simulation on a fork is not part of the
package.

## 10. HTTP service (`[server]`)

The endpoints, limits, payment flow and configuration are documented in [SERVICE.md](../SERVICE.md).
Implementation points:

- `create_app()` returns a FastAPI app; the protocol logic lives in the framework-free `EvalService`.
- **`UntrustedSuitePolicy`:** only allow-listed metric IDs and methods (`MethodSpec.service_allowed`,
  method kinds from `serve --allowed-kinds`); built-in metrics only (no plugins); regexes with the
  `regex` module and a timeout; `lxml` with entities and network disabled; local-only `$ref`;
  targets and `dataset_ref` through the egress guard (`net.py`); limits on request bytes, cases,
  runs and judge calls; bounded judge settings; a per-client-IP rate limit.
- **`X402Gate`:**
  1. Recompute the price, bind `{quote_id, request_hash, amount, asset, expires_at}`, and return a
     402 challenge with `accepts[]` for each configured asset and scheme (`eip3009` or
     `permit2-exact`, from `AssetCatalogPort`).
  2. Verify the signature (EIP-3009 `TransferWithAuthorization`, or the Permit2 witness), payTo,
     asset and chain, amount ≥ the quoted amount, the validity window and the quote's request hash.
  3. **Atomically reserve** the nonce in sqlite, and check `authorizationState` on-chain.
  4. Settle through a `SettlementPort` (the facilitator client, `serve --facilitator-url`).
  5. Store the idempotency record (scoped to the payer).
  6. On a server-side evaluation failure, issue a credit receipt in the quoted asset.
- **Async jobs:** results are readable only with the bearer `access_token` returned at creation.
- **Privacy:** in no-retention mode case contents are never stored or logged; only hashes and
  aggregates are kept for idempotency.

## 11. Configuration, errors, logging, i18n

- **`EvalConfig.from_env()`** reads the config file named by `EVAL_CONFIG` (or `bnbagent-eval --config`), then
  overrides it with `EVAL_*` variables: judge (`EVAL_JUDGE_PROVIDER`, `_MODEL`, `_BASE_URL`, `_REGION`,
  `_EMBEDDING_MODEL`, `_TEMPERATURE`, `_MAX_TOKENS`, `_CACHE`), `EVAL_MAX_CONCURRENCY` (default 8) and
  `EVAL_TIMEOUT_S`. API keys come only from the environment. `get_env` matches the SDK's semantics: an
  empty value counts as unset. Service settings (`EVAL_SERVER_*`) live in `server/config.py`.
- **Errors:** `EvalError` (standalone) → `SchemaError`, `SuiteError`, `RequestError`, `MissingExtraError`, `PolicyError`,
  `JudgeError` (→ `JudgeThrottled`, `JudgeParseError`, `JudgeNonRetryable`), `TargetError`, `AdapterError`,
  `BudgetExceeded`, `PaymentRequired`, `PaymentError`, `AttestationError`, `ChainIntegrationError`.
  Metric-level failures are data, never exceptions.
- **Logging:** `logging.getLogger(__name__)`, no handlers and no `print`. Prompts are logged at DEBUG only, with redaction.
- **i18n:** `language` on the case and in the options. `metrics/tokens.py` handles whitespace languages, CJK
  (one token per character) and language-aware normalization (English articles are dropped only for
  English or unknown text). Judge prompts are in English.

## 12. Porting rules

1. **Port behaviour, not structure.** Copy prompts, labels, weights and default thresholds faithfully, and record the
   source in the module docstring and in `MetricSpec.sources`.
2. **Do not carry over source defects:** in AgentSight, the missing `eval_name`, the bad `PrebuiltMetrics` reference,
   `print()` debug output, identical Trace and Session levels (ours are distinct), the sequential runner, the CX0
   detection heuristic, StrandsInMemory missing from detection, and the broken DatasetGenerator imports.
3. **AWS only in** `judges/providers/bedrock.py`, `metrics/vendors/agentcore.py` and the Bedrock embedding provider.
4. **Polarity per method**, normalized in `BaseMetric`, and tested per method.
5. **Licence provenance** in `NOTICE`, per ported family.

Intended differences from the sources and known divergences are listed in [source-map.md](source-map.md).
