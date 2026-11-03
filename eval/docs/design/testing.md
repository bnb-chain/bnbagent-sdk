# Test design

How `bnbagent-eval` is tested: the test levels, the layout, the shared test doubles, the
contract suite over the registry, parity, determinism, the opt-in suites and CI. It covers
[architecture.md](architecture.md), [metric-catalog.md](metric-catalog.md) and
[implementation.md](implementation.md); the commands are in the
[maintainer guide](../MAINTAINER_GUIDE.md).

## 1. Objectives

1. **Correctness:** every metric and method computes what its spec says, matching its source
   where one exists.
2. **Consistency:** every metric honours the shared contract (range, per-method polarity,
   statuses, serialization).
3. **Reproducibility:** the same request and the same judge outputs give the same `result_hash`
   locally, remotely, on another Python version and on a voter's replay.
4. **Chain safety:** evidence, metrics entries and attestations are bound, size-limited and
   verifiable; forged, replayed or mismatched evidence is rejected.
5. **Service safety:** untrusted suites and payments cannot cause code execution, SSRF, ReDoS,
   XXE, double charging or free evaluations ([security.md](security.md)).
6. **Isolation:** the default run needs no network, AWS or chain; the core imports neither
   `bnbagent` nor `web3`.

## 2. Test levels

| Level | Marker | Default run | Network | Scope |
|---|---|---|---|---|
| Unit | – | yes | none | every module |
| Contract | – | yes | none | every `(metric, method)` in the registry; every port implementation |
| Parity | `parity` | yes | none | our metrics vs recorded or installed reference implementations |
| Integration | `integration` | yes | loopback | local ↔ remote through a live uvicorn server, x402 with local keys |
| Determinism | – | yes | none | repeat runs, cache restart, hash sensitivity, voter replay, golden hashes |
| Vendor | `vendor` | no | none by default | real RAGAS / DeepEval / FMEval / AgentCore libraries |
| Live judge | `live_judge` | no | judge API | real judge calls (paid) |
| Testnet | `testnet` | no | BSC testnet | S1 and S2 end to end, with a funded wallet |
| Calibration | `calibration` | no | judge API | reserved; judge calibration runs through `bnbagent-eval calibrate` |

Markers are registered with `--strict-markers`; `pyproject.toml` deselects `vendor`,
`live_judge`, `testnet` and `calibration` by default, and sets `asyncio_mode = "auto"`.

## 3. Layout

```
eval/tests/
├── conftest.py      # no_network (autouse), suite_factory, case_factory
├── fixtures/        # catalog_index.yaml, prompt_hashes.json, injection/, parity/, schemas/, traces/
├── unit/            # per module; subfolders adapters, analysis, authoring, chain, generation,
│                    #   integrations, metrics, reporting, server
├── contract/        # metric contract (catalog*.py entries) and port contract (port_worlds*.py)
├── parity/          # reference implementations and recorded values
├── integration/     # loopback service
├── determinism/     # hash stability (_fixture.py, golden_hashes.json)
├── security/        # hardening and redaction regressions
├── vendor/  live/  testnet/   # opt-in
```

## 4. Fixtures and test doubles

| Double | Purpose |
|---|---|
| `no_network` (autouse, `tests/conftest.py`) | blocks every socket connection except loopback |
| `suite_factory`, `case_factory` | builders for suites and cases |
| `FakeJudge` (`bnbagent_eval.judges.providers.fake`) | scripted judge replies (a mapping by metric ID, a sequence, or a callable); records every request; embeds with deterministic vectors |
| `bnbagent_eval.testing.fakes` | in-memory ports: `MemoryStorage`, `FakeRegistry`, `FakeCommerce`, `FakePolicy`, `FakeChainReader`, `FakeSettlement`, `FakeSigner`. The chain workflows are tested against these with no SDK installed. |
| `contract/port_worlds*.py` | one *world* per port implementation (fakes, jsonrpc over `httpx.MockTransport`, bnbagent over SDK mocks), with capabilities that gate write checks |
| `unit/chain/conftest.py` | `signer` / `other_signer` (`LocalKeySigner`), `job_subject`, a sealed `response` |
| `unit/server/conftest.py` | `make_app`: the service app on a temporary sqlite store |
| `fixtures/traces/` | one sample per trace format (OTel GenAI, AgentCore ADOT, Langfuse Strands / LangChain / LangGraph, LangSmith, SmolAgents, Tau-Bench, CX0, EB-Habitat, native) |
| `fixtures/injection/payloads.json` | prompt-injection payloads (fake instructions, fake verdicts, fake fence tags) |

