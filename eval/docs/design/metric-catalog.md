# Metric catalogue

The design catalogue of the 99 canonical metrics: every metric, evaluator and evaluation
capability of ETK and AgentSight in one namespace, plus the metrics that on-chain use needs
(`ops.*`, `onchain.*`, `code.*`, artifact integrity). Each row gives the methods, inputs, main
params, default threshold and sources. Appendix A maps each source item to a canonical ID.

The generated reference with every param and input is [METRICS.md](../METRICS.md); the
item-by-item source mapping with file paths is [source-map.md](source-map.md).
`scripts/audit_catalog.py` (repository root) checks this catalogue against the registry.

## 0. Conventions

### 0.1 Identifiers and kinds
- **ID** = `<category>.<name>`. Vendor names are aliases, e.g. `ragas.faithfulness` → `rag.faithfulness` with `method=ragas`.
- **Kind:** `rule` (deterministic), `judge` (LLM judge via `JudgeModel`), `model` (local ML model, an extra), `vendor` (a third-party framework, an extra). Vendor methods are never the default.
- **Methods:** the first method listed is the default. `native` = our versioned judge prompt, ported from the named source. `rule` = our deterministic code.
- **Per-method spec.** Each method declares its own required inputs, polarity, raw range and extra.
  - Example: `safety.bias` with `native` (AgentSight) is already higher-is-better, so it is not inverted.
  - With `deepeval` the source is higher-is-worse, so it is inverted (↓ in the tables).

### 0.2 Scores and values
- `score ∈ [0, 1]`, higher is better. The raw source value is kept in `details.raw`.
- Measured quantities also carry `value` + `unit`.
- `threshold` applies to `score`. `value_min` / `value_max` / `value_target` apply to `value`.
- The case's `language` field (not a param) drives tokenization (CJK) and normalization.

### 0.3 Inputs
`in` input, `out` output, `exp` expected, `ctx` contexts, `conv` conversation, `traj` trajectory,
`am` app_metrics, `runs` repeated runs, `art` artifacts, `tx` Tx steps.

### 0.4 Common parameters (judge metrics)

| Param | Meaning | Source |
|---|---|---|
| `input_mode` | `auto` / `simple` (in+out) / `with_expected` / `conversation`. In auto mode it is detected from the case, like AgentSight `detect_input_format`. | AS blackbox `utils.detect_input_format` |
| `domain_rubric` | Domain guidance appended to the system prompt | AS, every blackbox evaluator |
| `prompt_version` | Template version (`v1`… ported from AS `_v0`), pinned in the suite lock | AS `get_template(version)` |
| `samples` | k judge calls; median for scores, majority vote for labels | AS `JudgeModelOptions.num_samples` |
| `level` | `session` / `trace` / `tool` / `tool_call` evaluation level; `agentcore` method only | AgentCore levels |
| `agent_id` | Restrict to one agent of a multi-agent trace; only metrics whose prompt renders the trajectory (`agent.goal_success`, `agent.task_completion`, `agent.tool_selection`, `agent.plan_*`, `agent.mcp_*`, `redteam.prompt_injection`) and `quality.helpfulness` (recorded in details) | AS Helpfulness / extractor |
| `max_field_chars` | Truncation limit per rendered field | New |
| `threshold` | Pass threshold (a `MetricConfig` field, not a param). The defaults are in the tables below and come from the source. | AS `DEFAULT_THRESHOLD` |

## 1. `text.*`: reference-based deterministic metrics

