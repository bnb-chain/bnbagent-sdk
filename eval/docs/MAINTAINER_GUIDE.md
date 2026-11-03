# bnbagent-eval maintainer guide

For people who develop, review or maintain `bnbagent-eval`: how the package is put together,
where every item of the source toolkits went, the rules that keep results reproducible, and how
to change things safely. Users start with the [user guide](USER_GUIDE.md).

| Document | What it holds |
|---|---|
| this guide | layout, architecture, the source → canonical map, invariants, tests, common tasks |
| [design/architecture.md](design/architecture.md) | scope, layers, the unified call, S1 / S2 / S3 flows, data contracts, key decisions |
| [design/metric-catalog.md](design/metric-catalog.md) | the 99 canonical metrics: methods, params, thresholds, sources |
| [design/implementation.md](design/implementation.md) | package layout, extras, types, engine, client, chain, server |
| [design/testing.md](design/testing.md) | test levels, contract tests, parity, determinism, CI |
| [design/security.md](design/security.md) | threat model, trust boundaries, controls, accepted risks |
| [design/source-map.md](design/source-map.md) | every source item with `path:line`, notes, differences from the sources, reverse index |
| [METRICS.md](METRICS.md) | the generated catalogue of what is registered (IDs, methods, inputs, aliases) |
| [STANDALONE.md](STANDALONE.md) | use without BNB (checked by `scripts/check_standalone.py` in CI) |

## 1. What this package is

`bnbagent-eval` (import `bnbagent_eval`) is the agent-evaluation module of the bnbagent SDK. It
re-implements the evaluation capability of:

- **AWS Gen AI Evaluation Toolkit (ETK)**: built-in scorers (LLM-as-judge, examples), bridges
  to RAGAS, DeepEval, FMEval and Bedrock AgentCore, PyRIT red-teaming, agent-as-judge, and test
  generators;
- **AgentSight** (`agent-lens-core`): blackbox / whitebox evaluators, text-similarity
  functions, confidence scoring and insights, failure diagnosis, eval recommendation, the eval
  maker, simulated users and judge cost reduction;
- **STED** (amazon-science `sted`, arXiv 2512.23712): semantic tree edit distance and
  consistency scoring for structured outputs, the `sted` method of `ops.consistency`;
- the **earlier bnbagent-eval prototype** (an ETK plugin over AgentSight + STED).

All 258 source items are accounted for (ETK 112, AgentSight 129, STED 9, prototype 8): 151 map
to a canonical metric and method, 85 to a capability module (generation, analysis, authoring,
adapters, reporting, engine), 5 are fixtures or infrastructure, and 17 are not ported by
decision (§4.4). None is missing.

The result is **99 canonical metrics in 12 categories** behind **one call**,
`EvalClient.evaluate(EvalRequest) -> EvalResponse`, whose response serves the three on-chain
uses: **S1** ERC-8004 metrics publication, **S2** ERC-8183 per-job verdicts and dispute
evidence, **S3** a paid x402 HTTP service.

Design rules that every change must keep:

1. **Independent core.** No module outside `integrations/bnbagent` imports `bnbagent` or `web3`;
   chain access goes through `ports.py`. Core dependencies are `httpx`, `pyyaml` and `eth-hash`
   only. Everything but the BNB adapter works standalone; CI checks it with
   `scripts/check_core_only.sh` and `scripts/check_standalone.py`.
2. **Optional means lazy.** Extras are imported inside functions through
   `_optional.require(module, extra)`, which raises `MissingExtraError` naming the extra.
3. **Reproducible.** The same request and the same judge outputs give the same `result_hash` on
   Python 3.10–3.12 (§5).
4. **Higher is better.** Every metric score is in [0, 1] with 1 = best; inverted sources are
   normalized in the method (`MethodSpec.inverted`, `raw_range`).
5. **Our implementations.** The metrics are implemented in this package (STED, BERTScore,
   ROUGE / METEOR / BLEU, the Porter stemmer and Treebank tokenizer, every judge prompt).
   Libraries that implement a metric are test oracles only (§8.1). General libraries are used
   where they are not the metric (`transformers` loads BERTScore's encoder, `jsonschema` /
   `lxml` validate). Third-party evaluators (RAGAS, DeepEval, FMEval, AgentCore) are optional,
   labelled methods, never a metric's default (§4.5).
6. **Untrusted input stays untrusted.** Case text is fenced in judge prompts; suites from a
   counterparty or a service caller are policy-checked (§7).

## 2. Layout

```
eval/
├── bnbagent_eval/
│   ├── types/          # data model: EvalCase, Session/Trace/steps, Suite, EvalRequest/Response, results
│   ├── hashing.py      # canonical JSON + keccak (the basis of every hash)
│   ├── config.py       # EvalConfig.from_env: the EVAL_CONFIG file, then EVAL_* variables
│   ├── net.py          # shared egress guard (public addresses only, IP pinning, size caps)
│   ├── ports.py        # RegistryPort, CommercePort, PolicyPort, StoragePort, SignerPort, ... (protocols)
│   ├── judges/         # JudgeModel protocol, providers (openai, anthropic, bedrock, fake), fencing,
│   │                   #   structured output + repair, cache, sampling; prompts/ (versioned templates)
│   ├── adapters/traces # OTel GenAI, Langfuse, LangSmith, AgentCore ADOT, ... -> Session
│   ├── analysis/       # confidence, insights, diagnosis, calibration
│   ├── metrics/        # base (spec, method, outcome), registry, judge_base, one package per category,
│   │                   #   vendors/ (methods attached to canonical metrics: ragas, deepeval, fmeval,
│   │                   #   agentcore, local models, agentic judge)
│   ├── privacy.py      # hash-only reports, redaction, report encryption
│   ├── reporting/      # json, markdown, console, csv; exporters/ (otel, prometheus, mlflow)
│   ├── engine/         # resolve (suite -> methods + suite_hash), runner, targets, aggregate (verdict),
│   │                   #   seal (hashes), budget, dataset, compare, simulated users
│   ├── generation/     # rag, agentic, filters, simulation, redteam, error injection
│   ├── authoring/      # lint + lock, eval maker, recommend, catalogue doc renderer
│   ├── chain/          # manifest, attestation (EIP-712), evidence, erc8004 (S1), erc8183 (S2), onchain
│   ├── client.py       # EvalClient, LocalBackend, RemoteBackend (S3 client)
│   ├── server/         # FastAPI app, policy, store, x402 gate, settlement (S3 service)
│   ├── integrations/   # bnbagent (SDK adapter: the only SDK importer), jsonrpc (read-only)
│   ├── testing/        # in-memory fakes for every port (also for user tests)
│   └── cli.py          # bnbagent-eval metrics | run | make | lint | publish | review | watch | serve |
│                       #   calibrate | schemas
├── tests/              # unit, contract, parity, integration, determinism, security, live, vendor, testnet
├── schemas/            # generated JSON Schemas + OpenAPI (bnbagent-eval schemas)
├── benchmarks/         # calibration report, live judge sweep results
├── examples/           # runnable example suites and cases
├── ci/                 # github-eval-ci.yml (the SDK repository's .github/workflows/eval-ci.yml)
├── scripts/            # coverage gates, hash stability, core-only check, wheel check, source-map renderer
└── docs/               # user guides, this guide, design/
```

## 3. Architecture

### 3.1 Layers

`lint-imports` enforces three contracts (in `pyproject.toml`): layers import downward only;
only `integrations.bnbagent` imports the SDK or web3; `chain`, `server` and `engine` reach chain
code only through `ports.py`.

```
L5 delivery      cli  →  server  →  client
L4 chain         chain                              (S1 / S2 workflows, attestation, evidence)
L3 orchestration authoring → generation → engine
L2 capabilities  reporting | metrics | privacy  →  analysis
L1 foundation    judges | adapters | ports  →  config → types → net
                 → hashing | _optional | _jsonschema | logging_utils | _version → exceptions
```

### 3.2 Life of a request

1. **Build** an `EvalRequest`: stage, suite, cases, and optionally a target, subject and options
   (`client.build_request`).
2. **Resolve** the suite (`engine/resolve.py`): metric IDs and aliases are resolved through the
   registry, params validated, extras checked, the policy hook applied (service), the lock
   built (metric versions + prompt hashes), and `suite_hash` computed.
