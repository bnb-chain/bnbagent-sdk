# Source map: ETK, AgentSight, STED and the earlier prototype → canonical catalogue

This document maps every evaluator, metric, scorer and evaluation capability of the two source
toolkits to the canonical metric ID (and method) or the non-metric capability module that
implements it in `bnbagent-eval`. It is derived from the source code and checked against the
registry. The compact map in the [maintainer guide](../MAINTAINER_GUIDE.md#43-the-map-item-by-item)
is generated from it.

- **ETK** = AWS Gen AI Evaluation Toolkit (`gen-ai-evaluation-toolkit`); paths are relative to
  that repository.
- **AS** = AgentSight (`agent-sight`); paths are relative to that repository. Only
  `agent-lens-core` contains evaluation logic; `agent-lens-api`, `agent-lens-platform`,
  `agent-lens-ui`, `agent-lens-client` and `agent-sight-sdk` are service, UI and client code
  (no evaluators; their custom-evaluator API wraps `eval_maker`).
- **STED** = amazon-science `sted` (`github.com/amazon-science/sted`, commit `627aee0`, Apache-2.0;
  Wang et al., arXiv 2512.23712); paths are relative to that repository.
- **Prototype** = the earlier bnbagent-eval prototype (`BNB_reference/bnbagent-eval`: an ETK plugin
  wrapping AgentSight + STED, plus orchestration); paths are relative to that repository.
- **Our file** paths are relative to `eval/` (the `bnbagent_eval` package root).
- Line numbers point at the item's definition (class, function or registry entry) at the mapped
  source revision.

## 0. How to verify

| Check | Command |
|---|---|
| Registry vs [metric-catalog.md](metric-catalog.md) (IDs, categories, methods, polarity, thresholds, inputs, params, sources) | `.venv/bin/python scripts/audit_catalog.py` (`--dump` prints the registry as JSON) |
| The registry holds exactly the catalogue IDs | `tests/contract/test_metric_contract.py::test_catalogue_coverage` (`tests/fixtures/catalog_index.yaml`) |
| Every vendor method records its vendor in `MetricSpec.sources` | `tests/unit/metrics/test_source_parity.py::test_every_vendor_method_has_a_recorded_source` |
| §6 lists each metric's `MetricSpec.sources` and aliases | `tests/unit/metrics/test_source_parity.py::test_source_map_reverse_index_matches_the_registry` |
| Aliases (`ragas.*`, `deepeval.*`, `fmeval.*`, `pyrit.*`, `vendor.agentcore.*`) resolve uniquely | `tests/contract/test_metric_contract.py::test_aliases_resolve_uniquely` |
| Generated metric reference | `bnbagent-eval metrics --markdown --check docs/METRICS.md` |

## 1. Coverage summary

| Toolkit | Rows (source items) in §2 / §3 | Mapped to a metric (ID + method) | Mapped to a capability module | Test fixture / infrastructure only | Not ported / deferred (by decision) | Missing |
|---|---|---|---|---|---|---|
| ETK | 112 | 86 | 18 | 4 | 4 | 0 |
| AS | 129 | 56 | 63 | 1 | 9 | 0 |
| STED (§3A) | 9 | 4 | 2 | 0 | 3 | 0 |
| Prototype (§3B) | 8 | 5 | 2 | 0 | 1 | 0 |

- One row per scorer / evaluator / score name / function or module (e.g. each of the 37 DeepEval
  names, each AgentCore built-in, each FMEval algorithm, each AS evaluator class and scoring
  function). Counted by classifying the rows of §2 and §3.
- "Not ported" items are listed in §4 with the reason; none of them is a metric (ETK
  `Builtin.ContextRelevance` is excluded by ETK itself).
- No source metric is missing. Intended differences are in §5.1, partial ports (behaviour that
  still differs) in §5.2; §5.3 describes the catalogue-registry check.
- `ops.consistency` has two tree methods: `ted` (plain tree edit distance, exact labels) and
  `sted` (our implementation of the STED paper, §3A). Every metric of the prototype is covered
  (§3B).
- Implementation policy: the metrics are implemented in this package. Libraries that implement
  a metric (rouge-score, NLTK's stemmer / tokenizer, bert-score, sted) are test oracles only
  (`tests/parity`). Third-party evaluators (RAGAS, DeepEval, FMEval, AgentCore) remain optional,
  labelled methods (`bnbagent_eval.metrics.vendors.PROVIDERS`), never the default.

---

## 2. ETK

### 2.1 `packages/evaluator-builtin` — `llm_as_judge` scorer

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/llm_as_judge.py:76` | `sample` | – (test fixture) | – | `bnbagent_eval/judges/providers/fake.py` | Fixed-0.75 test evaluator; replaced by the scripted `FakeJudge` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/llm_as_judge.py:86` | `toxicity` | `safety.toxicity` | `native` (score = 1 − toxicity) | `bnbagent_eval/metrics/safety/toxicity.py`, `judges/prompts/safety/toxicity.py` | ETK system prompt + `<evaluator>` block verbatim; ETK 0–1 score inverted |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/llm_as_judge.py:96` | `tool_use` | `agent.tool_selection` | `native` | `bnbagent_eval/metrics/agent/tool_selection.py` | ETK semantics, 5-tier labels instead of ETK's continuous 0–1 (documented in the module) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/llm_as_judge.py:106` | custom `description` (`_generic_scorer`) | `custom.judge_prompt` | `native` (`scale: numeric`) | `bnbagent_eval/metrics/custom/judge_prompt.py`, `judges/prompts/custom/judge_prompt.py` | ETK `SYSTEM_PROMPT` + `Evaluator definition: <evaluator>` YAML (name `custom`, description = the prompt) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/llm_as_judge.py:295` | `SYSTEM_PROMPT` / `_build_evaluator_prompt` | (prompt text of the three rows above) | – | `bnbagent_eval/judges/prompts/custom/judge_prompt.py` | |

### 2.2 `packages/evaluator-builtin` — RAGAS, DeepEval, AgentCore scorers; `evaluator-fmeval`

Every vendor score name is an alias that selects the vendor method of the canonical metric (the
handler is in `metrics/vendors/<vendor>.py`; the canonical class is in the "Our file" column for
native / rule methods).

**RAGAS** (`RagasScorer`, 11 names)

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:210` | `answer_relevancy` | `quality.relevance` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.answer_relevancy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:211` | `answer_accuracy` | `quality.answer_correctness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.answer_accuracy`; RAGAS `AnswerAccuracy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:212` | `response_groundedness` | `rag.groundedness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.response_groundedness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:213` | `faithfulness` | `rag.faithfulness` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.faithfulness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:214` | `context_relevancy` | `rag.context_relevance` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.context_relevancy`; RAGAS `ContextRelevance` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:215` | `context_precision` | `rag.context_precision` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.context_precision`; RAGAS `LLMContextPrecisionWithoutReference` (in, out, ctx) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:216` | `context_recall` | `rag.context_recall` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.context_recall` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:217` | `topic_adherence` | `conversation.topic_adherence` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.topic_adherence`; `mode` precision / recall / f1 (default f1) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:218` | `tool_call_accuracy` | `agent.tool_trajectory_match` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.tool_call_accuracy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:219` | `agent_goal_accuracy` | `agent.goal_success` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.agent_goal_accuracy`; `AgentGoalAccuracyWithoutReference`; chosen when the case has no `expected` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/ragas.py:220` | `agent_goal_accuracy_with_reference` | `agent.goal_success` | `ragas` | `bnbagent_eval/metrics/vendors/ragas.py` | alias `ragas.agent_goal_accuracy_with_reference`; `AgentGoalAccuracyWithReference`; chosen when the case has `expected` |

**DeepEval** (`DeepEvalScorer`, 24 single-turn + 13 conversational names)

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:73` | `answer_relevancy` | `quality.relevance` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.answer_relevancy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:74` | `faithfulness` | `rag.faithfulness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.faithfulness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:75` | `contextual_precision` | `rag.context_precision` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.contextual_precision` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:76` | `contextual_recall` | `rag.context_recall` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.contextual_recall` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:77` | `contextual_relevancy` | `rag.context_relevance` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.contextual_relevancy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:78` | `hallucination` | `rag.hallucination` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.hallucination`; raw = share of violations (DeepEval < 4.2 reports it; >= 4.2 reports 1 − share, converted back): score = 1 - raw |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:79` | `bias` | `safety.bias` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.bias`; raw = share of violations (DeepEval < 4.2 reports it; >= 4.2 reports 1 − share, converted back): score = 1 - raw |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:80` | `toxicity` | `safety.toxicity` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.toxicity`; raw = share of violations (DeepEval < 4.2 reports it; >= 4.2 reports 1 − share, converted back): score = 1 - raw |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:81` | `summarization` | `quality.summarization` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.summarization` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:82` | `json_correctness` | `format.json_schema` | `rule` | `bnbagent_eval/metrics/format/structured.py` | alias `deepeval.json_correctness`; the alias resolves to our deterministic `rule` method (no extra); select `method: deepeval` for the DeepEval metric |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:83` | `prompt_alignment` | `quality.instruction_following` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.prompt_alignment` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:84` | `exact_match` | `text.exact_match` | `rule` | `bnbagent_eval/metrics/text/exact.py` | alias `deepeval.exact_match`; the alias resolves to our deterministic `rule` method (no extra); select `method: deepeval` for the DeepEval metric |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:85` | `pattern_match` | `format.regex` | `rule` | `bnbagent_eval/metrics/format/structured.py` | alias `deepeval.pattern_match`; the alias resolves to our deterministic `rule` method (no extra); select `method: deepeval` for the DeepEval metric |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:86` | `task_completion` | `agent.task_completion` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.task_completion` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:87` | `tool_correctness` | `agent.tool_trajectory_match` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.tool_correctness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:88` | `mcp_use` | `agent.mcp_use` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.mcp_use` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:89` | `argument_correctness` | `agent.tool_parameter_accuracy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.argument_correctness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:90` | `misuse` | `safety.misuse` | `deepeval` (inverted) | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.misuse`; raw = share of violations (DeepEval < 4.2 reports it; >= 4.2 reports 1 − share, converted back): score = 1 - raw |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:91` | `non_advice` | `safety.non_advice` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.non_advice` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:92` | `pii_leakage` | `safety.pii_leakage` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.pii_leakage` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:93` | `plan_adherence` | `agent.plan_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.plan_adherence` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:94` | `plan_quality` | `agent.plan_quality` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.plan_quality` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:95` | `role_violation` | `safety.role_violation` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.role_violation` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:96` | `step_efficiency` | `agent.trajectory_efficiency` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.step_efficiency` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:100` | `conversation_completeness` | `conversation.completeness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.conversation_completeness` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:101` | `turn_relevancy` | `conversation.turn_relevancy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.turn_relevancy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:102` | `goal_accuracy` | `agent.goal_success` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.goal_accuracy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:103` | `knowledge_retention` | `conversation.knowledge_retention` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.knowledge_retention` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:104` | `role_adherence` | `conversation.role_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.role_adherence` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:105` | `tool_use` | `agent.tool_selection` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.tool_use` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:106` | `topic_adherence` | `conversation.topic_adherence` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.topic_adherence` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:107` | `mcp_task_completion` | `agent.mcp_task_completion` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.mcp_task_completion` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:108` | `multi_turn_mcp_use` | `agent.mcp_use` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.multi_turn_mcp_use`; conversational variant of `mcp_use` (chosen for conversation cases) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:109` | `turn_contextual_precision` | `conversation.turn_context_precision` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.turn_contextual_precision` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:110` | `turn_contextual_recall` | `conversation.turn_context_recall` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.turn_contextual_recall` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:111` | `turn_contextual_relevancy` | `conversation.turn_context_relevancy` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.turn_contextual_relevancy` |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/deepeval.py:112` | `turn_faithfulness` | `conversation.turn_faithfulness` | `deepeval` | `bnbagent_eval/metrics/vendors/deepeval.py` | alias `deepeval.turn_faithfulness` |

**AgentCore** (`AgentCoreScorer`, 13 built-ins)

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:176` | `Builtin.GoalSuccessRate` | `agent.goal_success` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.GoalSuccessRate`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:177` | `Builtin.Coherence` | `quality.coherence` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Coherence`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:178` | `Builtin.Conciseness` | `quality.conciseness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Conciseness`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:180` | `Builtin.Correctness` | `quality.correctness` | `agentcore` | `bnbagent_eval/metrics/quality/correctness.py` | alias `vendor.agentcore.Builtin.Correctness`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:181` | `Builtin.Faithfulness` | `rag.faithfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Faithfulness`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:182` | `Builtin.Harmfulness` | `safety.harmfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Harmfulness`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:183` | `Builtin.Helpfulness` | `quality.helpfulness` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Helpfulness`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:184` | `Builtin.InstructionFollowing` | `quality.instruction_following` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.InstructionFollowing`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:185` | `Builtin.Refusal` | `safety.refusal` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Refusal`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:186` | `Builtin.ResponseRelevance` | `quality.relevance` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.ResponseRelevance`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:187` | `Builtin.Stereotyping` | `safety.stereotyping` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.Stereotyping`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:188` | `Builtin.ToolParameterAccuracy` | `agent.tool_parameter_accuracy` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.ToolParameterAccuracy`; spans from the trajectory |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:189` | `Builtin.ToolSelectionAccuracy` | `agent.tool_selection` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | alias `vendor.agentcore.Builtin.ToolSelectionAccuracy`; spans from the trajectory |

**FMEval** (`FMEvalScorer`, 7 algorithms; `packages/evaluator-fmeval`)

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:17` | `factual_knowledge` | `text.contains` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.factual_knowledge` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:18` | `toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.toxicity` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:19` | `qa_toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.qa_toxicity` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:20` | `summarization_toxicity` | `safety.toxicity` | `fmeval` (inverted) | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.summarization_toxicity` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:21` | `qa_accuracy` | `text.token_f1` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.qa_accuracy` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:22` | `summarization_accuracy` | `text.rouge` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.summarization_accuracy` |
| `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:23` | `classification_accuracy` | `text.classification_accuracy` | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` | alias `fmeval.classification_accuracy` |

**AgentCore extras**

| Source (repo-relative:line) | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:179` | `Builtin.ContextRelevance` | – (excluded) | – | – | Excluded in ETK itself (the AWS API errors on it) |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:198` | custom evaluator (`CustomEvaluatorConfig`, `level`) | any metric's `agentcore` method | `agentcore` + param `agentcore_evaluator_id` (+ `level`) | `bnbagent_eval/metrics/vendors/agentcore.py` | Catalogue §13 says `vendor.agentcore.<Name>`; custom IDs are run through the param, not an alias |
| `packages/evaluator-builtin/src/evaluator_builtin/scorers/agentcore.py:166` | `MAX_TOOL_SPANS = 10`, `SpanFilter` | (span conversion) | – | `bnbagent_eval/metrics/vendors/_agentcore_spans.py` | trajectory → OTel session spans, tool level capped at 10 |

**FMEval sub-scores** (`fmeval.<algorithm>.<score>` aliases select the primary score)

| Source | Source name | Canonical ID | Method | Our file | Notes |
|---|---|---|---|---|---|
| fmeval `qa_accuracy` (via `packages/evaluator-fmeval/src/evaluator_fmeval/scorer.py:135` `_extract_scores`) | `exact_match_score`, `quasi_exact_match_score`, `f1_score`, `precision_over_words`, `recall_over_words` | `text.exact_match`, `text.quasi_exact_match`, `text.token_f1` (precision / recall in details) | `fmeval` | `bnbagent_eval/metrics/vendors/fmeval.py` | rule methods re-implement the same formulas (`text/exact.py`, `text/overlap.py`) |
| fmeval `summarization_accuracy` | `rouge`, `meteor`, `bertscore` | `text.rouge`, `text.meteor`, `text.bertscore` | `fmeval` | same | params `variant` → `rouge_type`, `use_stemmer`, `model` → `model_type_for_bertscore` |
| fmeval `factual_knowledge` | `factual_knowledge`, `factual_knowledge_quasi_exact` | `text.contains` (`quasi`) | `fmeval` | same | `<OR>` delimiter, `mode` all → `AND` |
| fmeval `toxicity` family | `toxicity`, `severe_toxicity`, `obscene`, `threat`, `insult`, `identity_attack`, `sexual_explicit` | `safety.toxicity` (`variant`, `model_type` detoxify / toxigen) | `fmeval` (inverted) | same | sub-scores in details |
| fmeval `classification_accuracy` | `classification_accuracy_score`, `balanced_accuracy_score`, `precision_score`, `recall_score` | `text.classification_accuracy` | `fmeval` | same | the rule method takes FMEval's `valid_labels` label extraction |

### 2.3 `packages/evaluator-builtin` — example scorers and app invokers

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-builtin/src/evaluator_builtin/example.py:66` | `example_exact_match` | `text.exact_match` | `rule` | `bnbagent_eval/metrics/text/exact.py` | |
| `packages/evaluator-builtin/src/evaluator_builtin/example.py:104` | `ExampleTextQualityScorer.length` | `text.length` | `rule` | `bnbagent_eval/metrics/text/classify.py` | ETK non-strict = non-empty (our default with no bounds); `strict` (> 5 chars) = `min_chars: 6` |
| `packages/evaluator-builtin/src/evaluator_builtin/example.py:113` | `ExampleTextQualityScorer.prefix` | `format.regex` | `rule` | `bnbagent_eval/metrics/format/structured.py` | `pattern: "^Response:"`, `full_match: false` |
| `packages/evaluator-builtin/src/evaluator_builtin/example.py:52` | `example_app` | – (example) | – | – | demo target |
| `packages/evaluator-builtin/src/evaluator_builtin/app_invoke/agentcore.py` | AgentCore runtime app invoke | – (not ported) | – | – | AWS-runtime invocation; use `CallableTarget` / `HttpTarget` (`engine/targets.py`) |
| `packages/evaluator-builtin/src/evaluator_builtin/app_invoke/bedrock_converse.py` | Bedrock Converse app invoke | – (not ported) | – | – | as above; judge-side Bedrock support is `judges/providers/bedrock.py` |

### 2.4 `packages/evaluator-pyrit`

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-pyrit/src/evaluator_pyrit/scorers.py:11` | `attack_succeeded` | `redteam.attack_resistance` | `rule` (alias `pyrit.attack_succeeded`) | `bnbagent_eval/metrics/redteam/attack.py` | inverted: score = 1 − succeeded (catalogue §11) |
| `packages/evaluator-pyrit/src/evaluator_pyrit/scorers.py:20` | `turns_used` | `redteam.turns_to_break` | `rule` (alias `pyrit.turns_used`) | `bnbagent_eval/metrics/redteam/attack.py` | value = turns (raw, as ETK); score = (turns − 1) / max_turns when broken (max_turns ≥ turns), 1 when resisted (**new normalization**) |
| `packages/evaluator-pyrit/src/evaluator_pyrit/app_invoke.py:87` | `pyrit_red_team_app_invoke` | `generation.redteam` (`run_red_team`) | – | `bnbagent_eval/generation/redteam.py` | `attack_strategy` single_turn / red_teaming, `max_turns` 5, converters, scorers |
| `packages/evaluator-pyrit/src/evaluator_pyrit/datasets.py:44` | `load_pyrit_dataset` | `generation.redteam.load_pyrit_dataset` | – | `bnbagent_eval/generation/redteam.py` | plus `onchain_objectives` (new) |

### 2.5 `packages/evaluator-agent-as-judge`

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `packages/evaluator-agent-as-judge/src/evaluator_agent_as_judge/builtins.py:151` | `requirements_compliance` (+ `REQUIREMENTS_COMPLIANCE_DESCRIPTION`, output schema) | `custom.requirements_compliance` | `native` (one judge call), `agentic` | `bnbagent_eval/metrics/custom/requirements.py`, `metrics/vendors/agentic.py` | REQ checklist, per-item 0–1 score, weighted mean rounded to 3 decimals; output schema fixed (no `output_schema` param) |
| `packages/evaluator-agent-as-judge/src/evaluator_agent_as_judge/scorers/agent_as_judge.py:32` | `AgentAsJudgeScorer` custom evaluators | `custom.rubric` | `agentic` | `bnbagent_eval/metrics/vendors/agentic.py` | read-only tools on our `JudgeModel` instead of the OpenCode subprocess |
| `packages/evaluator-agent-as-judge/src/evaluator_agent_as_judge/tools/verdict.py` | `write_verdict` (weighted partial scores) | `custom.rubric` / `custom.requirements_compliance` | `agentic` | `bnbagent_eval/metrics/vendors/agentic.py`, `judges/prompts/custom/agentic_verdict.py` | `{scores: [{name, score, weight}], summary, issues}` |
| `packages/evaluator-agent-as-judge/src/evaluator_agent_as_judge/prompt.py`, `runner.py`, `network_guard.py` | agent prompt, runner, network guard | (agentic loop) | – | `bnbagent_eval/metrics/vendors/agentic.py`, `judges/prompts/custom/agentic_plan.py`, `agentic_inspect.py` | no network: artifacts come from the case or `StoragePort` |

### 2.6 `packages/generator-builtin` (test generation and filtering)

| Source (repo-relative:line) | Source name | Capability module | Our file | Notes |
|---|---|---|---|---|
| `packages/generator-builtin/src/generator_builtin/rag.py:156` | `RagGenerator` | `generation.rag` | `bnbagent_eval/generation/rag.py` | chunk 1024 / overlap 128 / 3 questions per chunk; local files, URLs, `StoragePort` instead of S3 |
| `packages/generator-builtin/src/generator_builtin/llm_as_judge.py:148` | `LlmAsJudgeFilter` | `generation.filters` | `bnbagent_eval/generation/filters.py` | `criteria`, threshold 0.8; plus new `DedupeFilter`, `LengthFilter`, `LanguageFilter` |
| `packages/generator-builtin/src/generator_builtin/agentic/generator.py:78` | `AgenticGenerator` | `generation.agentic` | `bnbagent_eval/generation/agentic/__init__.py` | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/taxonomy.py` | taxonomy stage | `generation.agentic.stages.build_taxonomy` | `bnbagent_eval/generation/agentic/stages.py` | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/planner.py` | planner stage | `stages.sample_scenarios` | same | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/generation.py` | generation stage | `stages.generate_cases` | same | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/augmentor.py` | augmentation (rephrase, parameter_variation, multi_turn_extension, edge_case, negative_case) | `stages.augment_cases` | same | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/review_sampler.py` | review sampler (0.2) | `stages.apply_review_sampling` | same | |
| `packages/generator-builtin/src/generator_builtin/agentic/stages/trace_extractor.py` | seeding from production traces | `generation.agentic.seeding` | `bnbagent_eval/generation/agentic/seeding.py` | traces read through `adapters.traces` |
| `packages/generator-builtin/src/generator_builtin/agentic/tool_validator.py` | JSON-schema tool-call validator | `generation.agentic.validator` | `bnbagent_eval/generation/agentic/validator.py` | |
| `packages/generator-builtin/src/generator_builtin/agentic/prompts.py` | taxonomy / sampling / generation / augmentation prompts | (prompt text) | `bnbagent_eval/judges/prompts/generation/agentic.py` | |
| `packages/generator-builtin/src/generator_builtin/agentic/{bedrock,concurrency,errors}.py`, `common.py` | Bedrock batch clients, concurrency, errors | – (infrastructure) | `bnbagent_eval/generation/_llm.py` (JSON extraction, bounded gather) | AWS batch jobs not ported |

### 2.7 `packages/etk-sdk` (framework)

| Source (repo-relative) | Source name | Canonical ID / module | Our file | Notes |
|---|---|---|---|---|
| `packages/etk-sdk/src/genai_etk_sdk/scorer.py` | `Scorer` classes, `@scorer`, function scorers | `custom.python` | `bnbagent_eval/metrics/custom/python.py` | registered callables; disabled on the service |
| `packages/etk-sdk/src/genai_etk_sdk/evaluator.py` | `Evaluator` (invoke → score → results) | `engine.runner` | `bnbagent_eval/engine/runner.py` | |
| `packages/etk-sdk/src/genai_etk_sdk/exporter.py` | score summary (count, errors, mean) | `reporting` | `bnbagent_eval/reporting/*`, `engine/aggregate.py` | our Welford stats are new |
| `packages/etk-sdk/src/genai_etk_sdk/{remote,gateway_client,generation_jobs,batching,bedrock,log_context}.py` | ETK service / AWS plumbing | – (not ported) | – | replaced by our server / client |
| `packages/experiment-lambdas/**`, `packages/python-api/**`, `cdk`, `terraform*`, `cli`, `models`, `generated` | service, infrastructure, API | – | – | no metrics (`report_metrics_lambda` publishes CloudWatch metrics of results) |

---

## 3. AgentSight (`agent-lens-core`)

### 3.1 Blackbox and whitebox evaluators (`agent_lens/evaluators`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/evaluators/blackbox/answer_correctness.py:68` | `AnswerCorrectnessEvaluator` | `quality.answer_correctness` | native (conceptual_match); rule (`strategy` exact / rouge / jaccard / bertscore = semantic_match) | `bnbagent_eval/metrics/quality/answer_correctness.py` | strategy thresholds 1.0 / 0.7 / 0.5 / 0.8 kept; `case_insensitive` default true |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/bias_evaluator.py:55` | `BiasEvaluator` | `safety.bias` | native | `bnbagent_eval/metrics/safety/bias.py` | 5 severity labels, UNBIASED = 1 (not inverted); output or conversation |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/clarity_evaluator.py:56` | `ClarityEvaluator` | `quality.clarity` | native | `bnbagent_eval/metrics/quality/clarity.py` | 5-tier |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/completeness_evaluator.py:58` | `CompletenessEvaluator` | `quality.completeness` | native | `bnbagent_eval/metrics/quality/completeness.py` | question + context sub-scores averaged when contexts exist |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/conciseness_evaluator.py:103` | `ConcisenessEvaluator` | `quality.conciseness` | native | `bnbagent_eval/metrics/quality/conciseness.py` | verbosity .4 / redundancy .3 / repetition .3 |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/context_quality.py:74` | `ContextQualityEvaluator` | `rag.context_relevance`, `rag.context_precision` | native | `bnbagent_eval/metrics/rag/context_relevance.py`, `context_precision.py` | one source evaluator split in two: overall relevance (0-5 -> 0-1) and share of contexts >= `relevance_threshold` (0.5) |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/context_time_freshness.py:127` | `ContextTimeFreshnessEvaluator` | `rag.context_freshness` | native | `bnbagent_eval/metrics/rag/context_freshness.py` | `temporal_sensitivity`, `freshness_thresholds`, `query_timestamp`; overall 0-5 -> 0-1 |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/context_time_freshness.py:32` | `TemporalSensitivity` | `rag.context_freshness` | param | `bnbagent_eval/metrics/rag/context_freshness.py` | enum values CRITICAL / HIGH / MODERATE / LOW |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/contradiction_evaluator.py:54` | `ContradictionEvaluator` | `quality.contradiction` | native | `bnbagent_eval/metrics/quality/contradiction.py` | 1 = no contradiction; threshold 0.7 |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/conversation_completeness_evaluator.py:56` | `ConversationCompletenessEvaluator` | `conversation.completeness` | native | `bnbagent_eval/metrics/conversation/completeness.py` | >= 2 turns |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/conversational_coherence_evaluator.py:53` | `ConversationalCoherenceEvaluator` | `conversation.coherence` | native | `bnbagent_eval/metrics/conversation/coherence.py` | >= 2 turns |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/custom_prompt_evaluator.py:71` | `CustomPromptEvaluator` | `custom.judge_prompt` | native (`scale: five_tier`) | `bnbagent_eval/metrics/custom/judge_prompt.py` | placeholders, 4 categories, auto-appended content |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/entity_consistency_evaluator.py:57` | `EntityConsistencyEvaluator` | `conversation.entity_consistency` | native | `bnbagent_eval/metrics/conversation/entity_consistency.py` | >= 2 turns |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/factual_correctness.py:66` | `FactualCorrectnessEvaluator` | `rag.faithfulness` | native | `bnbagent_eval/metrics/rag/faithfulness.py` | new name; score = overall 5-tier rating, per-claim verdicts + avg_claim_score in details |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/fluency_evaluator.py:52` | `FluencyEvaluator` | `quality.fluency` | native; rule (`[fluency]`, LanguageTool) | `bnbagent_eval/metrics/quality/fluency.py` | rule: 1 - errors/words x penalty_factor (0.5) |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/harmfulness_evaluator.py:60` | `HarmfulnessEvaluator` | `safety.harmfulness` | native; model (`[toxicity]`, detoxify) | `bnbagent_eval/metrics/safety/harmfulness.py` | model: 1 - max category score |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/helpfulness_evaluator.py:44` | `HelpfulnessEvaluator` | `quality.helpfulness` | native | `bnbagent_eval/metrics/quality/helpfulness.py` | `include_inputs`, `agent_id` |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/knowledge_retention_evaluator.py:56` | `KnowledgeRetentionEvaluator` | `conversation.knowledge_retention` | native | `bnbagent_eval/metrics/conversation/knowledge_retention.py` | >= 2 turns |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/output_structure_compliance_evaluator.py:72` | `OutputStructureComplianceEvaluator` | `format.json_schema` / `format.regex` / `format.xml` / `format.structure` | rule (efficient mode); native (LLM mode, `prioritize_efficiency=False`) | `bnbagent_eval/metrics/format/structured.py`, `structure.py` | json_schema / regex_pattern / xml_schema / expected_structure split into 4 metrics; plain JSON validity is `format.json_valid` (rule only: AS has no schema-less JSON mode) |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/pii_leakage_evaluator.py:55` | `PIILeakageEvaluator` | `safety.pii_leakage` | native | `bnbagent_eval/metrics/safety/pii_leakage.py` | threshold 0.8 |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/refusal_evaluator.py:43` | `RefusalEvaluator` | `safety.refusal` | native | `bnbagent_eval/metrics/safety/refusal.py` | **new score semantics** (`expect_refusal`); AS result kept as details `refused` + `category` |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/relevance_evaluator.py:56` | `RelevanceEvaluator` | `quality.relevance` | native | `bnbagent_eval/metrics/quality/relevance.py` | key points in details |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/tone_evaluator.py:56` | `ToneEvaluator` | `quality.tone` | native | `bnbagent_eval/metrics/quality/tone.py` | no `expected_tone` (it does not exist in the source) |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/answer_correctness.py:41` | `MatchStrategy` | `quality.answer_correctness` | param `strategy` | `bnbagent_eval/metrics/quality/answer_correctness.py` | exact_match → exact, fuzzy_match_rouge → rouge, fuzzy_match_jaccard → jaccard, semantic_match → bertscore, conceptual_match → method `native` |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/categories.py:16` | `EvalCategory` | `custom.judge_prompt` `category`; `JudgeMetric.output_or_conversation`; `ConversationJudge.min_turns` | - | `bnbagent_eval/metrics/custom/judge_prompt.py`, `metrics/judge_base.py`, `metrics/conversation/_common.py` | input validation per category |
| `agent-lens-core/src/agent_lens/evaluators/blackbox/utils.py:148` | `detect_input_format` | `JudgeMetric.select_mode` (`input_mode` param) | - | `bnbagent_eval/metrics/judge_base.py` | trajectory-derived turns not used for mode detection (see divergences) |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/agent_adaptivity_evaluator.py:37` | `AgentAdaptivityEvaluator` | `agent.adaptivity` | native | `bnbagent_eval/metrics/agent/adaptivity.py` | (adaptive + not-required) / steps |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/agent_hallucination_evaluator.py:40` | `AgentHallucinationEvaluator` | `agent.reasoning_hallucination` | native | `bnbagent_eval/metrics/agent/reasoning_hallucination.py` | new name; 1 - hallucinated / steps |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/agent_reasoning_evaluator.py:25` | `AgentReasoningEvaluator` | `agent.reasoning` | native | `bnbagent_eval/metrics/agent/reasoning.py` | 7 rubrics; threshold 0.7 |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/agent_tool_trajectory_match_evaluator.py:31` | `AgentToolTrajectoryMatchEvaluator` | `agent.tool_trajectory_match` | rule | `bnbagent_eval/metrics/agent/trajectory.py` | EXACT / IN_ORDER / ANY_ORDER; per-invocation mean when `expected` lists one call list per invocation; `compare_args` extension |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/execution_chain_evaluator.py:77` | `ExecutionChainEvaluator` | `agent.execution_chain` | native | `bnbagent_eval/metrics/agent/execution_chain.py` | 6 links, weights .2/.2/.15x4, (s-1)/4, 3 samples |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/e2e_trajectory_evaluator.py:29` | `FullTrajectoryQualityEvaluator` | `agent.multi_agent_trajectory` | native (rubric) | `bnbagent_eval/metrics/agent/multi_agent_trajectory.py` | new name |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/goal_success_rate_evaluator.py:35` | `GoalSuccessRateEvaluator` | `agent.goal_success` | native | `bnbagent_eval/metrics/agent/goal_success.py` | mean of per-goal scores; threshold 0.7 |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/tool_parameter_accuracy_evaluator.py:34` | `ToolParameterAccuracyEvaluator` | `agent.tool_parameter_accuracy` | native | `bnbagent_eval/metrics/agent/tool_parameter_accuracy.py` | All/Partial/None = 1/.5/0, majority vote |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/tool_use_quality_evaluator.py:35` | `ToolUseQualityEvaluator` | `agent.tool_use_quality` | native (rubric) | `bnbagent_eval/metrics/agent/tool_use_quality.py` | `prompt_version` v0 / v1 / v2 = trajectory_accuracy variants |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/trajectory_efficiency_evaluator.py:40` | `TrajectoryEfficiencyEvaluator` | `agent.trajectory_efficiency` | native | `bnbagent_eval/metrics/agent/trajectory_efficiency.py` | 1 - inefficient / steps |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/custom_prompt_evaluator.py:39` | `WhiteboxCustomPromptEvaluator` | `custom.judge_prompt` | native (`scale: pass_fail_na`) | `bnbagent_eval/metrics/custom/judge_prompt.py` | `{conversation}` filled from the trajectory |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/rubric_based_evaluator.py:330` | `RubricBasedEvaluator` | `custom.rubric` | native | `bnbagent_eval/metrics/custom/rubric.py` | yes/no/N/A, majority vote; also the engine of the rubric whitebox metrics |
| `agent-lens-core/src/agent_lens/evaluators/whitebox/eval_metrics.py:24` | `PrebuiltMetrics` | `agent.*` (rows above) | - | - | enum of prebuilt whitebox metric names |

### 3.2 Text-similarity functions (`agent_lens/metrics`, `agent_lens/utils/similarity_utils.py`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/metrics/fuzzy_similarity.py:12` | `calculate_rouge_l` | `text.rouge` | rule | `metrics/text/overlap.py` | rougeL F, stemmer on (rouge-score) |
| `agent-lens-core/src/agent_lens/metrics/fuzzy_similarity.py:27` | `calculate_jaccard` | `text.jaccard` | rule | `metrics/text/overlap.py` | lowercase whitespace token sets |
| `agent-lens-core/src/agent_lens/metrics/fuzzy_similarity.py:52` | `calculate_cosine_similarity` | `text.tfidf_cosine` | rule | `metrics/text/overlap.py` | TF-IDF cosine; 0 where sklearn raises on an empty vocabulary |
| `agent-lens-core/src/agent_lens/metrics/fuzzy_similarity.py:71` | `calculate_bleu` | `text.bleu` | rule | `metrics/text/overlap.py` | NLTK method1 smoothing (fixed) |
| `agent-lens-core/src/agent_lens/metrics/semantic_similarity.py:7` | `calculate_bertscore` | `text.bertscore` | model | `metrics/text/bertscore.py` | deberta-xlarge-mnli default; empty text 0 |
| `agent-lens-core/src/agent_lens/utils/similarity_utils.py:189` | `SemanticRelevanceScorer` | `text.embedding_similarity` | keyword (default), embeddings, glove, fasttext | `metrics/text/similarity.py`, `metrics/vendors/local_models.py` | AS stop words verbatim (`tokens.SEMANTIC_STOPWORDS`), min word length 2, `(s+1)/2`; Bedrock embeddings = judge `embed` |
| `agent-lens-core/src/agent_lens/utils/similarity_utils.py:735` | `compute_similarity` | `text.embedding_similarity` | keyword | `metrics/text/similarity.py` | module-level helper of the default scorer |
| `agent-lens-core/src/agent_lens/utils/similarity_utils.py:752` | `compute_keyword_overlap` | `text.embedding_similarity` | keyword | `metrics/tokens.py (`keyword_jaccard`)` | same Jaccard as `_keyword_score` |
| `agent-lens-core/src/agent_lens/metrics/llm_judge.py:248` | `evaluate_with_llm` | judge infrastructure | - | `judges/base.py`, `judges/structured.py`, `metrics/judge_base.py` | retries, throttling, parse errors, usage |

### 3.3 Confidence scoring and insights (`agent_lens/scoring`, `agent_lens/types`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/scoring/scorer.py:32` | `ConfidenceScorer` | `agent.confidence` | rule | `metrics/agent/confidence.py`, `analysis/confidence.py` | 6 derived scores, 6 risk flags, 4 risk tiers, 4 profiles |
| `agent-lens-core/src/agent_lens/scoring/extractor.py:46` | `SignalExtractor` | `agent.confidence`, `agent.trajectory_stats` | rule | `analysis/confidence.py`, `metrics/agent/trajectory.py` | structural / temporal / token / semantic / tool / relevance signals; trajectory_stats depth and error count follow it |
| `agent-lens-core/src/agent_lens/scoring/deriver.py:21` | `ScoreDeriver` | `agent.confidence` | rule | `analysis/confidence.py` |  |
| `agent-lens-core/src/agent_lens/scoring/aggregator.py:28` | `ScoreAggregator` | `agent.confidence` | rule | `analysis/confidence.py` | weights, `weight_overrides` renormalised, `risk_exponent` |
| `agent-lens-core/src/agent_lens/scoring/scoring_config.py:194` | `ScoringConfig` | `agent.confidence` params | - | `analysis/confidence.py` | plus `configs/scoring/*.yaml` (profiles) |
| `agent-lens-core/src/agent_lens/scoring/scoring_utils.py:174` | `extract_keywords` | `text.jaccard` (`stopwords`) | rule | `analysis/confidence.py`, `metrics/text/overlap.py` | `[a-z]+`, >= 3 letters, scoring stop words |
| `agent-lens-core/src/agent_lens/scoring/scoring_utils.py:189` | `compute_keyword_overlap` | `text.jaccard` (`stopwords`) | rule | `metrics/text/overlap.py` | 0 when either side has no keyword |
| `agent-lens-core/src/agent_lens/types/confidence.py:280` | `ConfidenceSummary` | `agent.confidence` batch summary | - | `analysis/confidence.py` | mean / std / median, risk counts, common risks, `gated_count` under `min_confidence` |
| `agent-lens-core/src/agent_lens/scoring/insights.py` | `insights` | `analysis.insights` | - | `analysis/insights.py` | per tool / agent / model latency, errors, tokens, cost, cache hit rate; hotspots; retry loops; circular delegation |
| `agent-lens-core/src/agent_lens/types/insights.py` | `insights` | `analysis.insights` types | - | `analysis/insights.py` |  |

### 3.4 Failure diagnosis (`agent_lens/evaluators/diagnoser`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/evaluators/diagnoser/failure_diagnoser.py:30` | `FailureDiagnoser` | `analysis.diagnosis` | - | `analysis/diagnosis.py` | 7 root-cause categories, severity, `min_confidence` 0.5, 7 remediation targets |
| `agent-lens-core/src/agent_lens/evaluators/diagnoser/diagnosis_aggregator.py:30` | `DiagnosisAggregator` | `analysis.diagnosis` | - | `analysis/diagnosis.py` |  |
| `agent-lens-core/src/agent_lens/evaluators/diagnoser/root_cause_analysis.py` | `root_cause_analysis` | `analysis.diagnosis` | - | `analysis/diagnosis.py`, `judges/prompts/analysis/diagnosis.py` | root-cause prompt (`prompt_templates/root_cause`) |
| `agent-lens-core/src/agent_lens/evaluators/diagnoser/recommendations.py` | `recommendations` | `analysis.diagnosis` | - | `same` | recommendation prompt (`prompt_templates/recommendation`) |
| `agent-lens-core/src/agent_lens/evaluators/diagnoser/types.py` | `types` | `analysis.diagnosis` types | - | `analysis/diagnosis.py` |  |

### 3.5 Eval recommendation and authoring (`agent_lens/auto_evals`, `agent_lens/eval_maker`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/auto_evals/auto_eval.py:41` | `AutoEvaluator` | `authoring.recommend` | - | `authoring/recommend.py` | the module lives in `authoring` |
| `agent-lens-core/src/agent_lens/auto_evals/eval_discovery.py:22` | `EvalDiscovery` | `authoring.recommend` | - | `authoring/recommend.py` | metric prerequisites from `MetricSpec` inputs |
| `agent-lens-core/src/agent_lens/auto_evals/eval_recommendation.py:41` | `EvalRecommender` | `authoring.recommend` | - | `authoring/recommend.py` | judge shortlist |
| `agent-lens-core/src/agent_lens/eval_maker/agent.py` | `agent` | `authoring.eval_maker` | - | `authoring/eval_maker.py` | LangGraph state machine → plain async loop |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/assess_quality.py` | `assess_quality` | `eval_maker` step assess | - | `authoring/eval_maker.py`, `judges/prompts/authoring/eval_maker_assess.py` | AS QualityThresholds and weights kept |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/analyze_strategy.py` | `analyze_strategy` | step strategy | - | `judges/prompts/authoring/eval_maker_strategy.py` |  |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/match_blackbox.py` | `match_blackbox` | step match | - | `judges/prompts/authoring/eval_maker_match.py` | matches the whole catalogue (not only blackbox); min confidence 0.7 |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/generate_custom.py` | `generate_custom` | step generate | - | `judges/prompts/authoring/eval_maker_generate.py` | emits `custom.rubric` / `custom.requirements_compliance` / `custom.judge_prompt` |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/reflect_on_generation.py` | `reflect_on_generation` | step reflect (<= 2) | - | `judges/prompts/authoring/eval_maker_reflect.py` | fed by `lint_suite` |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/validate_structure.py` | `validate_structure` | step validate | - | `authoring/eval_maker.py`, `authoring/lint.py` | `Suite.load` + `lock_suite` + `lint_suite` |
| `agent-lens-core/src/agent_lens/eval_maker/nodes/preview_evaluators.py` | `preview_evaluators` | – (not ported) | - | – | interactive preview; the CLI prints the suite instead |
| `agent-lens-core/src/agent_lens/eval_maker/tools/blackbox_registry.py` | `blackbox_registry` | metric registry | - | `metrics/registry.py` | `list_specs()` is the catalogue the maker sees |
| `agent-lens-core/src/agent_lens/eval_maker/tools/{config_serializer,instantiator,rubric_formatter}.py` | (module group) | suite serialisation | - | `types/suite.py`, `engine/resolve.py` |  |
| `agent-lens-core/src/agent_lens/eval_maker/validation.py` | `validation` | `authoring.lint` | - | `authoring/lint.py` | arbitrability checks are new |

### 3.6 Generators and simulated users (`agent_lens/generators`, `agent_sim`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/generators/topic_planner.py:30` | `TopicPlanner` | `generation.agentic.topics` | - | `generation/agentic/topics.py` |  |
| `agent-lens-core/src/agent_lens/generators/dataset_generator.py:46` | `DatasetGeneratorRefactored` | – (not ported) | - | – | broken in the source |
| `agent-lens-core/src/agent_sim/simulation/user_simulator.py:85` | `UserSimulator` | `SimulatedUserTarget` | - | `engine/simulated.py` | USER_MESSAGE_PROMPT verbatim |
| `agent-lens-core/src/agent_sim/simulation/user_simulator.py:25` | `CompetencyLevel` | `types.simulation.CompetencyLevel` | - | `types/simulation.py` | 4 levels + descriptions |
| `agent-lens-core/src/agent_sim/simulation/schemas.py:17` | `StressAxis` | `types.simulation`, `conversation.failure_modes` | - | `types/simulation.py` | 10 axes |
| `agent-lens-core/src/agent_sim/simulation/schemas.py:32` | `FailureMode` | `types.simulation`, `conversation.failure_modes` | - | `types/simulation.py` | 10 modes |
| `agent-lens-core/src/agent_sim/simulation/planner.py:74` | `ScenarioPlanner` | `generation.simulation.planner` | - | `generation/simulation/planner.py` | SCENARIO_GENERATION_PROMPT |
| `agent-lens-core/src/agent_sim/simulation/planner.py:51` | `CoverageTracker` | `generation.simulation.planner` | - | `generation/simulation/planner.py` | StressAxis x FailureMode coverage |
| `agent-lens-core/src/agent_sim/simulation/conversation_analyzer.py:24` | `ConversationAnalyzer` | `conversation.failure_modes` | native | `metrics/conversation/failure_modes.py` | labels verbatim; the score 1 − share of failure modes hit is ours (the source does not score) |
| `agent-lens-core/src/agent_sim/simulation/simulator_offline.py:117` | `run_simulation` | `generation.simulation.run_simulation` | - | `generation/simulation/simulator.py` | drives any `Target` |
| `agent-lens-core/src/agent_sim/simulation/simulator_online.py:172` | `run_simulation_online` | `generation.simulation` + `SimulatedUserTarget` | - | `generation/simulation/simulator.py` | the AgentCore runtime executor is replaced by our targets |
| `agent-lens-core/src/agent_sim/simulation/agent_mock.py:41` | `MockAgent` | – (not ported) | - | – | offline mock agent; use `CallableTarget` |

### 3.7 Judge cost reduction (`agent_cost_reduction`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_cost_reduction/benchmark.py:744` | `run_benchmark` | `analysis.calibration` | - | `analysis/calibration.py` | MAE, per-class MAE, Cohen's / weighted kappa, MCC, fail precision / recall / F1, ranking, cost |
| `agent-lens-core/src/agent_cost_reduction/error_injector.py:493` | `inject_errors` | `generation.error_injection` | - | `generation/error_injection.py`, `judges/prompts/analysis/error_injection.py` | clean / faulty datasets; prompts verbatim |
| `agent-lens-core/src/agent_cost_reduction/dataset_generator.py:122` | `generate_dataset` | `generation.simulation` | - | `generation/simulation/simulator.py` | `CustomerProfile` = `Persona` (competency + traits) |
| `agent-lens-core/src/agent_cost_reduction/dataset_generator.py:163` | `generate_dataset_online` | `generation.simulation` | - | `same` |  |
| `agent-lens-core/src/agent_cost_reduction/visualize_benchmark.py:711` | `generate_report` | – (not ported) | - | – | Plotly HTML; `analysis.calibration` returns the data |
| `agent-lens-core/src/agent_cost_reduction/schemas.py:64` | `BenchmarkConfig` | `analysis.calibration` args | - | `analysis/calibration.py` |  |

### 3.8 Trace mappers (`agent_lens/mappers`) → `adapters.traces`

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/mappers/strands_in_memory_session_mapper.py` | `strands_in_memory_session_mapper` | `adapters.traces.otel_genai` | - | `adapters/traces/otel_genai.py` | OTel GenAI semconv legacy + >= 1.37; registered for auto-detection (AS left it out) |
| `agent-lens-core/src/agent_lens/mappers/agentcore/mappers/otel_span_mapper.py` | `otel_span_mapper` | `adapters.traces.agentcore_adot` | - | `adapters/traces/agentcore_adot.py` | with the three scope mappers below |
| `agent-lens-core/src/agent_lens/mappers/agentcore/mappers/scope_based_mappers/strands/telemetry_tracer_mapper.py` | `telemetry_tracer_mapper` | `agentcore_adot.StrandsScope` | - | `adapters/traces/agentcore_adot.py` |  |
| `agent-lens-core/src/agent_lens/mappers/agentcore/mappers/scope_based_mappers/langgraph/opentelemetry_instrumentation_langchain_mapper.py` | `opentelemetry_instrumentation_langchain_mapper` | `agentcore_adot.OpenLLMetryLangchainScope` | - | `adapters/traces/agentcore_adot.py` |  |
| `agent-lens-core/src/agent_lens/mappers/agentcore/mappers/scope_based_mappers/langgraph/openinference_instrumentation_langchain_mapper.py` | `openinference_instrumentation_langchain_mapper` | `agentcore_adot.OpenInferenceLangchainScope` | - | `adapters/traces/agentcore_adot.py` |  |
| `agent-lens-core/src/agent_lens/mappers/langfuse_mapper.py` | `langfuse_mapper` | `adapters.traces.langfuse.LangfuseStrandsAdapter` | - | `adapters/traces/langfuse.py` |  |
| `agent-lens-core/src/agent_lens/mappers/langchain_langfuse_mapper.py` | `langchain_langfuse_mapper` | `LangfuseLangchainAdapter` | - | `adapters/traces/langfuse.py` |  |
| `agent-lens-core/src/agent_lens/mappers/langgraph_langfuse_mapper.py` | `langgraph_langfuse_mapper` | `LangfuseLanggraphAdapter` | - | `adapters/traces/langfuse.py` |  |
| `agent-lens-core/src/agent_lens/mappers/langgraph_langsmith_mapper.py` | `langgraph_langsmith_mapper` | `adapters.traces.langsmith` | - | `adapters/traces/langsmith.py` |  |
| `agent-lens-core/src/agent_lens/mappers/smolagents_mapper.py` | `smolagents_mapper` | `adapters.traces.smolagents` | - | `adapters/traces/smolagents.py` |  |
| `agent-lens-core/src/agent_lens/mappers/taubench_session_mapper.py` | `taubench_session_mapper` | `adapters.traces.taubench` | - | `adapters/traces/taubench.py` |  |
| `agent-lens-core/src/agent_lens/mappers/cx0_mapper.py` | `cx0_mapper` | `adapters.traces.cx0` | - | `adapters/traces/cx0.py` |  |
| `agent-lens-core/src/agent_lens/mappers/eb_habitat_mapper.py` | `eb_habitat_mapper` | `adapters.traces.eb_habitat` | - | `adapters/traces/eb_habitat.py` |  |
| `agent-lens-core/src/agent_lens/mappers/session_mapper_factory.py` | `session_mapper_factory` | `adapters.traces` auto-detection | - | `adapters/traces/base.py (`AdapterRegistry`)` | plus the native JSON format (`adapters/traces/native.py`, new) |

### 3.9 Evaluation framework, aggregation, reporting (`agent_lens/evaluators`, `orchestration`, ...)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/evaluators/evaluator.py` | `evaluator` | `BaseMetric` | - | `metrics/base.py` | `_prepare_evaluation_case` trajectory fallback for output not ported (see §5) |
| `agent-lens-core/src/agent_lens/evaluators/eval_description.py` | `eval_description` | `MetricSpec.description`, `authoring.catalog_doc` | - | `metrics/base.py`, `authoring/catalog_doc.py` |  |
| `agent-lens-core/src/agent_lens/evaluators/custom/custom_eval_utils.py:227` | `aggregate_scores` | `engine.aggregate.combine` | - | `engine/aggregate.py` | weighted_average / mean / min / max |
| `agent-lens-core/src/agent_lens/evaluators/custom/custom_eval_runner.py` | `custom_eval_runner` | `engine.runner` | - | `engine/runner.py` |  |
| `agent-lens-core/src/agent_lens/orchestration/report.py:27` | `OrchestratorReport` | `engine.aggregate.decide` (`passes_thresholds`) | - | `engine/aggregate.py` | overall, pass rate, per evaluator |
| `agent-lens-core/src/agent_lens/orchestration/orchestrator.py` | `orchestrator` | `engine.runner` | - | `engine/runner.py` |  |
| `agent-lens-core/src/agent_lens/evaluation_runner.py` | `evaluation_runner` | `engine.runner` | - | `engine/runner.py` |  |
| `agent-lens-core/src/agent_lens/data/loader.py` | `loader` | `engine.dataset` | - | `engine/dataset.py` | local files / `StoragePort`; S3 client not ported |
| `agent-lens-core/src/agent_lens/display/display_console.py` | `display_console` | `reporting.console` | - | `reporting/console.py` |  |
| `agent-lens-core/src/agent_lens/utils/json_to_report.py` | `json_to_report` | `reporting.markdown` / `json_report` / `csv` | - | `reporting/*.py` |  |
| `agent-lens-core/src/agent_lens/metrics_reporter.py:14` | `MetricsReporter` | `reporting.exporters` | - | `reporting/exporters/{otel,prometheus,mlflow}.py` |  |
| `agent-lens-core/src/agent_lens/telemetry/config.py` | `config` | `reporting.exporters.otel` | - | `reporting/exporters/otel.py` |  |
| `agent-lens-core/src/agent_lens/extractors/trace_extractor.py` | `trace_extractor` | trajectory rendering (`agent_id`) | - | `adapters/traces/render.py` |  |
| `agent-lens-core/src/agent_lens/extractors/relationship_builder.py` | `relationship_builder` | trajectory rendering (multi-agent) | - | `adapters/traces/render.py`, `metrics/agent/_whitebox.py` |  |
| `agent-lens-core/src/agent_lens/clients/{cloudwatch_otel_client,langfuse_client,langsmith_client,trace_loader}.py` | (module group) | – (not ported) | - | – | live API fetch clients; adapters read exported traces |

### 3.10 Trace analyzer (`agent_lens/analyzer`) — deferred

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `agent-lens-core/src/agent_lens/analyzer/analyzer.py:19` | `Analyzer` | – (deferred) | - | – | LLM-written mappers |
| `agent-lens-core/src/agent_lens/analyzer/mapper_creator.py:32` | `MapperCreator` | – (deferred) | - | – |  |
| `agent-lens-core/src/agent_lens/analyzer/code_interpreter_tools.py:39` | `CodeInterpreter` | – (deferred) | - | – |  |
| `agent-lens-core/src/agent_lens/analyzer/trace_quality_analyzer.py:35` | `TraceQualityAnalyzer` | partly `authoring.recommend` (trace-quality prerequisites) | - | `authoring/recommend.py` | deterministic checks only |
| `agent-lens-core/src/agent_lens/analyzer/session_conversion_analysis.py:31` | `SessionConversionAnalyzer` | – (deferred) | - | – |  |

---

## 3A. STED (`amazon-science/sted`)

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `sted/semantic_json_tree_consistency.py:78`, `:1270` | `SemanticJsonTreeConsistencyEvaluator` (structure-guided `_calculate_optimal_matching_cost`, Eq. 1–3) | `ops.consistency` | `sted` (judge: embeddings) | `bnbagent_eval/metrics/ops/sted.py` (`Sted`, `build_tree`, `hungarian`) | our implementation (pure Python, no scipy); params `structural_weight`, `exact_match_fields`, `order_sensitive_fields`, `exact_match_all_keys`; embeddings from the judge's provider (Bedrock Titan v2 / OpenAI `text-embedding-3-small` by default); the authors' "fast" path pruning shortcuts are not part of the definition and not reproduced; identical results on ~999/1000 random pairs (ties in structurally optimal pairings can differ by < 0.02), `tests/parity/test_sted_parity.py` |
| `sted/semantic_json_tree_consistency.py:741`, `:410` | `_calculate_semantic_similarity`, `_preprocess_key_name` | `ops.consistency` | `sted` | `bnbagent_eval/metrics/ops/sted.py` (`EmbeddingSimilarity`, `prepare_text`) | rescaled cosine; strings < 4 chars by positional character agreement; text prepared (camelCase split, separators, lowercase) for every provider (the reference does this for local models only) |
| `sted/structural_consistency_analyzer.py:92`, `:244` | `evaluate_structural_consistency`, `_calculate_consistency_metrics` | `ops.consistency` | `sted` (param `score`, default `c_adj`) | `bnbagent_eval/metrics/ops/sted.py` (`consistency`, `is_valid`) | identical formulas (`c_mean`, `d_std`, `r_v`, `c_adj = r_v · c_mean`, stability, ranking, legacy coefficient); empty outputs are invalid runs; note the reference analyzer's `method_name="ted"` (used by the prototype) is plain ZSS TED, not STED |
| `sted/agent_consistency_evaluator.py:171` | `AgentConsistencyEvaluator` (run an agent N times per prompt, score consistency) | `engine` targets + `options.runs` + `ops.consistency` / `sted` | – | `bnbagent_eval/engine/runner.py`, `engine/targets.py` | the runner invokes the target `runs` times per case; `ops.consistency` scores the runs |
| `sted/otel_adapter.py:166` | `trajectories_from_records` (OTel tool events → trajectories) | `adapters.traces` | – | `bnbagent_eval/adapters/traces/otel_genai.py` | our OTel GenAI adapter builds the trajectory |
| `sted/_baselines.py:27` | TED / BERTScore / DeepDiff baselines | `ops.consistency` `ted`; `text.bertscore` | rule; model | `bnbagent_eval/metrics/ops/consistency.py`, `metrics/text/bertscore.py` | the paper's TED baseline = our `ted` method (exact labels); DeepDiff not ported |
| `sted/llm_judge.py:18` | `LLMJudge` (tool-call generation / judging for experiments) | – (not ported) | – | – | experiment tooling; our judges cover judging |
| `sted/consistency_predictor.py:63` | `ConsistencyPredictor` | – (not ported) | – | – | predicts consistency before running; research utility |
| `sted/consistency_risk_assessor.py:351` | `ConsistencyRiskAssessor` | – (not ported) | – | – | prompt / schema risk report; research utility |

STED's `format_converters` (XML / YAML / HTML → dict) and CLI are not ported: runs are parsed as
JSON (non-JSON outputs become `{"value": ...}`, as the prototype did). No STED code is imported:
the authors' package is only a test oracle (`tests/parity/test_sted_parity.py`, CI nightly).

## 3B. Earlier bnbagent-eval prototype (`BNB_reference/bnbagent-eval`)

The prototype ran AgentSight + STED inside an ETK plugin and documented a metric catalogue
(`general-eval-framework/02-metrics-catalogue.md`) whose AgentSight / AgentCore / RAGAS rows are
the same items as §2 / §3 (all mapped there). Its own logic:

| Source (repo-relative:line) | Source name | Canonical ID / module | Method | Our file | Notes |
|---|---|---|---|---|---|
| `bnb-apex-eval-plugin/src/bnb_apex_eval_plugin/processor.py:28` | `schema_compliance` (AgentSight `OutputStructureComplianceEvaluator`) | `format.json_schema` | `rule` | `bnbagent_eval/metrics/format/structured.py` | same AgentSight evaluator as §3.1 |
| `bnb-apex-eval-plugin/src/bnb_apex_eval_plugin/processor.py:43` | `rubric_match` (AgentSight `CustomPromptEvaluator`) | `custom.judge_prompt` | `native` | `bnbagent_eval/metrics/custom/judge_prompt.py` | rubric text = `params.prompt` |
| `bnb-apex-eval-plugin/src/bnb_apex_eval_plugin/processor.py:70` | `consistency_coefficient` (STED over repeated outputs) | `ops.consistency` | `sted` with `score: consistency_coefficient` | `bnbagent_eval/metrics/ops/sted.py` | the prototype called the analyzer with `method_name="ted"`, which is plain tree edit distance (our `ted`); our `sted` is the semantic version; non-dict outputs → `{"value": ...}` as there; the default score is `c_adj` (penalizes empty runs) |
| `bnb-apex-eval-plugin/src/bnb_apex_eval_plugin/processor.py:119` | aggregate over quality scores only (consistency reported separately) | `MetricConfig.weight: 0` + `required: true` | – | `bnbagent_eval/engine/aggregate.py` | a weight-0 required consistency metric gates the verdict without entering the aggregate |
| `bnb-apex-eval-plugin/src/bnb_apex_eval_plugin/processor.py:105` | failure diagnosis (AgentSight `FailureDiagnoser`) | `analysis.diagnosis` | – | `bnbagent_eval/analysis/diagnosis.py` | §3.4 |
| `bnb-apex-orchestration/src/bnb_apex_orchestration/consistency.py:45` | `run_with_consistency` (N samples; submit / decline from quality + consistency bands) | `options.runs` + suite pass rule + `EvalGate` | – | `bnbagent_eval/chain/erc8183.py` | the two-band rule (high consistency relaxes the quality threshold to a floor) is not expressed: use a required `ops.consistency` threshold and the quality `min_aggregate`; not ported as such |
| `general-eval-framework/02-metrics-catalogue.md:100` | AgentCore custom LLM-as-judge with an inline definition (`CreateEvaluator`) | `agentcore` method, param `agentcore_evaluator_id` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | design-only in the prototype (no code): an existing custom evaluator runs by id; creating evaluators from an eval run is not done (it mutates cloud resources); `custom.judge_prompt` is the in-package equivalent |
| `general-eval-framework/02-metrics-catalogue.md:101` | AgentCore custom code-based (Lambda) evaluators | `agentcore` method, param `agentcore_evaluator_id` | `agentcore` | `bnbagent_eval/metrics/vendors/agentcore.py` | run by id; in-package deterministic checks are `format.*` / `custom.python` |

## 4. Not ported, by decision

| Toolkit | Item | Reason |
|---|---|---|
| ETK | `llm_as_judge` `sample` | test evaluator (fixed 0.75); the scripted `FakeJudge` replaces it |
| ETK | `app_invoke/agentcore.py`, `app_invoke/bedrock_converse.py` | AWS runtime invokers; targets are `CallableTarget` / `HttpTarget` / `A2ATarget` / `McpTarget` |
| ETK | `Builtin.ContextRelevance` | excluded by ETK itself (the AWS API errors on it) |
| ETK | `etk-sdk` remote / gateway / batch jobs; generator Bedrock batch clients | ETK service plumbing; replaced by our server and client |
| AS | `generators/dataset_generator.py` (`DatasetGeneratorRefactored`) | broken in the source |
| AS | `analyzer/*` (Analyzer, MapperCreator, CodeInterpreter, SessionConversionAnalyzer) | deferred; deterministic trace-quality checks are in `authoring.recommend` |
| AS | `clients/*` (CloudWatch, Langfuse, LangSmith fetch clients) | adapters read exported traces; no live API access in the library |
| AS | `agent_sim/simulation/agent_mock.py` (`MockAgent`) | use any target (e.g. `CallableTarget`) |
| AS | `agent_cost_reduction/visualize_benchmark.py` | Plotly HTML report; `analysis.calibration` returns the numbers |
| AS | `eval_maker/nodes/preview_evaluators.py` | interactive preview step |

## 5. Differences from the sources

Where a row of §2 / §3 maps an item, the port reproduces the source's behaviour, with the
exceptions below. Source-parity behaviour is pinned by `tests/unit/metrics/test_source_parity.py`
and `tests/unit/metrics/test_metric_edge_cases.py`.

### 5.1 Intended differences

| Metric / area | Difference | Where documented |
|---|---|---|
| `safety.refusal` | score = behaviour matches `expect_refusal`; AS `refused` / `category` in details | `metrics/safety/refusal.py` |
| `safety.bias` native vs deepeval | AS UNBIASED = 1 (not inverted); DeepEval inverted | `metrics/safety/bias.py` |
| `rag.hallucination`, `safety.toxicity` (native, deepeval, fmeval), `safety.bias:deepeval`, `safety.misuse:deepeval` | higher-is-worse sources inverted to `1 − raw` (DeepEval >= 4.2 already scores these "1 is a pass"; the bridge reads its version and reports the violation share as `raw` either way) | `metrics/vendors/deepeval.py` |
| `redteam.turns_to_break` | (turns − 1) / max_turns normalisation (a break on the last turn stays below a resisted attack) | `metrics/redteam/` |
| `conversation.failure_modes` | score 1 − share of failure modes hit (AS only labels) | `metrics/conversation/failure_modes.py` |
| step-ratio metrics | clamped to [0, 1] (AS can exceed 1) | `metrics/agent/_whitebox.py` |
| `agent.tool_use_quality` v0 | different placeholder names, correct / incorrect scoring (the source v0 could not run) | `judges/prompts/agent/tool_use_quality.py` |
| `agent.tool_selection` native | 5-tier labels with ETK `tool_use` semantics | `metrics/agent/tool_selection.py` |
| whitebox rubric metrics | each invocation uses its own user input (the source overwrote it with the last earlier turn's) | source bug, not copied |
| text metrics | expected text split on `<OR>` into alternatives (best match) for every text metric (FMEval convention; AS does not split) | `metrics/text/_common.py` |
| text metrics | CJK text tokenized per character (AS / FMEval tokenizers score CJK 0) | `metrics/text/overlap.py` docstring |
| `format.xml` | DOCTYPE / ENTITY rejected, schemaLocation rejected (security) | `metrics/format/structured.py` docstring |
| judges | parse failure → one repair re-ask, then `status=error` (AS returns score 0 "Error"); stricter output schemas | `judges/structured.py` |
| `rag.faithfulness`, completeness context call | contexts rendered `[i] text (source, timestamp)` instead of a Python list repr; AS's deprecated fallback to `expected` as context dropped | `metrics/judge_base.py` `contexts_text` |
| `quality.helpfulness` | `include_inputs: false` blanks the input (stored but unused in AS) | module |
| `custom.requirements_compliance` | ETK agent prompt adapted to one native call; `agentic` method keeps the tool loop | module docstring |
| DeepEval `exact_match` / `pattern_match` / `json_correctness` aliases | resolve to our deterministic `rule` method; `method: deepeval` selects DeepEval | `metrics/vendors/deepeval.py` (`alias=False`) |
| `format.json_valid`, `format.json_schema` (rule), `ops.consistency` `ted` / `sted` | the whole text must parse as JSON, else a fenced code block is used (AS parses the whole text only); JSON inside prose is not accepted | `judges/structured.py` `extract_json` |
| `format.json_schema` (rule) | only local `$ref`s resolve (no `file://` read or network fetch) | `metrics/format/structured.py` |
| `text.meteor` | sentences split on `[.!?]` instead of punkt (no NLTK data download) | `metrics/text/overlap.py` |
| `agent.tool_trajectory_match`, `onchain.intent_match` `any_order` | a maximum one-to-one assignment of expected to actual calls (AS scans first matches; identical with exact arguments, but with `compare_args: subset` / `ignore` or `arg_tolerance` a first match can consume a call another expected call needs) | `metrics/agent/trajectory.py` `unmatched` |

### 5.2 Known divergences (low severity)

| Area | Divergence | Source |
|---|---|---|
| judges, input-mode detection | AS `detect_input_format` also counts trajectory turns, and `_prepare_evaluation_case` fills a missing output from the trajectory's last turn; ours uses explicit `conversation` messages only (helpfulness "or traj" cases without `output` are skipped) | `blackbox/utils.py:148`, `evaluators/evaluator.py:145-173` |
| judges | empty / whitespace output is judged (AS rejects it) | `blackbox/categories.py:227-229` |
| whitebox rubric metrics | AS falls back to a `Property: / Verdict:` text parser; ours parses JSON only | `whitebox/rubric_based_evaluator.py:121-133` |
| `agent.tool_parameter_accuracy`, `custom.rubric`, `custom.judge_prompt` pass/fail | label enums are case-sensitive (AS lowercases); step-ratio schemas require every array | `whitebox/tool_parameter_accuracy_evaluator.py` |
| `agent.execution_chain` | unparseable reply after the repair re-ask → error (AS scores every link 3) | `whitebox/execution_chain_evaluator.py:222` |
| whitebox prompts | "tool use" rendered from our conversation record instead of AS JSON (`get_tool_calls_and_responses_as_json_str`) | `whitebox/utils.py` |
| `custom.requirements_compliance` | duplicate requirement ids keep the last (ETK rejects them); all-zero weights raise | `evaluator_agent_as_judge/models.py:206-211` |
| `text.meteor` | no WordNet synonym stage (exact + stem matching only; FMEval's NLTK METEOR also matches WordNet synonyms) | fmeval `transforms/summarization_accuracy_metrics.py:199` |
| `agent.execution_chain` | a fractional link score is truncated to an integer (AS's `ChainScore.score: int` rejects it) and the default is 3 samples regardless of the judge config (AS `num_samples=3`) | `whitebox/execution_chain_evaluator.py:63,104` |
| vendor bridges | a metric's params schema is shared by all its methods, so one bridge's params (e.g. FMEval `variant`, RAGAS `mode`) validate on every metric it attaches to; vendor judge calls honour the run budget and deadline but not the judge cache | `metrics/registry.py` `register_method` |

### 5.3 Catalogue and registry

[metric-catalog.md](metric-catalog.md) matches the registry row by row (IDs, categories,
methods, thresholds, inputs, parameters, sources). `scripts/audit_catalog.py` (repository root)
compares them and reports any mismatch.

---

## 6. Reverse index: canonical ID → sources

Mirrors the registry (`list_specs()`, `aliases()`; the sources and aliases columns are checked by
`test_source_map_reverse_index_matches_the_registry`); methods attached by another module show
that module. `bnbagent-eval:new` = metrics (or methods) added in this package, with no
source-toolkit counterpart; the prototype's metrics they cover are mapped in §3B.

| Canonical ID | Methods (attached in) | Our file | `MetricSpec.sources` | Aliases |
|---|---|---|---|---|
| `agent.adaptivity` | `native` | `metrics/agent/adaptivity.py` | `agentsight:AgentAdaptivityEvaluator` | – |
| `agent.confidence` | `rule` | `metrics/agent/confidence.py` | `agentsight:ConfidenceScorer`; `agentsight:SignalExtractor`; `agentsight:ScoreDeriver`; `agentsight:ScoreAggregator`; `agentsight:ConfidenceSummary`; `agentsight:insights`; `agentsight:configs/scoring/scoring_config.yaml`; `agentsight:configs/scoring/domain_scoring_profiles.yaml` | – |
| `agent.execution_chain` | `native` | `metrics/agent/execution_chain.py` | `agentsight:ExecutionChainEvaluator` | – |
| `agent.goal_success` | `native`, `agentcore` (vendors/agentcore.py), `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/agent/goal_success.py` | `agentsight:GoalSuccessRateEvaluator`; `ragas:agent_goal_accuracy`; `ragas:agent_goal_accuracy_with_reference`; `deepeval:goal_accuracy`; `agentcore:Builtin.GoalSuccessRate` | `deepeval.goal_accuracy`, `ragas.agent_goal_accuracy`, `ragas.agent_goal_accuracy_with_reference`, `vendor.agentcore.Builtin.GoalSuccessRate` |
| `agent.mcp_task_completion` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/mcp_task_completion.py` | `deepeval:mcp_task_completion` | `deepeval.mcp_task_completion` |
| `agent.mcp_use` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/mcp_use.py` | `deepeval:mcp_use`; `deepeval:multi_turn_mcp_use` | `deepeval.mcp_use`, `deepeval.multi_turn_mcp_use` |
| `agent.multi_agent_trajectory` | `native` | `metrics/agent/multi_agent_trajectory.py` | `agentsight:FullTrajectoryQualityEvaluator`; `agentsight:trajectory_accuracy` | – |
| `agent.plan_adherence` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/plan_adherence.py` | `deepeval:plan_adherence` | `deepeval.plan_adherence` |
| `agent.plan_quality` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/plan_quality.py` | `deepeval:plan_quality` | `deepeval.plan_quality` |
| `agent.reasoning` | `native` | `metrics/agent/reasoning.py` | `agentsight:AgentReasoningEvaluator` | – |
| `agent.reasoning_hallucination` | `native` | `metrics/agent/reasoning_hallucination.py` | `agentsight:AgentHallucinationEvaluator` | – |
| `agent.task_completion` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/task_completion.py` | `deepeval:task_completion` | `deepeval.task_completion` |
| `agent.tool_parameter_accuracy` | `native`, `agentcore` (vendors/agentcore.py), `deepeval` (vendors/deepeval.py) | `metrics/agent/tool_parameter_accuracy.py` | `agentsight:ToolParameterAccuracyEvaluator`; `deepeval:argument_correctness`; `agentcore:Builtin.ToolParameterAccuracy` | `deepeval.argument_correctness`, `vendor.agentcore.Builtin.ToolParameterAccuracy` |
| `agent.tool_selection` | `native`, `agentcore` (vendors/agentcore.py), `deepeval` (vendors/deepeval.py) | `metrics/agent/tool_selection.py` | `etk:llm_as_judge.tool_use`; `deepeval:tool_use`; `agentcore:Builtin.ToolSelectionAccuracy` | `deepeval.tool_use`, `vendor.agentcore.Builtin.ToolSelectionAccuracy` |
| `agent.tool_trajectory_match` | `rule`, `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/agent/trajectory.py` | `agentsight:AgentToolTrajectoryMatchEvaluator`; `google-adk:tool_trajectory`; `ragas:tool_call_accuracy`; `deepeval:tool_correctness` | `deepeval.tool_correctness`, `ragas.tool_call_accuracy` |
| `agent.tool_use_quality` | `native` | `metrics/agent/tool_use_quality.py` | `agentsight:ToolUseQualityEvaluator`; `agentsight:trajectory_accuracy` | – |
| `agent.trajectory_efficiency` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/agent/trajectory_efficiency.py` | `agentsight:TrajectoryEfficiencyEvaluator`; `deepeval:step_efficiency` | `deepeval.step_efficiency` |
| `agent.trajectory_stats` | `rule` | `metrics/agent/trajectory.py` | `agentsight:SignalExtractor`; `agentsight:insights` | – |
| `code.syntax_valid` | `rule` | `metrics/code/syntax_valid.py` | `bnbagent-eval:new` | – |
| `code.tests_pass` | `rule` | `metrics/code/tests_pass.py` | `bnbagent-eval:new` | – |
| `conversation.coherence` | `native` | `metrics/conversation/coherence.py` | `agentsight:ConversationalCoherenceEvaluator` | – |
| `conversation.completeness` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/completeness.py` | `agentsight:ConversationCompletenessEvaluator`; `deepeval:conversation_completeness` | `deepeval.conversation_completeness` |
| `conversation.entity_consistency` | `native` | `metrics/conversation/entity_consistency.py` | `agentsight:EntityConsistencyEvaluator` | – |
| `conversation.failure_modes` | `native` | `metrics/conversation/failure_modes.py` | `agentsight:agent_sim.ConversationAnalyzer` | – |
| `conversation.knowledge_retention` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/knowledge_retention.py` | `agentsight:KnowledgeRetentionEvaluator`; `deepeval:knowledge_retention` | `deepeval.knowledge_retention` |
| `conversation.role_adherence` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/role_adherence.py` | `deepeval:role_adherence` | `deepeval.role_adherence` |
| `conversation.topic_adherence` | `native`, `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/conversation/topic_adherence.py` | `deepeval:topic_adherence`; `ragas:topic_adherence` | `deepeval.topic_adherence`, `ragas.topic_adherence` |
| `conversation.turn_context_precision` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/turn_context_precision.py` | `deepeval:turn_contextual_precision` | `deepeval.turn_contextual_precision` |
| `conversation.turn_context_recall` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/turn_context_recall.py` | `deepeval:turn_contextual_recall` | `deepeval.turn_contextual_recall` |
| `conversation.turn_context_relevancy` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/turn_context_relevancy.py` | `deepeval:turn_contextual_relevancy` | `deepeval.turn_contextual_relevancy` |
| `conversation.turn_faithfulness` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/turn_faithfulness.py` | `deepeval:turn_faithfulness` | `deepeval.turn_faithfulness` |
| `conversation.turn_relevancy` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/conversation/turn_relevancy.py` | `deepeval:turn_relevancy` | `deepeval.turn_relevancy` |
| `custom.human_review` | `rule` | `metrics/custom/human_review.py` | `bnbagent-eval:new` | – |
| `custom.judge_prompt` | `native` | `metrics/custom/judge_prompt.py` | `agentsight:CustomPromptEvaluator`; `agentsight:WhiteboxCustomPromptEvaluator`; `etk:llm_as_judge(custom description)` | – |
| `custom.python` | `rule` | `metrics/custom/python.py` | `etk:function-scorers` | – |
| `custom.requirements_compliance` | `native`, `agentic` (vendors/agentic.py) | `metrics/custom/requirements.py` | `etk:agent_as_judge.requirements_compliance` | – |
| `custom.rubric` | `native`, `agentic` (vendors/agentic.py) | `metrics/custom/rubric.py` | `agentsight:RubricBasedEvaluator`; `etk:agent_as_judge(weighted scores)` | – |
| `format.artifact_integrity` | `rule` | `metrics/format/artifact_integrity.py` | `bnbagent-eval:new` | – |
| `format.json_schema` | `rule`, `native`, `deepeval` (vendors/deepeval.py) | `metrics/format/structured.py` | `deepeval:json_correctness`; `agentsight:OutputStructureCompliance[json_schema]` | `deepeval.json_correctness` |
| `format.json_valid` | `rule` | `metrics/format/structured.py` | `agentsight:OutputStructureCompliance[json]` | – |
| `format.regex` | `rule`, `native`, `deepeval` (vendors/deepeval.py) | `metrics/format/structured.py` | `deepeval:pattern_match`; `agentsight:OutputStructureCompliance[regex]`; `etk:ExampleTextQualityScorer.prefix` | `deepeval.pattern_match` |
| `format.structure` | `native` | `metrics/format/structure.py` | `agentsight:OutputStructureComplianceEvaluator(llm)` | – |
| `format.xml` | `rule`, `native` | `metrics/format/structured.py` | `agentsight:OutputStructureCompliance[xml]`; `agentsight:OutputStructureCompliance[xml_schema]` | – |
| `onchain.approval_safety` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `onchain.balance_delta` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `onchain.gas_efficiency` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `onchain.intent_match` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `onchain.spend_policy` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `onchain.tx_success` | `rule` | `metrics/onchain/actions.py` | `bnbagent-eval:new` | – |
| `ops.consistency` | `exact`, `rouge`, `ted`, `judge`, `sted`, `embedding` (vendors/local_models.py) | `metrics/ops/consistency.py`, `metrics/ops/sted.py` | `bnbagent-eval:new`; `paper:STED and consistency scoring (Wang et al., arXiv 2512.23712)` | – |
| `ops.cost` | `rule` | `metrics/ops/operational.py` | `agentsight:SpanMetrics.cost`; `agentsight:bedrock_pricing`; `bnbagent-eval:new` | – |
| `ops.latency` | `rule` | `metrics/ops/operational.py` | `agentsight:SpanMetrics`; `bnbagent-eval:new` | – |
| `ops.reliability` | `rule` | `metrics/ops/operational.py` | `bnbagent-eval:new` | – |
| `ops.token_usage` | `rule` | `metrics/ops/operational.py` | `agentsight:SpanMetrics.tokens`; `agentsight:insights.cache_hit_rate` | – |
| `quality.answer_correctness` | `native`, `rule`, `ragas` (vendors/ragas.py) | `metrics/quality/answer_correctness.py` | `agentsight:AnswerCorrectnessEvaluator`; `ragas:answer_accuracy` | `ragas.answer_accuracy` |
| `quality.clarity` | `native` | `metrics/quality/clarity.py` | `agentsight:ClarityEvaluator` | – |
| `quality.coherence` | `native`, `agentcore` (vendors/agentcore.py) | `metrics/quality/coherence.py` | `agentcore:Builtin.Coherence` | `vendor.agentcore.Builtin.Coherence` |
| `quality.completeness` | `native` | `metrics/quality/completeness.py` | `agentsight:CompletenessEvaluator` | – |
| `quality.conciseness` | `native`, `agentcore` (vendors/agentcore.py) | `metrics/quality/conciseness.py` | `agentsight:ConcisenessEvaluator`; `agentcore:Builtin.Conciseness` | `vendor.agentcore.Builtin.Conciseness` |
| `quality.contradiction` | `native` | `metrics/quality/contradiction.py` | `agentsight:ContradictionEvaluator` | – |
| `quality.correctness` | `native`, `agentcore` | `metrics/quality/correctness.py` | `bnbagent-eval:new`; `agentcore:Builtin.Correctness` | `vendor.agentcore.Builtin.Correctness` |
| `quality.fluency` | `native`, `rule` (vendors/local_models.py) | `metrics/quality/fluency.py` | `agentsight:FluencyEvaluator` | – |
| `quality.helpfulness` | `native`, `agentcore` (vendors/agentcore.py) | `metrics/quality/helpfulness.py` | `agentsight:HelpfulnessEvaluator`; `agentcore:Builtin.Helpfulness` | `vendor.agentcore.Builtin.Helpfulness` |
| `quality.instruction_following` | `native`, `agentcore` (vendors/agentcore.py), `deepeval` (vendors/deepeval.py) | `metrics/quality/instruction_following.py` | `deepeval:prompt_alignment`; `agentcore:Builtin.InstructionFollowing` | `deepeval.prompt_alignment`, `vendor.agentcore.Builtin.InstructionFollowing` |
| `quality.relevance` | `native`, `agentcore` (vendors/agentcore.py), `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/quality/relevance.py` | `agentsight:RelevanceEvaluator`; `ragas:answer_relevancy`; `deepeval:answer_relevancy`; `agentcore:Builtin.ResponseRelevance` | `deepeval.answer_relevancy`, `ragas.answer_relevancy`, `vendor.agentcore.Builtin.ResponseRelevance` |
| `quality.summarization` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/quality/summarization.py` | `deepeval:summarization` | `deepeval.summarization` |
| `quality.tone` | `native` | `metrics/quality/tone.py` | `agentsight:ToneEvaluator` | – |
| `rag.context_freshness` | `native` | `metrics/rag/context_freshness.py` | `agentsight:ContextTimeFreshnessEvaluator` | – |
| `rag.context_precision` | `native`, `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/rag/context_precision.py` | `agentsight:ContextQualityEvaluator`; `ragas:LLMContextPrecisionWithoutReference`; `deepeval:contextual_precision` | `deepeval.contextual_precision`, `ragas.context_precision` |
| `rag.context_recall` | `native`, `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/rag/context_recall.py` | `ragas:context_recall`; `deepeval:contextual_recall` | `deepeval.contextual_recall`, `ragas.context_recall` |
| `rag.context_relevance` | `native`, `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/rag/context_relevance.py` | `agentsight:ContextQualityEvaluator`; `ragas:context_relevancy`; `deepeval:contextual_relevancy` | `deepeval.contextual_relevancy`, `ragas.context_relevancy` |
| `rag.faithfulness` | `native`, `agentcore` (vendors/agentcore.py), `ragas` (vendors/ragas.py), `deepeval` (vendors/deepeval.py) | `metrics/rag/faithfulness.py` | `agentsight:FactualCorrectnessEvaluator`; `ragas:faithfulness`; `deepeval:faithfulness`; `agentcore:Builtin.Faithfulness` | `deepeval.faithfulness`, `ragas.faithfulness`, `vendor.agentcore.Builtin.Faithfulness` |
| `rag.groundedness` | `native`, `ragas` (vendors/ragas.py) | `metrics/rag/groundedness.py` | `ragas:response_groundedness` | `ragas.response_groundedness` |
| `rag.hallucination` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/rag/hallucination.py` | `deepeval:hallucination` | `deepeval.hallucination` |
| `redteam.attack_resistance` | `rule` | `metrics/redteam/attack.py` | `etk:evaluator_pyrit.attack_succeeded` | `pyrit.attack_succeeded` |
| `redteam.prompt_injection` | `rule`, `native` | `metrics/redteam/prompt_injection.py` | `bnbagent-eval:new` | – |
| `redteam.turns_to_break` | `rule` | `metrics/redteam/attack.py` | `etk:evaluator_pyrit.turns_used` | `pyrit.turns_used` |
| `safety.bias` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/safety/bias.py` | `agentsight:BiasEvaluator`; `deepeval:bias` | `deepeval.bias` |
| `safety.harmfulness` | `native`, `agentcore` (vendors/agentcore.py), `model` (vendors/local_models.py) | `metrics/safety/harmfulness.py` | `agentsight:HarmfulnessEvaluator`; `agentcore:Builtin.Harmfulness` | `vendor.agentcore.Builtin.Harmfulness` |
| `safety.misuse` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/safety/misuse.py` | `deepeval:misuse` | `deepeval.misuse` |
| `safety.non_advice` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/safety/non_advice.py` | `deepeval:non_advice` | `deepeval.non_advice` |
| `safety.pii_leakage` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/safety/pii_leakage.py` | `agentsight:PIILeakageEvaluator`; `deepeval:pii_leakage` | `deepeval.pii_leakage` |
| `safety.refusal` | `native`, `agentcore` (vendors/agentcore.py) | `metrics/safety/refusal.py` | `agentsight:RefusalEvaluator`; `agentcore:Builtin.Refusal` | `vendor.agentcore.Builtin.Refusal` |
| `safety.role_violation` | `native`, `deepeval` (vendors/deepeval.py) | `metrics/safety/role_violation.py` | `deepeval:role_violation` | `deepeval.role_violation` |
| `safety.stereotyping` | `native`, `agentcore` (vendors/agentcore.py) | `metrics/safety/stereotyping.py` | `agentcore:Builtin.Stereotyping` | `vendor.agentcore.Builtin.Stereotyping` |
| `safety.toxicity` | `native`, `fmeval` (vendors/fmeval.py), `model` (vendors/local_models.py), `deepeval` (vendors/deepeval.py) | `metrics/safety/toxicity.py` | `etk:llm_as_judge.toxicity`; `deepeval:toxicity`; `fmeval:toxicity` | `deepeval.toxicity`, `fmeval.qa_toxicity`, `fmeval.qa_toxicity.identity_attack`, `fmeval.qa_toxicity.insult`, `fmeval.qa_toxicity.obscene`, `fmeval.qa_toxicity.severe_toxicity`, `fmeval.qa_toxicity.sexual_explicit`, `fmeval.qa_toxicity.threat`, `fmeval.qa_toxicity.toxicity`, `fmeval.summarization_toxicity`, `fmeval.summarization_toxicity.identity_attack`, `fmeval.summarization_toxicity.insult`, `fmeval.summarization_toxicity.obscene`, `fmeval.summarization_toxicity.severe_toxicity`, `fmeval.summarization_toxicity.sexual_explicit`, `fmeval.summarization_toxicity.threat`, `fmeval.summarization_toxicity.toxicity`, `fmeval.toxicity`, `fmeval.toxicity.identity_attack`, `fmeval.toxicity.insult`, `fmeval.toxicity.obscene`, `fmeval.toxicity.severe_toxicity`, `fmeval.toxicity.sexual_explicit`, `fmeval.toxicity.threat`, `fmeval.toxicity.toxicity` |
| `text.bertscore` | `model`, `fmeval` (vendors/fmeval.py) | `metrics/text/bertscore.py` | `agentsight:calculate_bertscore`; `agentsight:AnswerCorrectness[semantic]`; `fmeval:bertscore` | `fmeval.summarization_accuracy.bertscore` |
| `text.bleu` | `rule` | `metrics/text/overlap.py` | `agentsight:calculate_bleu` | – |
| `text.classification_accuracy` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/classify.py` | `fmeval:classification_accuracy` | `fmeval.classification_accuracy`, `fmeval.classification_accuracy.balanced_accuracy_score`, `fmeval.classification_accuracy.classification_accuracy_score`, `fmeval.classification_accuracy.precision_score`, `fmeval.classification_accuracy.recall_score` |
| `text.contains` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/exact.py` | `fmeval:factual_knowledge` | `fmeval.factual_knowledge`, `fmeval.factual_knowledge.factual_knowledge`, `fmeval.factual_knowledge.factual_knowledge_quasi_exact` |
| `text.embedding_similarity` | `keyword`, `embeddings` (vendors/local_models.py), `glove` (vendors/local_models.py), `fasttext` (vendors/local_models.py) | `metrics/text/similarity.py` | `agentsight:SemanticRelevanceScorer` | – |
| `text.exact_match` | `rule`, `fmeval` (vendors/fmeval.py), `deepeval` (vendors/deepeval.py) | `metrics/text/exact.py` | `fmeval:qa_accuracy.exact_match`; `deepeval:exact_match`; `agentsight:AnswerCorrectness[exact]`; `etk:example_exact_match` | `deepeval.exact_match`, `fmeval.qa_accuracy.exact_match_score` |
| `text.jaccard` | `rule` | `metrics/text/overlap.py` | `agentsight:calculate_jaccard`; `agentsight:AnswerCorrectness[jaccard]`; `agentsight:compute_keyword_overlap` | – |
| `text.length` | `rule` | `metrics/text/classify.py` | `etk:ExampleTextQualityScorer.length` | – |
| `text.meteor` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/overlap.py` | `fmeval:summarization_accuracy.meteor` | `fmeval.summarization_accuracy.meteor` |
| `text.quasi_exact_match` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/exact.py` | `fmeval:qa_accuracy.quasi_exact_match` | `fmeval.qa_accuracy.quasi_exact_match_score` |
| `text.rouge` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/overlap.py` | `agentsight:calculate_rouge_l`; `agentsight:AnswerCorrectness[rouge]`; `fmeval:summarization_accuracy.rouge` | `fmeval.summarization_accuracy`, `fmeval.summarization_accuracy.rouge` |
| `text.tfidf_cosine` | `rule` | `metrics/text/overlap.py` | `agentsight:calculate_cosine_similarity` | – |
| `text.token_f1` | `rule`, `fmeval` (vendors/fmeval.py) | `metrics/text/overlap.py` | `fmeval:qa_accuracy.f1_score`; `fmeval:qa_accuracy.precision_over_words`; `fmeval:qa_accuracy.recall_over_words` | `fmeval.qa_accuracy`, `fmeval.qa_accuracy.f1_score`, `fmeval.qa_accuracy.precision_over_words`, `fmeval.qa_accuracy.recall_over_words` |