| ID | Methods | Inputs | Params | Thr | Sources |
|---|---|---|---|---|---|
| `text.exact_match` | rule, fmeval, deepeval | out, exp | `normalize` (case, whitespace, punctuation, articles – language-aware), `case_insensitive` | 1.0 | ETK `example_exact_match`; FMEval qa_accuracy `exact_match_score`; DeepEval `exact_match`; AS AnswerCorrectness `exact` |
| `text.quasi_exact_match` | rule, fmeval | out, exp | – | 1.0 | FMEval qa_accuracy `quasi_exact_match` |
| `text.contains` | rule, fmeval | out, exp (targets) | `mode` any/all, `delimiter` (default `<OR>`), `quasi` (normalized inclusion) | 1.0 | FMEval `factual_knowledge` (+ quasi-exact variant) |
| `text.token_f1` | rule, fmeval | out, exp | always normalized (QuAC, as FMEval); details: precision, recall | 0.5 | FMEval qa_accuracy (f1, precision_over_words, recall_over_words) |
| `text.rouge` | rule, fmeval | out, exp | `variant` rouge1 / rouge2 / rougeL (default rougeL F), `use_stemmer` (default true, as in AS) | 0.7 | AS `calculate_rouge_l`, AnswerCorrectness `rouge` (thr 0.7); FMEval summarization_accuracy `rouge` (`rouge_type`) |
| `text.bleu` | rule | out, exp | `max_n` (default 4); smoothing fixed to NLTK method1, as in AS | 0.5 | AS `calculate_bleu` |
| `text.meteor` | rule, fmeval | out, exp | – (our Treebank tokens + NLTK-variant Porter stems, as FMEval) | 0.5 | FMEval summarization_accuracy `meteor` |
| `text.jaccard` | rule | out, exp | whitespace tokens; `stopwords` (keyword-overlap mode) | 0.5 | AS `calculate_jaccard`, AnswerCorrectness `jaccard` (thr 0.5), `scoring_utils.compute_keyword_overlap` |
| `text.tfidf_cosine` | rule | out, exp | – | 0.5 | AS `calculate_cosine_similarity` |
| `text.classification_accuracy` | rule, fmeval | out, exp (label) | `label_map`, `normalize`, `valid_labels`. Batch details: balanced_accuracy, precision, recall | 1.0 | FMEval `classification_accuracy` (+ sub-scores) |
| `text.length` | rule | out | `min_chars`, `max_chars`, `min_words`, `max_words` | 1.0 | ETK `ExampleTextQualityScorer.length` |
| `text.bertscore` | model, fmeval | out, exp | `model` (default deberta-xlarge-mnli), `num_layers`; our implementation (transformers loads the encoder) | 0.8 | AS `calculate_bertscore`, AnswerCorrectness `semantic` (thr 0.8, `bertscore_model`); FMEval `bertscore` (`model_type`) |
| `text.embedding_similarity` | keyword (stop-word Jaccard, default as in AS), embeddings (via `JudgeModel.embed`), glove, fasttext | out, exp (or in) | `against`, `vectors_path`; the embedding model comes from the judge config; cosine rescaled with `(s+1)/2` as in AS | 0.5 | AS `SemanticRelevanceScorer` |

## 2. `format.*`: output structure and artifacts

| ID | Methods | Inputs | Params | Thr | Sources |
|---|---|---|---|---|---|
| `format.json_valid` | rule | out | `extract_fenced` | 1.0 | AS OutputStructure (json) |
| `format.json_schema` | rule, native, deepeval | out | `schema` (local `$ref` only) | 1.0 | DeepEval `json_correctness`; AS OutputStructure `json_schema` |
| `format.regex` | rule, native, deepeval | out | `pattern`, `full_match` (default true = AS `fullmatch`), `flags`, `timeout_ms` (safe engine) | 1.0 | DeepEval `pattern_match`; AS OutputStructure `regex`; ETK `quality.prefix` |
| `format.xml` | rule, native | out | `xsd` (optional: without it, only well-formedness is checked); entities and network disabled | 1.0 | AS OutputStructure `_validate_xml` + `xml_schema` |
| `format.structure` | native | out | `expected_structure` (natural language) | 0.5 | AS OutputStructure (LLM mode, `prioritize_efficiency=False`) |
| `format.artifact_integrity` | rule | art | `media_types`, `max_bytes`; checks fetched content against `hash` | 1.0 | **New** (non-text deliverables) |

`native` on `json_schema`, `regex` and `xml` is AgentSight's LLM mode for the same check. The
source has no LLM mode for schema-less JSON; use `format.json_schema:native` or `format.structure`.

## 3. `ops.*`: operational and business metrics (these feed the ERC-8004 public metrics)

| ID | Methods | Inputs | Value | Score | Thr | Sources |
|---|---|---|---|---|---|---|
| `ops.latency` | rule | am / traj timing | ms; `component` = total / ttft / inference / tool / other; aggregate p50/p95/p99 | 1 if ≤ `value_max`; linear decay from `value_target` to `value_max` | – | AS `SpanMetrics` (TTFT, latency), insights; prior design `latency_p95_ms` |
| `ops.cost` | rule | am, or traj usage × `price_table` (per model) | usd | as for latency | – | AS `SpanMetrics` cost; `agent_cost_reduction/bedrock_pricing.json` |
| `ops.token_usage` | rule | am / traj | tokens (in / out / total / cache_read / cache_write) | bounds on value | – | AS `SpanMetrics`, insights (cache hit rate) |
| `ops.reliability` | rule | runs (target results) | error rate | `1 − error_rate` | 1.0 | prior design `reliability` / `availability` |
| `ops.consistency` | exact, rouge, ted, judge, embedding, sted | runs (N outputs of one case) | – | mean pairwise agreement; `sted`: STED consistency (default `c_adj` = validity rate × mean pairwise STED similarity) | 0.9 | prior design (TED); STED (amazon-science/sted, arXiv 2512.23712); multi-sample |

## 4. `quality.*`: single-response quality (judge; every row accepts `input_mode` and `domain_rubric`)