## 5. Contract tests

**Metric contract** (`contract/test_metric_contract.py`, `contract/test_contract_extras_provenance.py`):
parametrized over every `(metric, method)` in the registry, with a best and a worst case per
metric from `contract/catalog*.py`.

| Assertion | Tests |
|---|---|
| The spec is complete: ID pattern, category equals the prefix, methods, version, description, sources, default threshold | `test_spec_complete`, `test_default_threshold_and_method_names` |
| Aliases resolve uniquely to `(id, method)` | `test_aliases_resolve_uniquely` |
| The best case scores 1, the worst 0, with `status=scored` and full provenance | `test_best_and_worst` |
| Removing each required input gives `skipped` / `missing_input`, never an exception | `test_missing_inputs_skip` |
| An exception inside the method gives `error` / `metric_error` | `test_exceptions_become_errors` |
| Polarity per method: inverted methods map the raw worst value to 0 and keep the raw value | `test_polarity_per_method`, `test_inverted_methods_keep_raw` |
| `MetricResult` round-trips, its canonical JSON is stable, rule methods are deterministic | `test_roundtrip_and_determinism`, `test_full_provenance_and_canonical_json` |
| Every untrusted field is inside the fence; the nonce is deterministic and differs across samples | `test_untrusted_fields_are_fenced`, `test_no_untrusted_text_outside_fences`, `test_nonce_differs_across_samples` |
| A method whose extra is missing raises `MissingExtraError` with the install command | `test_missing_extra_raises_with_hint` |
| `params_schema` rejects unknown and wrongly typed params at resolution | `test_params_schema_rejects_unknown`, `test_unknown_param_rejected_at_resolution`, `test_wrongly_typed_param_rejected` |
| The registry holds exactly the IDs of `fixtures/catalog_index.yaml` | `test_catalogue_coverage` |
| `samples=k` makes exactly k judge calls; aggregation takes the lower median | `test_samples_and_lower_median`, `test_exact_calls_and_lower_median` |
| An exhausted budget gives `skipped` / `budget_exhausted` without a judge call | `test_budget_exhaustion_skips` |
| Methods with `service_allowed=False` are rejected by `UntrustedSuitePolicy` | `test_policy_rejects_service_disallowed` |
| `input_mode` auto-detection selects the matching template | `test_auto_mode_detection` |

Vendor methods get the missing-input and exception checks in every run (they fire before the
vendor library is touched; AgentCore methods, which need a trajectory the shared contract case
lacks, are covered by their unit tests). The scoring checks run only with
`EVAL_VENDOR_CONTRACT=1` and the extras, for the FMEval methods; RAGAS, DeepEval and AgentCore
score with an LLM judge or a cloud service and are scored in `tests/vendor`. Methods the scripted inputs cannot drive to 1 or 0
(judge embeddings, local models) are listed in `UNSCRIPTED` and covered by unit tests.

**Port contract** (`contract/test_ports.py`): one parametrized suite per port, run against every
world. Every implementation must give the same return types, error mapping and read behaviour;
read-only adapters skip the write checks.

## 6. Unit tests by component

