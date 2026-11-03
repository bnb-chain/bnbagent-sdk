# Changelog

## 0.1.0 (unreleased)

Initial release.

**Evaluation core**
- One evaluation call, `EvalClient.evaluate` (and `evaluate_sync`), for local and remote
  backends; `compare()` and `aggregate_evaluations()`.
- Suites in YAML or JSON with local `$ref`, weights, thresholds, value limits, required metrics,
  a pass rule with ordered verdict precedence, and stage actions (`dev`, `registration`,
  `pre_submit`, `review`, `vote`).
- Canonical hashing: `suite_hash`, `request_hash`, `result_hash` and `response_hash` (keccak over
  canonical JSON), identical on Python 3.10–3.12; suite lint and version locks.
- Targets: callable, HTTP (JSON and streaming with time to first token), A2A, MCP, ERC-8004
  agents and simulated users; repeated runs; `dataset_ref` with keccak verification.
- Trace adapters with format detection: OpenTelemetry GenAI, AgentCore ADOT, Langfuse,
  LangSmith, smolagents, Tau-Bench, CX0, EB-Habitat and the native format.
- Budgets, deadlines, truncation policy and cost reporting; configuration from `EVAL_*`
  variables or a YAML file (`EVAL_CONFIG`, `--config`).

**Metrics**
- 99 canonical metrics in 12 categories (`text`, `format`, `ops`, `quality`, `safety`, `rag`,
  `conversation`, `agent`, `onchain`, `redteam`, `code`, `custom`) covering the AWS Gen AI
  Evaluation Toolkit and AgentSight, with aliases for their names.
- Rule, judge, local-model and third-party methods per metric. Implementations in the package
  include ROUGE, METEOR, BLEU, BERTScore and STED consistency scoring; reference libraries are
  test oracles only.
- `onchain.*` metrics over transaction steps and receipts; `code.*` metrics in a sandbox;
  `custom.python`, `custom.rubric`, `custom.requirements_compliance`, `custom.judge_prompt` and
  `custom.human_review`.

**Judges**
- OpenAI-compatible, Anthropic and Bedrock providers with per-provider default models and a
  provider registry.
- Fenced prompts, structured output with one repair re-ask, lower-median sampling, retries,
  concurrency limits, a judge cache, versioned prompt templates and provenance.
- Calibration against human labels (`benchmark_agreement`, `rank_judges`, `bnbagent-eval
  calibrate`), including balanced datasets built by error injection.

**On-chain**
- Chain access through ports (registry, commerce, policy, storage, signer, chain reader,
  settlement, asset catalogue, payer) with in-memory fakes and a shared port contract suite.
- Adapters for the bnbagent SDK (`[bnbagent]`) and for plain read-only JSON-RPC.
- S1: `MetricsPublisher` for ERC-8004 registration files (`bnbagent-eval publish`).
- S2: ERC-8183 suite pinning, `EvalGate` for providers, `DisputeAdvisor` and `VoterAssistant`
  (`bnbagent-eval watch`, `review`); job-bound evidence and EIP-712 attestations.
- Opt-in BSC testnet flows.

**Service**
- HTTP eval service (`[server]`, `bnbagent-eval serve`): synchronous and asynchronous
  evaluation, quotes, an untrusted-suite policy and limits, idempotency, credits, a
  no-retention mode, an agent card, JSON Schemas and OpenAPI.
- x402 payments (EIP-3009 and Permit2) with facilitator settlement; `EvalClient.remote` with
  payer support and response verification.

**Generation and analysis**
- RAG and agentic test-case generation, case filters, simulated users and personas,
  red-teaming (native or PyRIT), error injection.
- Eval maker (`bnbagent-eval make`), metric recommendation, failure diagnosis (`run
  --diagnose`) and insights.

**Reporting**
- JSON, Markdown, console and CSV reports; OpenTelemetry, Prometheus and MLflow exporters
  (`[exporters]`); privacy modes and report encryption (`[crypto]`).

**Tooling**
- `bnbagent-eval` CLI: `metrics`, `run`, `make`, `lint`, `publish`, `review`, `watch`, `serve`,
  `calibrate`, `schemas`.
- Offline test suite with contract, parity, determinism and security tests; coverage gates;
  core-only and standalone separability checks; import-linter layering; a generated metric
  catalogue (`docs/METRICS.md`) and source map, checked in CI.