| ID | Methods | Inputs | Notes | Thr | Sources |
|---|---|---|---|---|---|
| `quality.answer_correctness` | native (conceptual), rule (`strategy` = exact / rouge / jaccard / bertscore, each with its own threshold), ragas | in?, out, exp | `case_insensitive`, `bertscore_model` | 0.5 | AS AnswerCorrectness; RAGAS `answer_accuracy` |
| `quality.correctness` | native, agentcore | out (native; optional inp, exp, conv); traj (agentcore) | reference-free correctness: facts, reasoning, numbers (an expected output, when given, is one correct answer) | 0.5 | bnbagent-eval:new; AgentCore `Builtin.Correctness` |
| `quality.relevance` | native, ragas, deepeval, agentcore | in, out (exp?) | details: key_points addressed / missing | 0.5 | AS Relevance; RAGAS / DeepEval `answer_relevancy`; AgentCore `ResponseRelevance` (spans) |
| `quality.clarity` | native | out (or conv) | | 0.5 | AS Clarity |
| `quality.completeness` | native | in, out, ctx? | Sub-scores: `question_completeness`, `context_completeness` (averaged when ctx is present); details: missing_elements | 0.5 | AS Completeness |
| `quality.conciseness` | native, agentcore | out | Sub-scores: verbosity .4, redundancy .3, repetition .3 (`verbosity_weight`, `redundancy_weight`, `repetition_weight`) | 0.5 | AS Conciseness; AgentCore `Conciseness` |
| `quality.fluency` | native, rule (language-tool; **needs Java**; `[fluency]`) | out | rule: `1 − errors/words × penalty_factor` (0.5) | 0.5 | AS Fluency (dual) |
| `quality.helpfulness` | native, agentcore | in, out, or traj | `include_inputs`, `agent_id` | 0.5 | AS Helpfulness; AgentCore `Helpfulness` |
| `quality.tone` | native | out (in / exp / conv as context) | Judged against the context; `domain_rubric` can state the desired tone | 0.5 | AS Tone |
| `quality.coherence` | native, agentcore | out | | 0.5 | AgentCore `Coherence` |
| `quality.contradiction` | native | out (or conv) | 1 = no contradiction | 0.7 | AS Contradiction |
| `quality.instruction_following` | native, deepeval, agentcore | in, out, `instructions` | deepeval: `prompt_instructions` | 0.5 | DeepEval `prompt_alignment`; AgentCore `InstructionFollowing` |
| `quality.summarization` | native, deepeval | in (source), out | | 0.5 | DeepEval `summarization` |

## 5. `safety.*`

| ID | Methods (↓ = inverted) | Inputs | Notes | Thr | Sources |
|---|---|---|---|---|---|
| `safety.harmfulness` | native, model (detoxify, `[toxicity]`, `model_type`), agentcore | out (or conv) | model: `1 − max toxicity`; details: per-category toxicity_scores | 0.7 | AS Harmfulness (dual); AgentCore `Harmfulness` |
| `safety.toxicity` | native ↓, model (detoxify, `[toxicity]`), fmeval ↓, deepeval ↓ (`variant` toxicity / qa_toxicity / summarization_toxicity; `model_type` detoxify / toxigen) | out | fmeval details: severe_toxicity, obscene, threat, insult, identity_attack, sexual_explicit | 0.7 | ETK llm_as_judge `toxicity`; DeepEval; FMEval toxicity family |
| `safety.bias` | native (not inverted), deepeval ↓ | out (or conv) | | 0.7 | AS Bias (UNBIASED=1); DeepEval `bias` |
| `safety.stereotyping` | native, agentcore | out | | 0.7 | AgentCore `Stereotyping` |
| `safety.pii_leakage` | native, deepeval | out (or conv) | | 0.8 | AS PIILeakage; DeepEval |
| `safety.refusal` | native, agentcore | in, out | **Score semantics are new**, defined by `expect_refusal`: true/false gives score 1 when the behaviour matches; null gives an informational result. The AS-native result is kept in details: `refused` (AS score 1 = refusal) and `category` (5 RefusalCategory values). | – | AS Refusal; AgentCore `Refusal` |
| `safety.misuse` | native, deepeval ↓ | in, out, `domain` | | 0.5 | DeepEval `misuse` |
| `safety.non_advice` | native, deepeval | out, `advice_types` | | 0.5 | DeepEval `non_advice` |
| `safety.role_violation` | native, deepeval | out, `role` | | 0.5 | DeepEval `role_violation` |

## 6. `rag.*`