3. **Invoke targets** (`engine/targets.py`, `agent_targets.py`, `simulated.py`) when the request
   has one: `options.runs` calls per case; each run is a case with `output`, `trajectory` and
   `app_metrics`. A local backend with a chain reader enriches `TxStep`s (`chain/onchain.py`).
4. **Choose the judge** when any judge method is used (`engine/runner.py` `_select_judge`):
   suite pin, then `options.judge_override`, then the client's judge, then the configuration
   ([JUDGES.md](JUDGES.md#which-judge-runs)).
5. **Score** every (case, metric) with `BaseMetric.evaluate` under the concurrency, budget and
   deadline limits. Exceptions become `status="error"` results; they never abort the run.
6. **Aggregate** (`engine/aggregate.py`): per-case scores, per-metric stats (Welford mean / std,
   value percentiles), then the verdict and the stage action (`STAGE_ACTIONS`). The precedence
   rules and the stage table are in [SUITES.md](SUITES.md#pass-rule-and-verdict).
7. **Seal** (`engine/seal.py`): `result_hash`, then `response_hash`, from one serialization.
8. **Attest** (`chain/attestation.py`) when the client has a signer: EIP-712 over result, subject
   and suite hashes, verdict status, score (basis points), evaluator, issue and expiry times.

## 4. Metrics and their sources

### 4.1 The metric model

- A metric is a `BaseMetric` subclass with a `MetricSpec`: canonical ID `<category>.<name>`,
  description, methods, params JSON Schema, default threshold, version, unit and sources. It is
  registered with `@register`.
- A **method** (`MethodSpec`) is one way to compute the metric, of kind `rule` (deterministic),
  `judge` (LLM), `model` (a local model such as BERTScore's encoder or Detoxify) or `vendor`
  (RAGAS, DeepEval, FMEval, AgentCore). It declares its required inputs, its extra (`extra`,
  `extra_module`), its raw range, whether it is inverted, and whether the service allows it
  (`service_allowed`).
- `BaseMetric.evaluate` is a template method: it checks the inputs (a missing one gives
  `skipped` / `missing_input`), dispatches to `_evaluate_<method>` or a registered handler,
  normalizes the score (a NaN or infinite score or value is `status="error"`), applies the
  threshold and records provenance.
- `registry.register_method(metric_id, spec, handler, params=..., prompt_hash=...)` attaches a
  method from another module; every vendor and local-model method works this way.
  `registry.effective_spec` returns the spec including attached methods and their params.
- **Aliases** map source names to `(canonical ID, method)`, e.g. `ragas.faithfulness` →
  `rag.faithfulness` / `ragas`.
- **Judge metrics** (`metrics/judge_base.py` `JudgeMetric`) use a versioned `PromptTemplate`
  (`judges/prompts/<category>/`) with AgentSight's input modes `simple`, `with_expected` and
  `conversation`; untrusted fields fenced with a deterministic nonce; structured output with one
  repair re-ask (a reply that fails the schema or holds NaN / Infinity); `samples` aggregated by the lower median (the upper median when the source
  scale is higher-is-worse); the judge cache; budget and deadline checks; truncation by
  characters (`max_field_chars`).

### 4.2 How the source toolkits map to the catalogue

| Source | Became | Naming |
|---|---|---|
| ETK LLM-as-judge (`toxicity`, `tool_use`, custom description) | `safety.toxicity`, `agent.tool_selection`, `custom.judge_prompt` (`native`) | canonical IDs |
| ETK RAGAS bridge (11 names) | `ragas` method on `quality.*`, `rag.*`, `conversation.topic_adherence`, `agent.*` | aliases `ragas.<name>` |
| ETK DeepEval bridge (37 names) | `deepeval` method on 36 canonical metrics across all categories | aliases `deepeval.<name>` |
| ETK FMEval (7 algorithms + sub-scores) | `fmeval` method on `text.*` and `safety.toxicity` | aliases `fmeval.<algo>[.<score>]` |
| ETK Bedrock AgentCore (13 built-ins) | `agentcore` method on `quality.*`, `rag.faithfulness`, `safety.*`, `agent.*`; `quality.correctness` | aliases `vendor.agentcore.Builtin.<Name>` |
| ETK PyRIT | `generation.redteam` runner + `redteam.*` metrics | `pyrit.attack_succeeded`, `pyrit.turns_used` |
| ETK agent-as-judge | `agentic` method of `custom.rubric` / `custom.requirements_compliance` | – |
| ETK example scorers | `text.length`, `text.contains`, `ops.latency`, ... | – |
| ETK generators / filters | `generation.rag`, `generation.agentic`, `generation.filters` | – |
| AgentSight blackbox evaluators | `quality.*`, `safety.*`, `rag.*`, `format.*` (`native`) | canonical IDs |
| AgentSight whitebox evaluators | `agent.*`, `conversation.*` (`native`) | canonical IDs |
| AgentSight similarity functions | `text.*` rule methods, `text.embedding_similarity`, `text.bertscore` | – |
| AgentSight confidence scoring, insights | `agent.confidence` (rule), `analysis.confidence`, `analysis.insights` | – |
| AgentSight diagnoser, auto_evals, eval_maker | `analysis.diagnosis`, `authoring.recommend`, `authoring.eval_maker` | – |
| AgentSight `agent_sim` | `generation.simulation`, `engine.simulated.SimulatedUserTarget`, `conversation.failure_modes` | – |
| AgentSight cost reduction | `analysis.calibration`, `generation.error_injection`, `ops.cost` prices | – |
| AgentSight trace mappers | `adapters.traces` | – |
| STED (`SemanticJsonTreeConsistencyEvaluator` + `StructuralConsistencyAnalyzer`) | `sted` method of `ops.consistency` (`metrics/ops/sted.py`; `score` = `c_adj` by default) | the plain tree edit distance is the separate `ted` method |
| earlier prototype (`schema_compliance`, `rubric_match`, `consistency_coefficient`, diagnosis) | `format.json_schema`, `custom.judge_prompt`, `ops.consistency` / `sted` (`score: consistency_coefficient`), `analysis.diagnosis` | – |
| (no source) on-chain use | `onchain.*`, `format.artifact_integrity`, `custom.human_review`, `custom.python` | `bnbagent-eval:new` |

Redesign choices that apply across the map (5-tier judge scales where a source used continuous
0–1, higher-is-better polarity, the CJK tokenizer, `<OR>` reference splitting, stricter parsing,
XML security checks) are listed in
[source-map.md §5.1](design/source-map.md#51-intended-differences); the low-severity divergences
that remain are in §5.2 there.

To look something up in either direction: `bnbagent-eval metrics --json` gives every spec with
its `sources`; [METRICS.md](METRICS.md) lists all aliases; `registry.resolve("deepeval.bias")`
returns the class and method.

### 4.3 The map, item by item

Generated from [design/source-map.md](design/source-map.md), which adds `path:line` and notes for
each row. Regenerate after editing the source map: `python3 eval/scripts/render_source_map.py`
(a test fails while it is stale).

<!-- source-map:start (generated by eval/scripts/render_source_map.py) -->

#### ETK

**2.1 `packages/evaluator-builtin` — `llm_as_judge` scorer**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `sample` | – (test fixture) | – | `bnbagent_eval/judges/providers/fake.py` |
| `toxicity` | `safety.toxicity` | `native` (score = 1 − toxicity) | `bnbagent_eval/metrics/safety/toxicity.py`, `judges/prompts/safety/toxicity.py` |
| `tool_use` | `agent.tool_selection` | `native` | `bnbagent_eval/metrics/agent/tool_selection.py` |
| custom `description` (`_generic_scorer`) | `custom.judge_prompt` | `native` (`scale: numeric`) | `bnbagent_eval/metrics/custom/judge_prompt.py`, `judges/prompts/custom/judge_prompt.py` |
| `SYSTEM_PROMPT` / `_build_evaluator_prompt` | (prompt text of the three rows above) | – | `bnbagent_eval/judges/prompts/custom/judge_prompt.py` |

**2.2 RAGAS**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `answer_relevancy` | `quality.relevance` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `answer_accuracy` | `quality.answer_correctness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `response_groundedness` | `rag.groundedness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `faithfulness` | `rag.faithfulness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `context_relevancy` | `rag.context_relevance` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `context_precision` | `rag.context_precision` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `context_recall` | `rag.context_recall` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `topic_adherence` | `conversation.topic_adherence` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `tool_call_accuracy` | `agent.tool_trajectory_match` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `agent_goal_accuracy` | `agent.goal_success` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |
| `agent_goal_accuracy_with_reference` | `agent.goal_success` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` |

**2.2 DeepEval**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `answer_relevancy` | `quality.relevance` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `faithfulness` | `rag.faithfulness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `contextual_precision` | `rag.context_precision` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `contextual_recall` | `rag.context_recall` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `contextual_relevancy` | `rag.context_relevance` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `hallucination` | `rag.hallucination` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `bias` | `safety.bias` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `toxicity` | `safety.toxicity` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `summarization` | `quality.summarization` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `json_correctness` | `format.json_schema` | `rule` | `bnbagent_eval/metrics/format/structured.py` |
| `prompt_alignment` | `quality.instruction_following` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `exact_match` | `text.exact_match` | `rule` | `bnbagent_eval/metrics/text/exact.py` |
| `pattern_match` | `format.regex` | `rule` | `bnbagent_eval/metrics/format/structured.py` |
| `task_completion` | `agent.task_completion` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `tool_correctness` | `agent.tool_trajectory_match` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `mcp_use` | `agent.mcp_use` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `argument_correctness` | `agent.tool_parameter_accuracy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `misuse` | `safety.misuse` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `non_advice` | `safety.non_advice` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `pii_leakage` | `safety.pii_leakage` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `plan_adherence` | `agent.plan_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `plan_quality` | `agent.plan_quality` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `role_violation` | `safety.role_violation` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `step_efficiency` | `agent.trajectory_efficiency` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `conversation_completeness` | `conversation.completeness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `turn_relevancy` | `conversation.turn_relevancy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `goal_accuracy` | `agent.goal_success` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `knowledge_retention` | `conversation.knowledge_retention` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `role_adherence` | `conversation.role_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `tool_use` | `agent.tool_selection` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `topic_adherence` | `conversation.topic_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `mcp_task_completion` | `agent.mcp_task_completion` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `multi_turn_mcp_use` | `agent.mcp_use` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `turn_contextual_precision` | `conversation.turn_context_precision` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `turn_contextual_recall` | `conversation.turn_context_recall` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `turn_contextual_relevancy` | `conversation.turn_context_relevancy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |
| `turn_faithfulness` | `conversation.turn_faithfulness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` |

**2.2 AgentCore**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `Builtin.GoalSuccessRate` | `agent.goal_success` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Coherence` | `quality.coherence` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Conciseness` | `quality.conciseness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Correctness` | `quality.correctness` | `agentcore` | `bnbagent_eval/metrics/quality/correctness.py` |
| `Builtin.Faithfulness` | `rag.faithfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Harmfulness` | `safety.harmfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Helpfulness` | `quality.helpfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.InstructionFollowing` | `quality.instruction_following` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Refusal` | `safety.refusal` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.ResponseRelevance` | `quality.relevance` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.Stereotyping` | `safety.stereotyping` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.ToolParameterAccuracy` | `agent.tool_parameter_accuracy` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `Builtin.ToolSelectionAccuracy` | `agent.tool_selection` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |

**2.2 FMEval**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `factual_knowledge` | `text.contains` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `qa_toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `summarization_toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `qa_accuracy` | `text.token_f1` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `summarization_accuracy` | `text.rouge` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `classification_accuracy` | `text.classification_accuracy` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` |

**2.2 AgentCore extras**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `Builtin.ContextRelevance` | – (excluded) | – | – |
| custom evaluator (`CustomEvaluatorConfig`, `level`) | any metric's `agentcore` method | `agentcore` + param `agentcore_evaluator_id` (+ `level`) | `bnbagent_eval/metrics/vendors/agentcore.py` |
| `MAX_TOOL_SPANS = 10`, `SpanFilter` | (span conversion) | – | `bnbagent_eval/metrics/vendors/_agentcore_spans.py` |

**2.2 FMEval sub-scores**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `exact_match_score`, `quasi_exact_match_score`, `f1_score`, `precision_over_words`, `recall_over_words` | `text.exact_match`, `text.quasi_exact_match`, `text.token_f1` (precision / recall in details) | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` |
| `rouge`, `meteor`, `bertscore` | `text.rouge`, `text.meteor`, `text.bertscore` | `fmeval` | same |
| `factual_knowledge`, `factual_knowledge_quasi_exact` | `text.contains` (`quasi`) | `fmeval` | same |
| `toxicity`, `severe_toxicity`, `obscene`, `threat`, `insult`, `identity_attack`, `sexual_explicit` | `safety.toxicity` (`variant`, `model_type` detoxify / toxigen) | `fmeval` (inverted) | same |
| `classification_accuracy_score`, `balanced_accuracy_score`, `precision_score`, `recall_score` | `text.classification_accuracy` | `fmeval` | same |

**2.3 `packages/evaluator-builtin` — example scorers and app invokers**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `example_exact_match` | `text.exact_match` | `rule` | `bnbagent_eval/metrics/text/exact.py` |
| `ExampleTextQualityScorer.length` | `text.length` | `rule` | `bnbagent_eval/metrics/text/classify.py` |
| `ExampleTextQualityScorer.prefix` | `format.regex` | `rule` | `bnbagent_eval/metrics/format/structured.py` |
| `example_app` | – (example) | – | – |
| AgentCore runtime app invoke | – (not ported) | – | – |
| Bedrock Converse app invoke | – (not ported) | – | – |

**2.4 `packages/evaluator-pyrit`**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `attack_succeeded` | `redteam.attack_resistance` | `rule` (alias `pyrit.attack_succeeded`) | `bnbagent_eval/metrics/redteam/attack.py` |
| `turns_used` | `redteam.turns_to_break` | `rule` (alias `pyrit.turns_used`) | `bnbagent_eval/metrics/redteam/attack.py` |
| `pyrit_red_team_app_invoke` | `generation.redteam` (`run_red_team`) | – | `bnbagent_eval/generation/redteam.py` |
| `load_pyrit_dataset` | `generation.redteam.load_pyrit_dataset` | – | `bnbagent_eval/generation/redteam.py` |

**2.5 `packages/evaluator-agent-as-judge`**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `requirements_compliance` (+ `REQUIREMENTS_COMPLIANCE_DESCRIPTION`, output schema) | `custom.requirements_compliance` | `native` (one judge call), `agentic` | `bnbagent_eval/metrics/custom/requirements.py`, `metrics/vendors/agentic.py` |
| `AgentAsJudgeScorer` custom evaluators | `custom.rubric` | `agentic` | `bnbagent_eval/metrics/vendors/agentic.py` |
| `write_verdict` (weighted partial scores) | `custom.rubric` / `custom.requirements_compliance` | `agentic` | `bnbagent_eval/metrics/vendors/agentic.py`, `judges/prompts/custom/agentic_verdict.py` |
| agent prompt, runner, network guard | (agentic loop) | – | `bnbagent_eval/metrics/vendors/agentic.py`, `judges/prompts/custom/agentic_plan.py`, `agentic_inspect.py` |

**2.6 `packages/generator-builtin` (test generation and filtering)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `RagGenerator` | `generation.rag` | – | `bnbagent_eval/generation/rag.py` |
| `LlmAsJudgeFilter` | `generation.filters` | – | `bnbagent_eval/generation/filters.py` |
| `AgenticGenerator` | `generation.agentic` | – | `bnbagent_eval/generation/agentic/__init__.py` |
| taxonomy stage | `generation.agentic.stages.build_taxonomy` | – | `bnbagent_eval/generation/agentic/stages.py` |
| planner stage | `stages.sample_scenarios` | – | same |
| generation stage | `stages.generate_cases` | – | same |
| augmentation (rephrase, parameter_variation, multi_turn_extension, edge_case, negative_case) | `stages.augment_cases` | – | same |
| review sampler (0.2) | `stages.apply_review_sampling` | – | same |
| seeding from production traces | `generation.agentic.seeding` | – | `bnbagent_eval/generation/agentic/seeding.py` |
| JSON-schema tool-call validator | `generation.agentic.validator` | – | `bnbagent_eval/generation/agentic/validator.py` |
| taxonomy / sampling / generation / augmentation prompts | (prompt text) | – | `bnbagent_eval/judges/prompts/generation/agentic.py` |
| Bedrock batch clients, concurrency, errors | – (infrastructure) | – | `bnbagent_eval/generation/_llm.py` (JSON extraction, bounded gather) |

**2.7 `packages/etk-sdk` (framework)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `Scorer` classes, `@scorer`, function scorers | `custom.python` | – | `bnbagent_eval/metrics/custom/python.py` |
| `Evaluator` (invoke → score → results) | `engine.runner` | – | `bnbagent_eval/engine/runner.py` |
| score summary (count, errors, mean) | `reporting` | – | `bnbagent_eval/reporting/*`, `engine/aggregate.py` |
| ETK service / AWS plumbing | – (not ported) | – | – |
| service, infrastructure, API | – | – | – |

#### AgentSight (`agent-lens-core`)

**3.1 Blackbox and whitebox evaluators (`agent_lens/evaluators`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `AnswerCorrectnessEvaluator` | `quality.answer_correctness` | native (conceptual_match); rule (`strategy` exact / rouge / jaccard / bertscore = semantic_match) | `bnbagent_eval/metrics/quality/answer_correctness.py` |
| `BiasEvaluator` | `safety.bias` | native | `bnbagent_eval/metrics/safety/bias.py` |
| `ClarityEvaluator` | `quality.clarity` | native | `bnbagent_eval/metrics/quality/clarity.py` |
| `CompletenessEvaluator` | `quality.completeness` | native | `bnbagent_eval/metrics/quality/completeness.py` |
| `ConcisenessEvaluator` | `quality.conciseness` | native | `bnbagent_eval/metrics/quality/conciseness.py` |
| `ContextQualityEvaluator` | `rag.context_relevance`, `rag.context_precision` | native | `bnbagent_eval/metrics/rag/context_relevance.py`, `context_precision.py` |
| `ContextTimeFreshnessEvaluator` | `rag.context_freshness` | native | `bnbagent_eval/metrics/rag/context_freshness.py` |
| `TemporalSensitivity` | `rag.context_freshness` | param | `bnbagent_eval/metrics/rag/context_freshness.py` |
| `ContradictionEvaluator` | `quality.contradiction` | native | `bnbagent_eval/metrics/quality/contradiction.py` |
| `ConversationCompletenessEvaluator` | `conversation.completeness` | native | `bnbagent_eval/metrics/conversation/completeness.py` |
| `ConversationalCoherenceEvaluator` | `conversation.coherence` | native | `bnbagent_eval/metrics/conversation/coherence.py` |
| `CustomPromptEvaluator` | `custom.judge_prompt` | native (`scale: five_tier`) | `bnbagent_eval/metrics/custom/judge_prompt.py` |
| `EntityConsistencyEvaluator` | `conversation.entity_consistency` | native | `bnbagent_eval/metrics/conversation/entity_consistency.py` |
| `FactualCorrectnessEvaluator` | `rag.faithfulness` | native | `bnbagent_eval/metrics/rag/faithfulness.py` |
| `FluencyEvaluator` | `quality.fluency` | native; rule (`[fluency]`, LanguageTool) | `bnbagent_eval/metrics/quality/fluency.py` |
| `HarmfulnessEvaluator` | `safety.harmfulness` | native; model (`[toxicity]`, detoxify) | `bnbagent_eval/metrics/safety/harmfulness.py` |
| `HelpfulnessEvaluator` | `quality.helpfulness` | native | `bnbagent_eval/metrics/quality/helpfulness.py` |
| `KnowledgeRetentionEvaluator` | `conversation.knowledge_retention` | native | `bnbagent_eval/metrics/conversation/knowledge_retention.py` |
| `OutputStructureComplianceEvaluator` | `format.json_schema` / `format.regex` / `format.xml` / `format.structure` | rule (efficient mode); native (LLM mode, `prioritize_efficiency=False`) | `bnbagent_eval/metrics/format/structured.py`, `structure.py` |
| `PIILeakageEvaluator` | `safety.pii_leakage` | native | `bnbagent_eval/metrics/safety/pii_leakage.py` |
| `RefusalEvaluator` | `safety.refusal` | native | `bnbagent_eval/metrics/safety/refusal.py` |
| `RelevanceEvaluator` | `quality.relevance` | native | `bnbagent_eval/metrics/quality/relevance.py` |
| `ToneEvaluator` | `quality.tone` | native | `bnbagent_eval/metrics/quality/tone.py` |
| `MatchStrategy` | `quality.answer_correctness` | param `strategy` | `bnbagent_eval/metrics/quality/answer_correctness.py` |
| `EvalCategory` | `custom.judge_prompt` `category`; `JudgeMetric.output_or_conversation`; `ConversationJudge.min_turns` | - | `bnbagent_eval/metrics/custom/judge_prompt.py`, `metrics/judge_base.py`, `metrics/conversation/_common.py` |
| `detect_input_format` | `JudgeMetric.select_mode` (`input_mode` param) | - | `bnbagent_eval/metrics/judge_base.py` |
| `AgentAdaptivityEvaluator` | `agent.adaptivity` | native | `bnbagent_eval/metrics/agent/adaptivity.py` |
| `AgentHallucinationEvaluator` | `agent.reasoning_hallucination` | native | `bnbagent_eval/metrics/agent/reasoning_hallucination.py` |
| `AgentReasoningEvaluator` | `agent.reasoning` | native | `bnbagent_eval/metrics/agent/reasoning.py` |
| `AgentToolTrajectoryMatchEvaluator` | `agent.tool_trajectory_match` | rule | `bnbagent_eval/metrics/agent/trajectory.py` |
| `ExecutionChainEvaluator` | `agent.execution_chain` | native | `bnbagent_eval/metrics/agent/execution_chain.py` |
| `FullTrajectoryQualityEvaluator` | `agent.multi_agent_trajectory` | native (rubric) | `bnbagent_eval/metrics/agent/multi_agent_trajectory.py` |
| `GoalSuccessRateEvaluator` | `agent.goal_success` | native | `bnbagent_eval/metrics/agent/goal_success.py` |
| `ToolParameterAccuracyEvaluator` | `agent.tool_parameter_accuracy` | native | `bnbagent_eval/metrics/agent/tool_parameter_accuracy.py` |
| `ToolUseQualityEvaluator` | `agent.tool_use_quality` | native (rubric) | `bnbagent_eval/metrics/agent/tool_use_quality.py` |
| `TrajectoryEfficiencyEvaluator` | `agent.trajectory_efficiency` | native | `bnbagent_eval/metrics/agent/trajectory_efficiency.py` |
| `WhiteboxCustomPromptEvaluator` | `custom.judge_prompt` | native (`scale: pass_fail_na`) | `bnbagent_eval/metrics/custom/judge_prompt.py` |
| `RubricBasedEvaluator` | `custom.rubric` | native | `bnbagent_eval/metrics/custom/rubric.py` |
| `PrebuiltMetrics` | `agent.*` (rows above) | - | - |

**3.2 Text-similarity functions (`agent_lens/metrics`, `agent_lens/utils/similarity_utils.py`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `calculate_rouge_l` | `text.rouge` | rule | `metrics/text/overlap.py` |
| `calculate_jaccard` | `text.jaccard` | rule | `metrics/text/overlap.py` |
| `calculate_cosine_similarity` | `text.tfidf_cosine` | rule | `metrics/text/overlap.py` |
| `calculate_bleu` | `text.bleu` | rule | `metrics/text/overlap.py` |
| `calculate_bertscore` | `text.bertscore` | model | `metrics/text/bertscore.py` |
| `SemanticRelevanceScorer` | `text.embedding_similarity` | keyword (default), embeddings, glove, fasttext | `metrics/text/similarity.py`, `metrics/vendors/local_models.py` |
| `compute_similarity` | `text.embedding_similarity` | keyword | `metrics/text/similarity.py` |
| `compute_keyword_overlap` | `text.embedding_similarity` | keyword | `metrics/tokens.py (`keyword_jaccard`)` |
| `evaluate_with_llm` | judge infrastructure | - | `judges/base.py`, `judges/structured.py`, `metrics/judge_base.py` |

**3.3 Confidence scoring and insights (`agent_lens/scoring`, `agent_lens/types`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `ConfidenceScorer` | `agent.confidence` | rule | `metrics/agent/confidence.py`, `analysis/confidence.py` |
| `SignalExtractor` | `agent.confidence`, `agent.trajectory_stats` | rule | `analysis/confidence.py`, `metrics/agent/trajectory.py` |
| `ScoreDeriver` | `agent.confidence` | rule | `analysis/confidence.py` |
| `ScoreAggregator` | `agent.confidence` | rule | `analysis/confidence.py` |
| `ScoringConfig` | `agent.confidence` params | - | `analysis/confidence.py` |
| `extract_keywords` | `text.jaccard` (`stopwords`) | rule | `analysis/confidence.py`, `metrics/text/overlap.py` |
| `compute_keyword_overlap` | `text.jaccard` (`stopwords`) | rule | `metrics/text/overlap.py` |
| `ConfidenceSummary` | `agent.confidence` batch summary | - | `analysis/confidence.py` |
| `insights` | `analysis.insights` | - | `analysis/insights.py` |
| `insights` | `analysis.insights` types | - | `analysis/insights.py` |

**3.4 Failure diagnosis (`agent_lens/evaluators/diagnoser`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `FailureDiagnoser` | `analysis.diagnosis` | - | `analysis/diagnosis.py` |
| `DiagnosisAggregator` | `analysis.diagnosis` | - | `analysis/diagnosis.py` |
| `root_cause_analysis` | `analysis.diagnosis` | - | `analysis/diagnosis.py`, `judges/prompts/analysis/diagnosis.py` |
| `recommendations` | `analysis.diagnosis` | - | `same` |
| `types` | `analysis.diagnosis` types | - | `analysis/diagnosis.py` |

**3.5 Eval recommendation and authoring (`agent_lens/auto_evals`, `agent_lens/eval_maker`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `AutoEvaluator` | `authoring.recommend` | - | `authoring/recommend.py` |
| `EvalDiscovery` | `authoring.recommend` | - | `authoring/recommend.py` |
| `EvalRecommender` | `authoring.recommend` | - | `authoring/recommend.py` |
| `agent` | `authoring.eval_maker` | - | `authoring/eval_maker.py` |
| `assess_quality` | `eval_maker` step assess | - | `authoring/eval_maker.py`, `judges/prompts/authoring/eval_maker_assess.py` |
| `analyze_strategy` | step strategy | - | `judges/prompts/authoring/eval_maker_strategy.py` |
| `match_blackbox` | step match | - | `judges/prompts/authoring/eval_maker_match.py` |
| `generate_custom` | step generate | - | `judges/prompts/authoring/eval_maker_generate.py` |
| `reflect_on_generation` | step reflect (<= 2) | - | `judges/prompts/authoring/eval_maker_reflect.py` |
| `validate_structure` | step validate | - | `authoring/eval_maker.py`, `authoring/lint.py` |
| `preview_evaluators` | – (not ported) | - | – |
| `blackbox_registry` | metric registry | - | `metrics/registry.py` |
| (module group) | suite serialisation | - | `types/suite.py`, `engine/resolve.py` |
| `validation` | `authoring.lint` | - | `authoring/lint.py` |

**3.6 Generators and simulated users (`agent_lens/generators`, `agent_sim`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `TopicPlanner` | `generation.agentic.topics` | - | `generation/agentic/topics.py` |
| `DatasetGeneratorRefactored` | – (not ported) | - | – |
| `UserSimulator` | `SimulatedUserTarget` | - | `engine/simulated.py` |
| `CompetencyLevel` | `types.simulation.CompetencyLevel` | - | `types/simulation.py` |
| `StressAxis` | `types.simulation`, `conversation.failure_modes` | - | `types/simulation.py` |
| `FailureMode` | `types.simulation`, `conversation.failure_modes` | - | `types/simulation.py` |
| `ScenarioPlanner` | `generation.simulation.planner` | - | `generation/simulation/planner.py` |
| `CoverageTracker` | `generation.simulation.planner` | - | `generation/simulation/planner.py` |
| `ConversationAnalyzer` | `conversation.failure_modes` | native | `metrics/conversation/failure_modes.py` |
| `run_simulation` | `generation.simulation.run_simulation` | - | `generation/simulation/simulator.py` |
| `run_simulation_online` | `generation.simulation` + `SimulatedUserTarget` | - | `generation/simulation/simulator.py` |
| `MockAgent` | – (not ported) | - | – |

**3.7 Judge cost reduction (`agent_cost_reduction`)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `run_benchmark` | `analysis.calibration` | - | `analysis/calibration.py` |
| `inject_errors` | `generation.error_injection` | - | `generation/error_injection.py`, `judges/prompts/analysis/error_injection.py` |
| `generate_dataset` | `generation.simulation` | - | `generation/simulation/simulator.py` |
| `generate_dataset_online` | `generation.simulation` | - | `same` |
| `generate_report` | – (not ported) | - | – |
| `BenchmarkConfig` | `analysis.calibration` args | - | `analysis/calibration.py` |

**3.8 Trace mappers (`agent_lens/mappers`) → `adapters.traces`**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `strands_in_memory_session_mapper` | `adapters.traces.otel_genai` | - | `adapters/traces/otel_genai.py` |
| `otel_span_mapper` | `adapters.traces.agentcore_adot` | - | `adapters/traces/agentcore_adot.py` |
| `telemetry_tracer_mapper` | `agentcore_adot.StrandsScope` | - | `adapters/traces/agentcore_adot.py` |
| `opentelemetry_instrumentation_langchain_mapper` | `agentcore_adot.OpenLLMetryLangchainScope` | - | `adapters/traces/agentcore_adot.py` |
| `openinference_instrumentation_langchain_mapper` | `agentcore_adot.OpenInferenceLangchainScope` | - | `adapters/traces/agentcore_adot.py` |
| `langfuse_mapper` | `adapters.traces.langfuse.LangfuseStrandsAdapter` | - | `adapters/traces/langfuse.py` |
| `langchain_langfuse_mapper` | `LangfuseLangchainAdapter` | - | `adapters/traces/langfuse.py` |
| `langgraph_langfuse_mapper` | `LangfuseLanggraphAdapter` | - | `adapters/traces/langfuse.py` |
| `langgraph_langsmith_mapper` | `adapters.traces.langsmith` | - | `adapters/traces/langsmith.py` |
| `smolagents_mapper` | `adapters.traces.smolagents` | - | `adapters/traces/smolagents.py` |
| `taubench_session_mapper` | `adapters.traces.taubench` | - | `adapters/traces/taubench.py` |
| `cx0_mapper` | `adapters.traces.cx0` | - | `adapters/traces/cx0.py` |
| `eb_habitat_mapper` | `adapters.traces.eb_habitat` | - | `adapters/traces/eb_habitat.py` |
| `session_mapper_factory` | `adapters.traces` auto-detection | - | `adapters/traces/base.py (`AdapterRegistry`)` |

**3.9 Evaluation framework, aggregation, reporting (`agent_lens/evaluators`, `orchestration`, ...)**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `evaluator` | `BaseMetric` | - | `metrics/base.py` |
| `eval_description` | `MetricSpec.description`, `authoring.catalog_doc` | - | `metrics/base.py`, `authoring/catalog_doc.py` |
| `aggregate_scores` | `engine.aggregate.combine` | - | `engine/aggregate.py` |
| `custom_eval_runner` | `engine.runner` | - | `engine/runner.py` |
| `OrchestratorReport` | `engine.aggregate.decide` (`passes_thresholds`) | - | `engine/aggregate.py` |
| `orchestrator` | `engine.runner` | - | `engine/runner.py` |
| `evaluation_runner` | `engine.runner` | - | `engine/runner.py` |
| `loader` | `engine.dataset` | - | `engine/dataset.py` |
| `display_console` | `reporting.console` | - | `reporting/console.py` |
| `json_to_report` | `reporting.markdown` / `json_report` / `csv` | - | `reporting/*.py` |
| `MetricsReporter` | `reporting.exporters` | - | `reporting/exporters/{otel,prometheus,mlflow}.py` |
| `config` | `reporting.exporters.otel` | - | `reporting/exporters/otel.py` |
| `trace_extractor` | trajectory rendering (`agent_id`) | - | `adapters/traces/render.py` |
| `relationship_builder` | trajectory rendering (multi-agent) | - | `adapters/traces/render.py`, `metrics/agent/_whitebox.py` |
| (module group) | – (not ported) | - | – |

**3.10 Trace analyzer (`agent_lens/analyzer`) — deferred**

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `Analyzer` | – (deferred) | - | – |
| `MapperCreator` | – (deferred) | - | – |
| `CodeInterpreter` | – (deferred) | - | – |
| `TraceQualityAnalyzer` | partly `authoring.recommend` (trace-quality prerequisites) | - | `authoring/recommend.py` |
| `SessionConversionAnalyzer` | – (deferred) | - | – |

#### STED (`amazon-science/sted`)

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `SemanticJsonTreeConsistencyEvaluator` (structure-guided `_calculate_optimal_matching_cost`, Eq. 1–3) | `ops.consistency` | `sted` (judge: embeddings) | `bnbagent_eval/metrics/ops/sted.py` (`Sted`, `build_tree`, `hungarian`) |
| `_calculate_semantic_similarity`, `_preprocess_key_name` | `ops.consistency` | `sted` | `bnbagent_eval/metrics/ops/sted.py` (`EmbeddingSimilarity`, `prepare_text`) |
| `evaluate_structural_consistency`, `_calculate_consistency_metrics` | `ops.consistency` | `sted` (param `score`, default `c_adj`) | `bnbagent_eval/metrics/ops/sted.py` (`consistency`, `is_valid`) |
| `AgentConsistencyEvaluator` (run an agent N times per prompt, score consistency) | `engine` targets + `options.runs` + `ops.consistency` / `sted` | – | `bnbagent_eval/engine/runner.py`, `engine/targets.py` |
| `trajectories_from_records` (OTel tool events → trajectories) | `adapters.traces` | – | `bnbagent_eval/adapters/traces/otel_genai.py` |
| TED / BERTScore / DeepDiff baselines | `ops.consistency` `ted`; `text.bertscore` | rule; model | `bnbagent_eval/metrics/ops/consistency.py`, `metrics/text/bertscore.py` |
| `LLMJudge` (tool-call generation / judging for experiments) | – (not ported) | – | – |
| `ConsistencyPredictor` | – (not ported) | – | – |
| `ConsistencyRiskAssessor` | – (not ported) | – | – |

#### Earlier bnbagent-eval prototype (`BNB_reference/bnbagent-eval`)

| Source item | New: canonical ID / module | Method | Our file |
|---|---|---|---|
| `schema_compliance` (AgentSight `OutputStructureComplianceEvaluator`) | `format.json_schema` | `rule` | `bnbagent_eval/metrics/format/structured.py` |
| `rubric_match` (AgentSight `CustomPromptEvaluator`) | `custom.judge_prompt` | `native` | `bnbagent_eval/metrics/custom/judge_prompt.py` |
| `consistency_coefficient` (STED over repeated outputs) | `ops.consistency` | `sted` with `score: consistency_coefficient` | `bnbagent_eval/metrics/ops/sted.py` |
| aggregate over quality scores only (consistency reported separately) | `MetricConfig.weight: 0` + `required: true` | – | `bnbagent_eval/engine/aggregate.py` |
| failure diagnosis (AgentSight `FailureDiagnoser`) | `analysis.diagnosis` | – | `bnbagent_eval/analysis/diagnosis.py` |
| `run_with_consistency` (N samples; submit / decline from quality + consistency bands) | `options.runs` + suite pass rule + `EvalGate` | – | `bnbagent_eval/chain/erc8183.py` |
| AgentCore custom LLM-as-judge with an inline definition (`CreateEvaluator`) | `agentcore` method, param `agentcore_evaluator_id` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |
| AgentCore custom code-based (Lambda) evaluators | `agentcore` method, param `agentcore_evaluator_id` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` |

<!-- source-map:end -->

### 4.4 Not ported, by decision

[source-map.md §4](design/source-map.md#4-not-ported-by-decision) has the list with reasons. In
short: ETK app invokers and service plumbing (targets replace them); `Builtin.ContextRelevance`
(ETK itself excludes it); AgentSight's broken `DatasetGenerator`; the trace analyzer and
`MapperCreator`; live trace-fetch clients (users export traces and load them); `MockAgent` (any
target serves); the Plotly benchmark report; the eval-maker preview step; STED's research
utilities (`LLMJudge`, `ConsistencyPredictor`, `ConsistencyRiskAssessor`); the prototype's
two-band retry policy (`run_with_consistency`), which a required consistency metric plus
`min_aggregate` covers.

### 4.5 Third-party methods

`metrics/vendors/__init__.py` `PROVIDERS` lists every third-party provider, keyed by its pip
extra: name, link, role (`evaluator`: the provider computes the score; `model`: our code runs
their model) and what it needs. `tests/unit/test_third_party.py` enforces:

- every `vendor` / `model` method with an extra names a provider in `PROVIDERS`;
- every provider installs with one command (`pip install 'bnbagent-eval[<extra>]'`);
- an `evaluator` method is never a metric's first (default) method.

User-facing details are in [THIRD_PARTY.md](THIRD_PARTY.md). To add a provider, see
[Adding a method to an existing metric](#adding-a-method-to-an-existing-metric).

### 4.6 Models and configuration

The precedence and the variables are in [JUDGES.md](JUDGES.md#which-judge-runs). In the code:
`config.py` reads the YAML file named by `EVAL_CONFIG` / `bnbagent-eval --config` and the
`EVAL_*` variables (the module docstring lists the keys); `types/judge.py` holds the
per-provider defaults, `DEFAULT_JUDGE_MODELS` (Bedrock `global.anthropic.claude-sonnet-5-5`,
Anthropic `claude-sonnet-5-5`; OpenAI-compatible endpoints must name a model) and
`DEFAULT_EMBEDDING_MODELS` (Bedrock `amazon.titan-embed-text-v2:0`, OpenAI
`text-embedding-3-small`). `types.judge.embedding_model(cfg)` is the one place that resolves the
embedding model. There is no default provider: judge calls cost money, so choosing one stays
explicit.

## 5. Hashes and reproducibility

| Hash | Covers | Excludes |
|---|---|---|
| `suite_hash` | resolved suite (inline `$ref`) + lock (metric versions, prompt hashes) | – |
| `request_hash` | the canonical request | – |
| `result_hash` | schema version, stage, subject, suite hash, per-case results, aggregate, verdict, metric versions, judge config | `judge_source` (cache vs live), run `app_metrics`, timestamps |
| `response_hash` | the whole response | `response_hash` itself, `attestation` |

Canonical JSON: sorted keys, compact separators, `ensure_ascii=False`, floats rounded to 6
decimals, NaN rejected, keccak-256. The ERC-8004 / SDK manifest hash is reproduced exactly as the
SDK computes it (`ensure_ascii=True`).

**Rules when changing behaviour:**

- Changing how a metric computes a score: bump `MetricSpec.version`. It goes into the lock and
  the hashes, so earlier results stay attributable.
- Changing prompt text: add a template version (`PromptTemplate.version`) and re-pin. Earlier
  versions stay in `VERSIONS` so locked suites keep working.
- New fields in hashed types: keep them `None` by default and out of the dict when unset, or
  every golden hash changes (see `provenance.judge.tool_choice` for the pattern).

**Pinned fixtures and how to re-pin them** (commands from `eval/`). Review the diff before
committing any of these.

| Fixture | Re-pin | Check |
|---|---|---|
| prompt hashes (`tests/fixtures/prompt_hashes.json`) | `python -c "from tests.unit.metrics.test_prompt_snapshots import _pin; _pin()"` | `pytest tests/unit/metrics/test_prompt_snapshots.py` |
| determinism golden (`tests/determinism/golden_hashes.json`) | `python3 scripts/check_hash_stability.py --write-golden` (runs 3.10 / 3.11 / 3.12 via uv) | the same script without the flag |
| trace adapter goldens (`tests/fixtures/traces/`) | `BNBAGENT_UPDATE_GOLDEN=1 pytest tests/unit/adapters` | `pytest tests/unit/adapters` |
| JSON Schemas + OpenAPI (`schemas/`) | `bnbagent-eval schemas` (FastAPI pinned in CI) | `bnbagent-eval schemas --check` |
| metric catalogue ([METRICS.md](METRICS.md)) | `bnbagent-eval metrics --markdown > docs/METRICS.md` | `bnbagent-eval metrics --markdown --check docs/METRICS.md` |
| map in this guide (§4.3) | `python3 scripts/render_source_map.py` | `python3 scripts/render_source_map.py --check` |

## 6. On-chain integration

User-facing flows are in [ONCHAIN.md](ONCHAIN.md) and [SERVICE.md](SERVICE.md).

| Scenario | Modules | Notes |
|---|---|---|
| S1 ERC-8004 | `chain/erc8004.py` (`MetricsPublisher`), CLI `publish` | Entries keyed by `suite_hash` in the registration file's `metrics` list; other keys preserved; written with `set_agent_uri` (the SDK's `register_agent` drops unknown keys); report uploaded first. |
| S2 ERC-8183 | `chain/erc8183.py` (`EvalGate`, `SqliteGateState`, `DisputeAdvisor`, `VoterAssistant`), `chain/evidence.py`, CLI `watch` / `review` | The suite is pinned in `success_criteria` as `eval_suite:<uri>#<suite_hash>` and loaded as untrusted. The gate is fire-once and resends an already-evaluated deliverable on a retryable failure. Watcher semantics: `False` / `{"retry": True}` = retry, `None` = done. |
| S3 x402 | `server/` (`app.py`, `policy.py`, `store.py`, `payment.py`, `settlement.py`), `client.py` `RemoteBackend`, CLI `serve` | The quote is bound to `request_hash`; nonces are reserved atomically in sqlite (one winner under concurrency); idempotency keys are scoped per payer; credits are single-use. |

- **SDK adapter** (`integrations/bnbagent`): `compat.SUPPORTED_RANGE` (`>=0.5.1,<0.7`, the same
  range as the `[bnbagent]` extra) is checked on import. `compat.wallet_env_kwargs` wraps the
  SDK's private `AgentConfig._wallet_kwargs_from_env`; replace it when the SDK exposes a public
  helper.
- **Wanted from the SDK:** a signing-policy extension for the `bnbagent-eval` EIP-712 domain;
  raw-digest signing on wallets, for the EIP-191 fallback; a public helper for reading wallet
  settings from the environment; an SDK `SettlementPort` (only the facilitator settles, and its
  nonce state is not queryable).
- **Evidence** (`EvalEvidenceV1`): a JSON object within a byte budget, plus an ABI form. The
  attestation signs status and `scoreBps`, so a provider cannot turn a signed `fail` into `pass`.
- **Testnet:** opt-in scripts in `tests/testnet/` ([TESTNET.md](TESTNET.md)); they have not been
  run against BSC testnet.

## 7. Security invariants

These must hold after every change. [design/security.md](design/security.md) has the threat
model and the reasoning; `tests/security/`, `tests/unit/chain/test_chain_security.py` and
`tests/unit/server/test_service_security.py` hold the regression tests.

- **Agreed (S2) suites are untrusted:** they load with `refs=False` and pass
  `check_negotiated_suite`, so no `custom.python` and no foreign judge `base_url`.
- **Service policy** (`server/policy.py`): built-in metrics only; `rule` and `judge` kinds by
  default; no `$ref`, `suite_ref`, simulated users, `custom.python`, `code.*` or `agentic`;
  bounded judge settings and per-metric `samples`; public http(s) targets only.
- **Egress** (`net.py`): connections go to the IP that passed the public-address check (no DNS
  rebinding); IPv4-in-IPv6 forms are unwrapped; response bodies are capped (10 MB by default).
- **Payments:** the server re-prices every request; the quote is bound to `request_hash`;
  signatures are recovered and must equal `from`; the validity window is checked; nonces are
  atomic and survive restarts; a settlement failure releases the nonce unless the chain says it
  was used; credits use the quoted asset.
- **Code sandbox** (`metrics/code/sandbox.py`): user / net / mount / pid namespaces and
  Landlock, rlimits, a scrubbed environment, no `shell=True`, capped output. It refuses to run
  (`sandbox_unavailable`) when isolation is missing.
- **Secrets** never enter reports, provenance or logs. Log redaction covers keys, bearer tokens,
  dict reprs and `X-PAYMENT` bodies.
- **Judge prompts:** every untrusted field is fenced. Never interpolate case text outside a
  fence.

## 8. Testing and CI

[design/testing.md](design/testing.md) describes the suite in detail. In short:

| Folder | What |
|---|---|
| `tests/contract/` | registry-wide contract tests over every registered (metric, method), driven by `catalog*.py` entries; port contract tests |
| `tests/unit/` | per-module tests (metrics, judges, engine, chain, server, adapters, CLI, docs snippets) |
| `tests/parity/` | our implementations against reference libraries and recorded source-toolkit outputs |
| `tests/integration/` | loopback service, local ↔ remote parity |
| `tests/determinism/` | repeat runs, cache restart, hash sensitivity, voter replay, golden hashes |
| `tests/security/` | security regressions |
| `tests/live/` | real judge calls (`-m live_judge`); `sweep_judges.py` runs every judge method |
| `tests/vendor/` | real RAGAS / DeepEval / FMEval / AgentCore (`-m vendor`) |
| `tests/testnet/` | BSC testnet flows (`-m testnet`, manual) |

The `vendor`, `live_judge`, `testnet` and `calibration` markers are deselected by default.

| Command (from `eval/`) | Purpose |
|---|---|
| `pytest -q` | the offline suite |
| `ruff check . && ruff format --check . && lint-imports` | style and import contracts |
| `pytest -q --cov=bnbagent_eval --cov-report=json:coverage.json && python scripts/coverage_gates.py coverage.json` | 95% line-coverage gates on `chain/`, `ports.py`, `hashing.py`, `metrics/{text,format,ops,onchain}`, `server/{payment,policy}.py` |
| `bash scripts/check_core_only.sh` | install with no extras; no SDK / web3 imported; the suite passes |
| `python scripts/check_standalone.py` (in a `[standalone]` venv without the SDK) | one run of each standalone feature ([STANDALONE.md](STANDALONE.md)) |
| `python3 scripts/check_hash_stability.py` | identical hashes on 3.10 / 3.11 / 3.12 |
| `python3 scripts/check_wheel.py` | wheel contents |
| `EVAL_JUDGE_PROVIDER=bedrock EVAL_JUDGE_MODEL=... python tests/live/sweep_judges.py` | live sweep of every judge method (paid) |
| `EVAL_VENDOR_CONTRACT=1 pytest -q tests/contract` | contract scoring of the FMEval methods (needs `[fmeval]`); RAGAS, DeepEval and AgentCore run against the real libraries in `tests/vendor/` |

### 8.1 Reference oracles

| Our implementation | Oracle | Test | Runs |
|---|---|---|---|
| ROUGE (`metrics/text/overlap.py`) | rouge-score | `tests/parity/test_text_libraries.py` | every run (`[dev]`) |
| Porter stemmer, original + NLTK modes (`metrics/tokens.py`) | NLTK | same | every run |
| Penn Treebank tokenizer (`metrics/tokens.py`) | NLTK `word_tokenize` | same | every run |
| text / format / ops metrics | recorded ETK / AgentSight outputs | `tests/parity/test_text_parity.py` | every run |
| STED (`metrics/ops/sted.py`) | the authors' `sted` package | `tests/parity/test_sted_parity.py` | nightly (`reference-parity-nightly`) |
| BERTScore (`metrics/text/bertscore.py`) | bert-score | `tests/parity/test_bertscore_parity.py` | nightly |

CI is `ci/github-eval-ci.yml` (installed in the SDK repository as
`.github/workflows/eval-ci.yml`). On every push or pull request touching the package it runs
`lint` (with the generated-file checks), `test` (Python 3.10–3.12, core-only and with all
extras), `bnbagent` (against the in-repo SDK `python/`), `coverage`, `determinism`,
`isolation` (separability plus a diff guard), `build` and `exporters`. Nightly it runs
`reference-parity-nightly`, `vendor-nightly` and `live-nightly` (only when AWS credentials are
configured).

## 9. Common tasks

### Adding a metric

A metric is one class registered under a canonical ID `<category>.<name>`. The registry
discovers metrics automatically: put the module in `bnbagent_eval/metrics/<category>/` (a public
module name, no leading underscore) and decorate the class with `@register`.

**Rule (deterministic) metric:**

```python
from ...types.case import EvalCase
from ..base import BaseMetric, Kind, MethodSpec, MetricContext, MetricSpec, Outcome
from ..registry import register


@register
class MyMetric(BaseMetric):
    spec = MetricSpec(
        id="text.my_metric",
        category="text",
        description="One sentence, < 80 chars.",
        methods=(
            MethodSpec("rule", Kind.RULE, required_inputs=frozenset({"output", "expected"})),
        ),
        params_schema={"type": "object", "properties": {...}, "additionalProperties": False},
        default_threshold=0.5,  # from the source evaluator
        sources=("agentsight:SomeEvaluator",),
    )

    def _evaluate_rule(self, case: EvalCase, ctx: MetricContext) -> Outcome:
        return Outcome(score=..., reason=..., details={...})
```

`BaseMetric.evaluate` handles missing inputs, deadlines, normalization (`raw_range`,
`inverted`), thresholds and exceptions. Never raise for bad case data: return an `Outcome` (an
exception inside the method becomes `status="error"`).

**Native judge metric:**

1. A prompt template in `bnbagent_eval/judges/prompts/<category>/<name>.py`:

   ```python
   from .. import FIVE_TIER, PromptTemplate, five_tier_schema

   V1 = PromptTemplate(
       metric_id="quality.clarity",
       version="v1",
       system=SYSTEM_V1,                      # the source system prompt, verbatim
       parts={"simple": "...{actual_output}", "task_simple": "...", ...},
       output_schema=five_tier_schema({...extra fields...}),
       labels=FIVE_TIER,                     # label -> score, or None if score_parsed is custom
       domain_rubric_template="...{domain_rubric}",
       source="agentsight:clarity_v0",
   )
   VERSIONS = {"v1": V1}
   ```

   - Port AgentSight `prompt_templates/<name>/<name>_v0.py` **verbatim** (system prompt,
     `PROMPT_PARTS`, `DOMAIN_RUBRIC_TEMPLATE`, labels); generating the module from the source
     file avoids typos.
   - Placeholders in `parts`: `{input}`, `{actual_output}`, `{expected_output}`,
     `{conversation_history}`, `{contexts}` (override `JudgeMetric.fields` for more).
   - Modes: `simple`, `with_expected`, `conversation` (+ `task_<mode>`); a template may define a
     subset.
   - For a metric with no AgentSight prompt, write a new prompt in the same 5-tier style with
     `source="bnbagent-eval:new (cf. deepeval:<metric>)"`.
   - Never change a released template in place: add `V2` and bump `default_version`; earlier
     versions stay loadable for locked suites.

2. The metric in `bnbagent_eval/metrics/<category>/<name>.py`:

   ```python
   from ...judges.prompts.quality import clarity as prompts
   from ..base import MetricSpec
   from ..judge_base import JudgeMetric, judge_params, native_method
   from ..registry import register


   @register
   class Clarity(JudgeMetric):
       templates = prompts.VERSIONS
       spec = MetricSpec(
           id="quality.clarity",
           category="quality",
           description="...",
           methods=(native_method("output", optional=("input", "conversation")),),
           params_schema=judge_params({"extra_param": {"type": "string"}}),
           default_threshold=0.5,
           sources=("agentsight:ClarityEvaluator",),
       )
   ```

   - Override `score_parsed(parsed, template)` for non-label scoring (weighted sub-scores,
     counts).
   - Override `_evaluate_native` when the source makes several calls: call
     `await self.ask(case, ctx, template, mode, fields, tag="<call name>")` per call, merge
     provenance, and return `self.outcome(...)`.
   - Polarity: the score is always higher-is-better. If the judge rates "badness", invert in
     `score_parsed` and keep the raw rating in `details`.
   - `required_inputs` are the inputs without which the metric cannot run; everything else is
     optional.

**Then, for every new metric:**

1. Contract inputs in `tests/contract/catalog_<category>.py` (merged automatically): a best and a
   worst case (with scripted judge replies for judge methods) that must score 1.0 and 0.0. The
   registry-wide contract tests then check fencing, sampling, budget, missing inputs, exceptions
   and round-trips.
2. Golden tests in `tests/unit/metrics/` (judge metrics: `test_<category>_judge.py` with
   `FakeJudge`): label mapping, every special formula from the source, input-mode selection,
   optional inputs changing the prompt, parameter handling.
3. A row in `tests/fixtures/catalog_index.yaml` and in
   [design/metric-catalog.md](design/metric-catalog.md); if it has a source, a row in
   [design/source-map.md](design/source-map.md).
4. Regenerate METRICS.md, the prompt hashes and §4.3 (§5); `tests/unit/test_catalog_doc.py` fails
   while METRICS.md is stale.
5. From `eval/`: `ruff format . && ruff check . && lint-imports && pytest`.

### Adding a method to an existing metric

For a vendor bridge or a local model:

1. Put it in `metrics/vendors/<name>.py` with `register_method(..., params=...)`, importing the
   library lazily.
2. Add aliases with `registry.add_alias`.
3. For a new provider: an extra in `pyproject.toml` and a `PROVIDERS` entry (§4.5).
4. Test with fake modules (`monkeypatch.setitem(sys.modules, ...)`), plus an opt-in
   `tests/vendor/` test.

### Other tasks

- **Change a prompt:** add a template version and keep the earlier one in `VERSIONS`; re-pin the
  prompt hashes; run `tests/live/sweep_judges.py` for the affected metric.
- **Change scoring:** bump `MetricSpec.version`; update the contract and parity tests; re-pin the
  determinism golden (`check_hash_stability.py --write-golden`) and explain the change in the
  commit.
- **Add a judge provider:** `judges.providers.register_provider(name, factory)`; the factory
  returns a `JudgeModel` with an async `complete()`, and optionally `embed()`.
- **Add a trace format:** `adapters.traces.register_adapter(adapter)` with `detect()` and
  `to_session()`, plus golden fixtures in `tests/fixtures/traces/`.
- **Support a new SDK release:** widen `compat.SUPPORTED_RANGE` and the `[bnbagent]` extra
  together; run the `bnbagent` CI leg and the port contract tests
  (`tests/contract/test_ports.py`).
- **Release:** update `CHANGELOG.md` and `bnbagent_eval/_version.py`; run
  `python -m build && twine check dist/*` and `python3 scripts/check_wheel.py`.

## 10. Known gaps

**Not built:**
- `benchmark_ref` (reserved; the engine and the service reject it) and fork simulation;
- a local `EvalClient.quote()`;
- payer-signed async job reads;
- a live-judge requirement for voters;
- tolerance-based comparison with the provider's evidence;
- `CommercePort.get_description`;
- a per-metric timeout;
- multimodal judge requests;
- a `native` judge method for `onchain.intent_match` (rule only);
- recorded source outputs for judge metrics (deterministic metrics only).

**Open design points:** `retry_or_decline` as the single pre-submit fail action; truncation by
characters rather than tokens; the `SignerPort.sign_typed_data` signature; the evidence trimming
order.

**Python 3.10 / 3.11:** pass `gate.__call__` (or use `watch()`) when handing an `EvalGate` to
the SDK watcher.

**External checks:** the x402 `permit2-exact` witness type string is unconfirmed against the live
b402 facilitator; the testnet flows need a funded account; licence sign-off is pending (this
package is Apache-2.0 inside the MIT SDK repository).

## 11. Conventions

- Python ≥ 3.10, `from __future__ import annotations`, line length 99, `ruff` and
  `import-linter` clean, concise docstrings.
- Record every port's source in its module docstring and in `MetricSpec.sources`; keep prompt
  text verbatim where ported.
- No secrets or PII in code, fixtures or docs: synthetic addresses and well-known dev keys only,
  marked `secret-scan: allow`.
- Tests are offline by default: network is blocked except loopback (`tests/conftest.py`), and
  judges are `FakeJudge` scripts.