| Component | Where | What is checked |
|---|---|---|
| Types, hashing, schemas | `unit/test_types.py`, `test_hashing.py`, `test_seal_fastpath.py`, `server/test_schemas.py` | canonical JSON (key order, float quantization, NaN rejected, non-ASCII kept); what `result_hash` and `response_hash` include; round trips; schema files current |
| Suites | `unit/test_suite.py` | `$ref` inlining into `suite_hash`, remote `$ref` rejected, lock mismatches, field paths in `SuiteError` |
| Deterministic metrics | `unit/metrics/test_text.py`, `test_format.py`, `test_ops_agent.py`, `test_rule_edges.py`, `test_sted.py`, `test_metric_edge_cases.py` | golden tables per metric; CJK tokenization; ReDoS pattern times out; XXE and `schemaLocation` rejected; TED / STED; trajectory match modes |
| Judge layer | `unit/test_judges.py` | provider request mapping, retries, structured parsing and repair, judge precedence, cache keys and sqlite cache, fencing |
| Judge metrics | `unit/metrics/test_*_judge.py`, `test_prompt_snapshots.py` | label → score mapping and source formulas; prompt snapshots pinned in `fixtures/prompt_hashes.json` (a template edit without a version bump fails); input modes, `domain_rubric`, injection fixtures |
| Source parity of ported behaviour | `unit/metrics/test_source_parity.py` | the behaviours of the source toolkits recorded in [source-map.md](source-map.md) |
| On-chain metrics | `unit/metrics/test_onchain.py`, `test_onchain_edges.py`, `unit/chain/test_onchain_enrich.py` | receipts, intent matching, balance deltas, approvals, spend policy, gas; enrichment pins the block |
| Code and artifacts | `unit/metrics/test_code_metrics.py`, `test_artifact_integrity.py` | sandboxed test runs, time-limit kill, network blocked; hash, media type, size cap |
| Local models and vendors | `unit/metrics/test_local_models.py`, `test_vendor_*.py`, `unit/test_third_party.py` | input mapping and result mapping with the libraries mocked |
| Trace adapters | `unit/adapters/` | one golden `Session` per format, auto-detection, robustness, trajectory rendering |
| Engine and targets | `unit/test_runner_client.py`, `test_engine_misc.py`, `test_aggregate.py`, `test_targets_datasets.py`, `test_agent_targets_compare.py`, `test_simulated_target.py` | concurrency, isolation of failures, budgets, deadlines, truncation, aggregation (Welford vs `statistics`, hypothesis), the verdict truth table; callable / HTTP (streaming) / A2A / MCP / ERC-8004 / simulated-user targets; `compare` and `aggregate_evaluations` |
| Chain workflows | `unit/chain/` | attestation sign / verify and every rejection; suite criterion and `suite_from_job`; `MetricsPublisher` upsert preserving other keys; `EvalGate`; evidence size and trimming; `DisputeAdvisor`; `VoterAssistant` binding checks; human review |
| Adapters | `unit/integrations/` | the bnbagent adapter (type mapping, error mapping, `compat.py` version checks, the SDK's real `funded_job_watcher`) and the JSON-RPC adapter |
| Service and x402 | `unit/server/` | endpoints and error codes, the untrusted-suite policy and limits, quotes, `X402Gate` (EIP-3009 and Permit2; replays sequential and concurrent; restart), idempotency, credits, jobs, no-retention, agent card |
| Security regressions | `unit/server/test_service_security.py`, `unit/chain/test_chain_security.py`, `security/` | the controls of [security.md](security.md) |
| Analysis, authoring, generation | `unit/analysis/`, `unit/authoring/`, `unit/generation/`, `unit/test_confidence_insights.py`, `test_authoring_analysis.py`, `test_redteam.py` | confidence, insights, diagnosis, recommend, calibration statistics, error injection, eval maker, RAG / agentic generation and filters, simulated users, red-teaming |
| Reporting and privacy | `unit/reporting/`, `unit/test_cli_reporting.py`, `test_report_encryption.py` | JSON / Markdown / console / CSV, exporters (fakes), hash-only reports, redaction, encryption |
| Configuration and CLI | `unit/test_foundations.py`, `test_config_file.py`, `test_cli_*.py` | `EvalConfig` from env and file, `_optional.require`, log redaction, every subcommand and exit codes |
| Isolation | `unit/test_isolation.py` | `import bnbagent_eval` (in a subprocess) loads no extra, SDK or web3 and makes no network call; the import-linter contracts hold |
| Documentation | `unit/test_docs_snippets.py`, `test_catalog_doc.py`, `test_maintainer_guide.py` | `<!-- doctest -->` snippets of the guides run offline, relative links resolve, `docs/METRICS.md` and the maintainer guide's source map are current |

## 7. Parity

| Our implementation | Reference | Test | Runs |
|---|---|---|---|
| text / format / ops metrics | values recorded from the reference libraries (`parity/record_text.py`, in a separate environment) into `fixtures/parity/text.json` | `parity/test_text_parity.py` | every run |
| ROUGE, Porter stemmer (original and NLTK modes), Penn Treebank tokenizer | rouge-score, NLTK (`[dev]` only) | `parity/test_text_libraries.py` | every run |
| STED (`metrics/ops/sted.py`) | the authors' `sted` package | `parity/test_sted_parity.py` | nightly |
| BERTScore (`metrics/text/bertscore.py`) | bert-score | `parity/test_bertscore_parity.py` | nightly |

Judge metrics have no recorded oracle: their unit tests feed scripted replies through
`FakeJudge` and check the source formulas (weights, sub-scores, aggregation), and
`unit/metrics/test_source_parity.py` pins the source behaviours listed in
[source-map.md](source-map.md).

## 8. Determinism and reproducibility

`determinism/test_determinism.py` and `scripts/check_hash_stability.py`:

1. The same request with `FakeJudge` gives an identical `result_hash` over 10 runs, in a fresh
   process, in any scoring order, and equal to the pinned `determinism/golden_hashes.json`.
2. `check_hash_stability.py` runs the fixture (`determinism/_fixture.py`) on Python 3.10, 3.11
   and 3.12 in throwaway `uv` environments and compares with the golden hashes.
3. With the sqlite judge cache, a new runner and a restarted process give the same hash.
4. Local ↔ remote: `EvalClient()` and `EvalClient.remote(...)` against a loopback server give the
   same `result_hash` (`integration/test_local_remote_parity.py`); `response_hash` may differ.
5. Any hash-relevant change (prompt version, judge model, temperature, samples, metric version,
   a `$ref` file's content) changes both `result_hash` and `suite_hash`; the `$ref` path does not.
6. A voter's replay of the provider's evaluation, given the same judge outputs, gives the same
   `result_hash`.

## 9. Opt-in suites

| Suite | Run | Content |
|---|---|---|
| `vendor` | `pytest -m vendor tests/vendor` with the vendor extras | one run per vendor method against the real library, with a `FakeJudge` answering the vendor's schema (or a real Bedrock judge with `EVAL_VENDOR_BEDROCK_MODEL`) |
| `live_judge` | `EVAL_LIVE_BEDROCK_MODEL=... pytest -m live_judge tests/live` | real judge calls: good answers must score above bad ones. `tests/live/sweep_judges.py` runs every judge method against a real model |
| `testnet` | `EVAL_TESTNET=1 ... pytest -m testnet tests/testnet` ([TESTNET.md](../TESTNET.md)) | S1 evaluate → publish → read back; S2 client review (dry run) and provider gate on a funded job. Never run in CI |

Judge calibration against human labels uses `bnbagent-eval calibrate`; the recorded run is
`benchmarks/calibration/REPORT.md`.

## 10. CI

The workflow is `eval/ci/github-eval-ci.yml`, installed in the SDK repository as
`.github/workflows/eval-ci.yml`. It runs on pushes and pull requests that touch `eval/**`,
`python/**` or the workflow, nightly, and on manual dispatch.

| Job | When | Steps |
|---|---|---|
| `lint` | push / PR | `ruff check`, `ruff format --check`, `lint-imports`; `bnbagent-eval schemas --check` and `metrics --markdown --check docs/METRICS.md` (FastAPI pinned, since `openapi.json` depends on it) |
| `test` | push / PR | Python 3.10, 3.11, 3.12 × extras `[dev]` and `[dev,format,evm,crypto,server]`; default markers |
| `bnbagent` | always | contract, adapter and chain tests against the SDK in the same repository (`python/`) |
| `coverage` | push / PR | coverage report and `scripts/coverage_gates.py` |
| `determinism` | always | `scripts/check_hash_stability.py` |
| `isolation` | push / PR | `scripts/check_core_only.sh` (copy outside the repository, no extras, suite passes); a `[standalone]` install without the SDK runs `scripts/check_standalone.py`; on PRs a diff guard allows only `eval/` and the workflow to change |
| `build` | push / PR | `python -m build`, `twine check`, `scripts/check_wheel.py` (only `bnbagent_eval/**`, prompts included, no tests, Apache-2.0 metadata, LICENSE and NOTICE) |
| `exporters` | push / PR | `tests/unit/reporting` with real OpenTelemetry and Prometheus objects |
| `vendor-nightly` | nightly / manual | `-m vendor tests/vendor` with `[ragas,deepeval]` |
| `live-nightly` | nightly / manual, when `EVAL_LIVE_BEDROCK_MODEL` is configured | `-m live_judge tests/live` |
| `reference-parity-nightly` | nightly / manual | STED and BERTScore parity against the authors' code |

**Coverage gates** (`scripts/coverage_gates.py`): 95% line coverage for `metrics/text/`,
`metrics/format/`, `metrics/ops/`, `metrics/onchain/`, `chain/`, `ports.py`,
`server/payment.py`, `server/policy.py` and `hashing.py`.

**Import contracts** (`[tool.importlinter]` in `pyproject.toml`): only
`integrations.bnbagent` imports `bnbagent` / `web3`; `chain`, `server` and `engine` never import
an adapter; layers import downward only.

Locally, `scripts/verify.sh` at the repository root runs ruff, the import contracts, the default
test suite, the schema check and the secret scan.

## 11. Traceability: requirements and controls → tests

| Requirement / control | Tests |
|---|---|
| All ETK and AgentSight capabilities ([metric-catalog.md](metric-catalog.md)) | contract `test_catalogue_coverage`, the metric contract, §7 parity, `test_source_parity.py` |
| One consistent metric model | metric contract; aliases; per-method polarity |
| S1 ERC-8004 | `unit/chain/` publisher tests; testnet S1 |
| S2 ERC-8183 | `unit/chain/` gate, advisor, voter and evidence tests; the real SDK watcher in `unit/integrations/`; §8.6; testnet S2 |
| S3 HTTP + x402 | `unit/server/`; `integration/`; §8.4 |
| Verdict precedence and stage actions | the verdict truth table in the engine tests |
| Prompt injection | contract fencing tests, judge-layer fencing, injection fixtures, `redteam.prompt_injection` |
| Metric-breaking games | truncation, required errors and `max_error_rate` in the engine tests |
| Lenient suite, evidence forgery, replay | `suite_from_job`, `VoterAssistant` binding and attestation tests; `test_chain_security.py` |
| Disagreeing evaluators | `compare` / `aggregate_evaluations` tests |
| Untrusted suites and requests on the service | `unit/server/` policy tests, contract `test_policy_rejects_service_disallowed`, `test_service_security.py` |
| Payment fraud | quote binding, nonces (concurrent and restart), `authorizationState`, idempotency scope, credits in `unit/server/` |
| Egress (SSRF, DNS rebinding, response size) | `unit/server/` policy and transport tests, artifact fetch tests |
| Code sandbox | `test_code_metrics.py`, `security/test_hardening.py` |
| Privacy and key handling | redaction, encryption and no-retention tests; `security/` |
| Reproducibility | §8 |
| Light, portable, independent of the SDK | `test_isolation.py`; CI `isolation` (core-only, standalone, diff guard); import contracts; port contract suite |
| Language-neutral API (JSON Schemas, OpenAPI) | `bnbagent-eval schemas --check`; `server/test_schemas.py` |
| Apache-2.0 packaging | `scripts/check_wheel.py` in CI `build` |