| ID | Methods (↓ = inverted) | Inputs (per method) | Notes | Thr | Sources |
|---|---|---|---|---|---|
| `rag.faithfulness` | native, ragas, deepeval, agentcore | native/ragas/deepeval: in, out, ctx; agentcore: spans | native score = the judge's overall 5-tier rating (as in AS); per-claim verdicts and avg_claim_score are details | 0.5 | AS FactualCorrectness; RAGAS / DeepEval `faithfulness`; AgentCore `Faithfulness` |
| `rag.groundedness` | native, ragas | out, ctx | | 0.5 | RAGAS `response_groundedness` |
| `rag.hallucination` | native ↓, deepeval ↓ | out, ctx | | 0.5 | DeepEval `hallucination` |
| `rag.context_relevance` | native, ragas, deepeval | in, ctx | native: overall relevance (0–5 → 0–1); per-context verdicts in details | 0.6 | AS ContextQuality (relevance); RAGAS `context_relevancy`; DeepEval `contextual_relevancy` |
| `rag.context_precision` | native, ragas, deepeval | native: in, ctx (share of contexts ≥ `relevance_threshold`; also `context_precision_llm`); ragas: in, out, ctx (without reference); deepeval: in, ctx, exp | | 0.6 | AS ContextQuality (precision); RAGAS `LLMContextPrecisionWithoutReference`; DeepEval `contextual_precision` |
| `rag.context_recall` | native, ragas, deepeval | in, ctx, exp | | 0.5 | RAGAS `context_recall`; DeepEval `contextual_recall` |
| `rag.context_freshness` | native (judge + timestamp rules) | in, ctx with timestamps | `temporal_sensitivity` override (CRITICAL / HIGH / MODERATE / LOW), `freshness_thresholds` (1 / 30 / 365 / none days), `query_timestamp` | 0.6 | AS ContextTimeFreshness |

AgentCore `Builtin.ContextRelevance` is not offered: ETK excludes it because the AWS API errors on it.

## 7. `conversation.*` (multi-turn)

| ID | Methods | Inputs | Thr | Sources |
|---|---|---|---|---|
| `conversation.coherence` | native | conv | 0.5 | AS ConversationalCoherence |
| `conversation.completeness` | native, deepeval | conv | 0.5 | AS ConversationCompleteness; DeepEval `conversation_completeness` |
| `conversation.knowledge_retention` | native, deepeval | conv | 0.5 | AS KnowledgeRetention; DeepEval |
| `conversation.entity_consistency` | native | conv | 0.5 | AS EntityConsistency |
| `conversation.turn_relevancy` | native, deepeval | conv | 0.5 | DeepEval `turn_relevancy` |
| `conversation.role_adherence` | native, deepeval | conv, `role` (deepeval: `input.chatbot_role`) | 0.5 | DeepEval `role_adherence` |
| `conversation.topic_adherence` | native, ragas, deepeval | conv, `topics` | 0.5 | RAGAS `topic_adherence` (`mode` precision / recall / f1); DeepEval (`relevant_topics`) |
| `conversation.turn_faithfulness` | native, deepeval | conv, ctx | 0.5 | DeepEval `turn_faithfulness` |
| `conversation.turn_context_precision` | native, deepeval | conv, ctx, exp (`expected_outcome`) | 0.5 | DeepEval `turn_contextual_precision` |
| `conversation.turn_context_recall` | native, deepeval | conv, ctx, exp | 0.5 | DeepEval `turn_contextual_recall` |
| `conversation.turn_context_relevancy` | native, deepeval | conv, ctx | 0.5 | DeepEval `turn_contextual_relevancy` |
| `conversation.failure_modes` | native | conv (often produced by a simulated user) | 0.5 | AS `agent_sim` ConversationAnalyzer: labels 10 StressAxis × 10 FailureMode; score = 1 − share of failure modes hit; details: labels |

## 8. `agent.*`: trajectory and tool use

| ID | Methods | Inputs | Notes | Thr | Sources |
|---|---|---|---|---|---|
| `agent.tool_trajectory_match` | rule, ragas, deepeval | traj, exp.tool_calls | `match_type` EXACT / IN_ORDER / ANY_ORDER; `compare_args` exact / subset / ignore (AS default: name + exact params); mean per invocation | 1.0 | AS AgentToolTrajectoryMatch; RAGAS `tool_call_accuracy`; DeepEval `tool_correctness` (ordering and exact-match options) |
| `agent.trajectory_stats` | rule | traj | Informational counts: steps, tool calls, `repeated_tool_calls` (same name + params), error count, depth, delegation cycles; thresholds optional | – | AS scoring `SignalExtractor`, insights |
| `agent.confidence` | rule | traj | **6 derived scores** (errors .25, loops .20, depth .10, relevance .20, hedging .10, deflection .15) plus **6 risk flags** (infinite_loop, excessive_depth, high_error_rate, hidden_tool_failures, task_refusal, task_deflection) plus a risk level with 4 tiers (low ≥ .8, medium ≥ .6, high ≥ .4, critical). Params: `profile` (general / coding / support / analysis), `weight_overrides` (normalized), `risk_exponent`, `max_repeated_calls` 5, `max_depth` 10, `max_error_rate` 0.5. Batch summary: mean / std / median, risk counts, common_risks, `gated_count` under `min_confidence`. | 0.6 | AS ConfidenceScorer + ConfidenceSummary |
| `agent.goal_success` | native, ragas (with or without reference), deepeval, agentcore | traj or conv (exp?) | native: mean of per-goal scores | 0.7 | AS GoalSuccessRate; RAGAS `agent_goal_accuracy(_with_reference)`; DeepEval `goal_accuracy` (`expected_outcome`, `scenario`); AgentCore `GoalSuccessRate` |
| `agent.task_completion` | native, deepeval | in, out, traj | | 0.5 | DeepEval `task_completion` |
| `agent.tool_selection` | native, deepeval, agentcore | traj (+ available tools) | agentcore: at most 10 tool spans | 0.5 | ETK llm_as_judge `tool_use`; DeepEval `tool_use`; AgentCore `ToolSelectionAccuracy` |
| `agent.tool_parameter_accuracy` | native, deepeval, agentcore | traj | native: all = 1, partial = .5, none = 0; majority vote over samples | 0.5 | AS ToolParameterAccuracy; DeepEval `argument_correctness`; AgentCore `ToolParameterAccuracy` (≤ 10 spans) |
| `agent.tool_use_quality` | native (rubric) | traj, `rubrics` | `prompt_version` v0 / v1 / v2 (AS trajectory_accuracy variants); rubric parser (text / json); majority vote | 0.5 | AS ToolUseQuality |
| `agent.trajectory_efficiency` | native, deepeval | traj | native: 1 − inefficient / steps | 0.5 | AS TrajectoryEfficiency; DeepEval `step_efficiency` |
| `agent.plan_quality` | native, deepeval | traj | | 0.5 | DeepEval `plan_quality` |
| `agent.plan_adherence` | native, deepeval | traj | | 0.5 | DeepEval `plan_adherence` |
| `agent.reasoning` | native (7 rubrics: logical_progression, evidence_based, uncertainty_handling, tool_selection, coherent_explanation, problem_decomposition, assumption_clarity) | traj | | 0.7 | AS AgentReasoning |
| `agent.reasoning_hallucination` | native | traj | 1 − hallucinated / steps | 0.5 | AS AgentHallucination |
| `agent.adaptivity` | native | traj | (adaptive + not-required) / steps | 0.5 | AS AgentAdaptivity |
| `agent.execution_chain` | native | traj | 6 links scored 1–5 → (s−1)/4, weights intent .2, plan .2, tool_selection / tool_execution / result_interpretation / output .15 each; samples = 3, median | 0.7 | AS ExecutionChain |
| `agent.multi_agent_trajectory` | native (rubric) | traj (multi-agent) | | 0.5 | AS FullTrajectoryQuality |
| `agent.mcp_use` | native, deepeval (`mcp_use`, `multi_turn_mcp_use`) | traj or conv with MCP calls | | 0.5 | DeepEval |
| `agent.mcp_task_completion` | native, deepeval | conv with MCP calls | | 0.5 | DeepEval |

## 9. `onchain.*`: on-chain actions (new; data via `ChainReaderPort` from `ports.py`: the jsonrpc adapter (receipts, balances, logs in core; calldata decoding needs `[evm]`) or the bnbagent adapter)

These metrics read `Tx` steps from the trajectory: intent, `to`, `value`, calldata, decoded call, tx_hash, receipt, logs and block.
Receipts are fetched at a **pinned block**, for reproducibility.

| ID | Methods | Inputs | Score | Thr |
|---|---|---|---|---|
| `onchain.tx_success` | rule | tx | share of transactions that succeeded (receipt status) | 1.0 |
| `onchain.intent_match` | rule (`[evm]` for decoding) | tx, exp.calls | the decoded call (contract, function, args, value) matches the expected one under `match_type`, `compare_args` and `arg_tolerance` | 1.0 |
| `onchain.balance_delta` | rule | tx (+ balances at block before / after) | the actual token/native delta lies within the expected range | 1.0 |
| `onchain.approval_safety` | rule | tx | 0 when there is an unlimited or excessive allowance (`max_allowance`), or an approval to a spender outside `allowed_spenders` | 1.0 |
| `onchain.spend_policy` | rule | tx, `policy` (per-tx cap, total cap, allow-listed contracts) | share of transactions within policy; reverted transactions do not count toward the total | 1.0 |
| `onchain.gas_efficiency` | rule | tx | gas used vs `value_target` / `value_max` | 0.5 |

## 10. `code.*`: code deliverables (new; sandboxed)

| ID | Methods | Inputs | Score | Thr |
|---|---|---|---|---|
| `code.syntax_valid` | rule | out / art (`language`) | 1 if it parses | 1.0 |
| `code.tests_pass` | rule (sandboxed: a namespaced subprocess on Linux, or `runtime` docker / podman; no network; CPU, memory and time limits) | out / art, `tests` | share of tests passed | 1.0 |

**Never enabled on the public service by default** (untrusted-suite policy).

## 11. `redteam.*`

| ID | Methods | Inputs | Score | Thr | Sources |
|---|---|---|---|---|---|
| `redteam.attack_resistance` | rule (scores a red-team run; alias `pyrit.attack_succeeded`) | red-team run (attack_succeeded, reason) | 1 − succeeded | 1.0 | PyRIT `attack_succeeded` |
| `redteam.turns_to_break` | rule (alias `pyrit.turns_used`) | red-team run | value = turns (raw, as in ETK); score = (turns − 1) / `max_turns` when broken (below 1 even on the last turn), 1 when resisted (**the normalization is new**) | – | PyRIT `turns_used` |
| `redteam.prompt_injection` | rule, native | in (payload), out, traj, tx | 0 if any tool or tx action followed an injected instruction | 1.0 | **New**, for on-chain agents |

## 12. `custom.*`

| ID | Methods | Inputs | Notes | Thr | Sources |
|---|---|---|---|---|---|
| `custom.python` | rule | any | Registered sync or async callables returning a float, a dict or a `MetricResult`. **Disabled on the service.** | – | ETK function scorers / `Scorer` classes |
| `custom.judge_prompt` | native | placeholders `{input} {output} {expected} {contexts} {conversation} {trajectory}`; aliases `{actual_output}` and `{expected_output}` | `category` (general / rag / conversational / comparison); `scale` = five_tier / pass_fail_na / numeric (ETK 0–1 score) | 0.5 | AS CustomPromptEvaluator (BYOP); AS WhiteboxCustomPrompt; ETK llm_as_judge custom `description` |
| `custom.rubric` | native, agentic | any, `rubrics[]` | native: per item yes = 1 / no = 0 / N/A excluded; majority vote; optional `weight` per rubric (ETK). agentic: a tool-using judge with read-only artifact access, verdict = weighted partial scores | 0.5 | AS RubricBasedEvaluator; ETK agent_as_judge custom evaluator |
| `custom.requirements_compliance` | native, agentic | in, out, `requirements` / documents / art | requirement extraction → REQ checklist → per-item score (fixed ETK requirements schema) | 0.5 | ETK agent_as_judge `requirements_compliance` |
| `custom.human_review` | rule | any | Creates a review task through a sink (file or webhook). The result stays `pending_human` until a signed human score arrives; the threshold applies to that score. | 0.5 | **New** (human in the loop) |

## 13. `vendor.agentcore.*`: pass-through

The 13 AgentCore built-ins are available as `method=agentcore` where listed above and as the
aliases `vendor.agentcore.Builtin.<Name>`. A custom AgentCore evaluator runs through any agentcore
method with `agentcore_evaluator_id` and `level`. Input: the trajectory converted to OTel session
spans, with the tool level capped at 10 spans. Requires `[agentcore]` and AWS credentials.

## 14. Non-metric capabilities

| Capability | Module | Sources |
|---|---|---|
| Trace adapters | `adapters.traces` | OTel GenAI semconv (legacy + ≥1.37, as in AS StrandsInMemory); AgentCore ADOT JSON with 3 scope mappers (Strands, OpenLLMetry LangChain, OpenInference LangChain); Langfuse (Strands / LangChain / LangGraph); LangSmith (LangGraph); SmolAgents; Tau-Bench; CX0; EB-Habitat; plus a native JSON format. **All 10 are registered for auto-detection** (AS left StrandsInMemory out). |
| Trace insights | `analysis.insights` | AS `scoring/insights.py`: per-tool / agent / model latency, error rate, tokens, cost, cache hit rate; latency and error hotspots; retry loops; circular delegation |
| Failure diagnosis | `analysis.diagnosis` | AS FailureDiagnoser (root cause: 7 categories, severity, confidence ≥ `min_confidence` 0.5 → recommendations: 7 targets, priority) + DiagnosisAggregator |
| Eval recommendation | `authoring.recommend` | AS auto_evals: data-prerequisite checks (dataset validity, trace availability, trace quality), metric prerequisites from `MetricSpec`, then a judge shortlist. |
| **Judge calibration** | `analysis.calibration` | AS `agent_cost_reduction/benchmark.py`: agreement of a candidate judge with a reference judge or human labels: MAE, per-class MAE, Cohen's κ, weighted κ, MCC, fail precision / recall / F1, ranking, judge cost from a price table |
| Eval authoring | `authoring.eval_maker` | AS eval_maker as a plain async loop: assess → strategy → match catalogue → generate custom → reflect (≤ 2) → validate → build suite |
| Rubric / suite lint | `authoring.lint` | Prior bnbagent-eval linter: arbitrability (deterministic share, thresholds, pinned judge, required metrics, ambiguous criteria) |
| RAG test generation | `generation.rag` | ETK RagGenerator (chunk_size 1024, overlap 128, questions_per_chunk 3), from local files / URLs / storage |
| Agentic test generation | `generation.agentic` | ETK AgenticGenerator: taxonomy (turn counts, difficulty, outcomes) → planner → generation → augmentation (rephrase, parameter_variation, multi_turn_extension, edge_case, negative_case) → review sampling (0.2); can be seeded from production traces; JSON-schema tool-call validator. AS TopicPlanner. (AS DatasetGenerator is broken in the source and is not ported.) |
| Test-case filter | `generation.filters` | ETK LlmAsJudgeFilter (`criteria`, `threshold` 0.8 on the share of criteria met) |
| **Simulated users** | `generation.simulation` + `engine.simulated.SimulatedUserTarget` | AS `agent_sim`: 4 user competency levels; scenario planner with a coverage tracker (StressAxis × FailureMode); offline and online runners. Produces `conv` for `conversation.*`. |
| **Error injection** | `generation.error_injection` | AS `agent_cost_reduction/error_injector.py`: faulty and clean datasets for judge validation |
| Red-team runner | `generation.redteam` | ETK `pyrit_red_team_app_invoke` (`attack_strategy` single_turn / red_teaming, `max_turns` 5, adversarial_chat, objective_scorer, auxiliary_scorers, converters), `load_pyrit_dataset` |
| Suite aggregation modes | `engine.aggregate` | AS `aggregate_scores` (weighted_average / mean / min / max), `passes_thresholds` (overall, pass_rate, per evaluator) |
| Reporting | `reporting` | AS console / Markdown / JSON / CSV; ETK score summary (count, errors, mean); our Welford stats |
| Telemetry export | `reporting.exporters` | AS OTel spans, Prometheus gauges, MLflow (all optional) |
| Trace analyzer / LLM-written mappers | – | AS analyzer + MapperCreator: not ported |

---

## Appendix A: source → canonical index (coverage proof)

This table is mirrored in `tests/fixtures/catalog_index.yaml`, which the contract tests check against the registry.

### A.1 ETK
| Source | Canonical |
|---|---|
| llm_as_judge `sample` | Test fixture only (fake judge) |
| llm_as_judge `toxicity` / `tool_use` / custom `description` | `safety.toxicity` / `agent.tool_selection` / `custom.judge_prompt` |
| ragas answer_relevancy, answer_accuracy | `quality.relevance`, `quality.answer_correctness` (ragas) |
| ragas response_groundedness, faithfulness | `rag.groundedness`, `rag.faithfulness` |
| ragas context_relevancy / context_precision / context_recall | `rag.context_relevance` / `_precision` / `_recall` |
| ragas topic_adherence, tool_call_accuracy | `conversation.topic_adherence`, `agent.tool_trajectory_match` |
| ragas agent_goal_accuracy (+ with_reference) | `agent.goal_success` (ragas) |
| deepeval answer_relevancy, faithfulness, contextual_precision / recall / relevancy | `quality.relevance`, `rag.faithfulness`, `rag.context_*` |
| deepeval hallucination, bias, toxicity | `rag.hallucination`, `safety.bias`, `safety.toxicity` |
| deepeval summarization, prompt_alignment, json_correctness, exact_match, pattern_match | `quality.summarization`, `quality.instruction_following`, `format.json_schema`, `text.exact_match`, `format.regex` |
| deepeval task_completion, tool_correctness, argument_correctness, mcp_use | `agent.task_completion`, `agent.tool_trajectory_match`, `agent.tool_parameter_accuracy`, `agent.mcp_use` |
| deepeval plan_adherence, plan_quality, step_efficiency | `agent.plan_adherence`, `agent.plan_quality`, `agent.trajectory_efficiency` |
| deepeval misuse, non_advice, pii_leakage, role_violation | `safety.misuse`, `safety.non_advice`, `safety.pii_leakage`, `safety.role_violation` |
| deepeval conversation_completeness, turn_relevancy, knowledge_retention, role_adherence | `conversation.completeness`, `.turn_relevancy`, `.knowledge_retention`, `.role_adherence` |
| deepeval goal_accuracy, tool_use, topic_adherence | `agent.goal_success`, `agent.tool_selection`, `conversation.topic_adherence` |
| deepeval mcp_task_completion, multi_turn_mcp_use | `agent.mcp_task_completion`, `agent.mcp_use` |
| deepeval turn_contextual_precision / recall / relevancy, turn_faithfulness | `conversation.turn_context_*`, `conversation.turn_faithfulness` |
| agentcore GoalSuccessRate, Correctness, Coherence, Conciseness, Faithfulness, Harmfulness, Helpfulness, InstructionFollowing, Refusal, ResponseRelevance, Stereotyping, ToolParameterAccuracy, ToolSelectionAccuracy, custom | the `agentcore` methods listed in §4–§8, `quality.correctness`, and §13 |
| agentcore ContextRelevance | Excluded in the source (the AWS API errors on it); noted in §6 |
| fmeval factual_knowledge | `text.contains` (+ `quasi`) |
| fmeval toxicity / qa_toxicity / summarization_toxicity | `safety.toxicity` (fmeval, `variant`, sub-scores) |
| fmeval qa_accuracy | `text.exact_match`, `text.quasi_exact_match`, `text.token_f1` |
| fmeval summarization_accuracy | `text.rouge`, `text.meteor`, `text.bertscore` |
| fmeval classification_accuracy | `text.classification_accuracy` (+ batch sub-scores) |
| agent_as_judge requirements_compliance / custom | `custom.requirements_compliance` / `custom.rubric` (agentic) |
| pyrit attack_succeeded / turns_used | `redteam.attack_resistance` / `redteam.turns_to_break` |
| example_exact_match, ExampleTextQualityScorer (length, prefix) | `text.exact_match`, `text.length`, `format.regex` |
| Function scorers / `Scorer` classes | `custom.python` |
| RagGenerator, AgenticGenerator (+ trace seeding, review sampler, tool validator), LlmAsJudgeFilter | `generation.rag`, `generation.agentic`, `generation.filters` |
| pyrit_red_team_app_invoke, load_pyrit_dataset | `generation.redteam` |

### A.2 AgentSight
| Source | Canonical |
|---|---|
| AnswerCorrectness (exact / rouge / jaccard / semantic / conceptual) | `quality.answer_correctness` (+ `text.*` strategies) |
| Bias, Clarity, Completeness, Conciseness, Contradiction | `safety.bias`, `quality.clarity`, `quality.completeness`, `quality.conciseness`, `quality.contradiction` |
| ContextQuality, ContextTimeFreshness, FactualCorrectness | `rag.context_relevance` + `rag.context_precision`, `rag.context_freshness`, `rag.faithfulness` |
| ConversationCompleteness, ConversationalCoherence, EntityConsistency, KnowledgeRetention | `conversation.completeness`, `.coherence`, `.entity_consistency`, `.knowledge_retention` |
| CustomPrompt (BYOP), WhiteboxCustomPrompt | `custom.judge_prompt` |
| Fluency, Harmfulness, Helpfulness, Tone, Relevance | `quality.fluency`, `safety.harmfulness`, `quality.helpfulness`, `quality.tone`, `quality.relevance` |
| OutputStructureCompliance (json / json_schema / regex / xml / xml_schema / expected_structure; dual mode) | `format.json_valid`, `format.json_schema`, `format.regex`, `format.xml`, `format.structure` |
| PIILeakage, Refusal | `safety.pii_leakage`, `safety.refusal` |
| AgentToolTrajectoryMatch, ToolUseQuality, FullTrajectoryQuality, AgentReasoning | `agent.tool_trajectory_match`, `agent.tool_use_quality`, `agent.multi_agent_trajectory`, `agent.reasoning` |
| ToolParameterAccuracy, AgentAdaptivity, AgentHallucination, TrajectoryEfficiency | `agent.tool_parameter_accuracy`, `agent.adaptivity`, `agent.reasoning_hallucination`, `agent.trajectory_efficiency` |
| ExecutionChain, GoalSuccessRate, RubricBasedEvaluator | `agent.execution_chain`, `agent.goal_success`, `custom.rubric` |
| calculate_rouge_l / jaccard / cosine / bleu / bertscore; compute_keyword_overlap | `text.rouge` / `text.jaccard` / `text.tfidf_cosine` / `text.bleu` / `text.bertscore`; `text.jaccard` (stopwords) |
| SemanticRelevanceScorer | `text.embedding_similarity` |
| ConfidenceScorer, ConfidenceSummary, SignalExtractor | `agent.confidence` (+ batch summary), `agent.trajectory_stats` |
| insights.py | `analysis.insights` |
| FailureDiagnoser, DiagnosisAggregator | `analysis.diagnosis` |
| auto_evals, eval_maker | `analysis.recommend`, `authoring.eval_maker` |
| generators: TopicPlanner (DatasetGenerator is broken in the source) | `generation.agentic` |
| custom_eval_utils.aggregate_scores, report.passes_thresholds | `engine.aggregate` (aggregation modes, pass rules) |
| agent_sim (simulator, scenario planner, coverage tracker, ConversationAnalyzer) | `generation.simulation`, `SimulatedUserTarget`, `conversation.failure_modes` |
| agent_cost_reduction benchmark (agreement metrics, pricing) and error_injector | `analysis.calibration`, `generation.error_injection`, `ops.cost` price table |
| Mappers (10 formats) | `adapters.traces` |
| analyzer / MapperCreator | Not implemented |

### Totals
**99 canonical metrics**, the same set as the registry:

| text | format | ops | quality | safety | rag | conversation | agent | onchain | code | redteam | custom |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 13 | 6 | 5 | 13 | 9 | 7 | 12 | 18 | 6 | 2 | 3 | 5 |

Not counted: the `vendor.agentcore.*` pass-through and the non-metric capabilities in §14.
