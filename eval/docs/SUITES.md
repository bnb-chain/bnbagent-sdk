# Suites

A suite is the contract of an evaluation: which metrics run, how each one is configured, how
results combine into a verdict, and which judge model scores the judge metrics. The same suite
file is used locally, pinned in an ERC-8183 job, published with ERC-8004 metrics, and sent to the
eval service. Its `suite_hash` (keccak over the resolved suite and the metric / prompt versions)
identifies it everywhere.

## Format

Suites are YAML or JSON files (`Suite.load(path)`), mappings (`Suite.load(dict)`) or `Suite`
objects.

<!-- doctest-file: metrics/exact.yaml -->
```yaml
# metrics/exact.yaml: a shared metric config, inlined with $ref
id: text.exact_match
params: {normalize: true}
required: true
```

<!-- doctest-file: suite.yaml -->
```yaml
name: market-summary
version: "2"
description: Daily BNB market summary written by the research agent.
metrics:
  - {$ref: metrics/exact.yaml}           # replaced by the content of that file
  - id: text.token_f1
    threshold: 0.6
  - id: format.json_valid
    name: json_ok                        # results are reported under this name
    required: true
  - id: quality.clarity
    method: native                       # optional: the first method is the default
    params: {samples: 3}
    weight: 2
  - id: ops.latency
    value_target: 2000                   # ms: score 1 at or below the target ...
    value_max: 8000                      # ... falling to 0 at the maximum
judge:
  provider: fake                         # openai | anthropic | bedrock (see JUDGES.md)
  model: fake
  temperature: 0
  samples: 3
pass_rule:
  aggregation: weighted_average          # weighted_average | mean | min | max
  min_aggregate: 0.7
  min_pass_rate: 0.9
  inconclusive_band: 0.05
  max_error_rate: 0.0
  on_inconclusive: retry                 # retry | decline | submit (pre_submit stage)
```

An object whose only key is `$ref` is replaced by the content of the referenced file (YAML or
JSON), relative to the file that contains it; it can stand for one metric, a `params` value, a
rubric list or the whole `metrics` list. A list is not spliced into a surrounding list. Only
local files are allowed: remote references are refused, and the eval service rejects `$ref`
altogether (send the suite inline). The `suite_hash` is computed after inlining, so moving a
config into a file does not change it.

### Suite fields

| Field | Meaning |
|---|---|
| `name`, `version` | identify the suite (both are part of `suite_hash`) |
| `metrics` | one or more metric configs (below) |
| `judge` | the pinned judge model for every judge metric (provider, model, temperature, samples, ...) |
| `pass_rule` | how case and suite verdicts are decided (below) |
| `lock` | pinned metric and prompt versions (`bnbagent-eval lint --lock`) |
| `description`, `extensions` | free text and vendor-specific data; both are hashed |

### Metric configs

| Field | Default | Meaning |
|---|---|---|
| `id` | | canonical metric ID or alias (see [METRICS.md](METRICS.md)) |
| `name` | `id` | the key results are reported under; needed to use one metric twice |
| `method` | first method | `rule`, `native`, a vendor name, ... |
| `params` | `{}` | method parameters, validated against the metric's JSON Schema |
| `threshold` | the metric's default | pass threshold on the [0, 1] score |
| `value_min`, `value_max`, `value_target` | | limits on the raw value, honoured only by value metrics (spec `value_limits: true`: `ops.latency`, `ops.cost`, `ops.token_usage`, `onchain.gas_efficiency`); elsewhere they are ignored and lint warns (`value_limits_ignored`) |
| `weight` | `1.0` | weight in `weighted_average` |
| `required` | `false` | the case fails if this metric fails, and the run is inconclusive if it cannot score |
| `allow_truncation` | `false` | allow a required judge metric to truncate long inputs in negotiated stages |

Common judge parameters: `samples` (1 to 15 judge calls, the lower median wins),
`prompt_version`, `domain_rubric` (extra rubric text), `input_mode` and `max_field_chars`.

A metric has one `params` schema for all of its methods. Vendor-specific keys (e.g. `region`
and `agentcore_evaluator_id` for AgentCore, `strict_mode` for DeepEval) sit in the same schema
as the native ones, so a suite validates whatever `method` it picks, and a method ignores the
keys it does not use. Check `bnbagent-eval metrics --json` for a metric's schema.

### Pass rule and verdict

Each metric result gets `passed = score >= threshold`. A case's score combines its scored
metrics with `pass_rule.aggregation`; a case passes when every required metric passed and the
score reaches `min_aggregate`. The suite verdict is decided in this order (the first match wins):

1. a required metric failed in any case: **fail**;
2. a required metric could not score, the share of unscored results exceeds `max_error_rate`,
   or the deadline or budget stopped the run: **inconclusive**;
3. a metric waits for human review (`custom.human_review`): **pending_human**;
4. no metric produced a score: **inconclusive**;
5. the aggregate score is within `inconclusive_band` of `min_aggregate`: **inconclusive**;
6. aggregate score >= `min_aggregate` and case pass rate >= `min_pass_rate`: **pass**;
7. otherwise: **fail**, with the failing condition in `verdict.reasons`.

### Stages and actions

`EvalRequest.stage` says where the evaluation runs; it maps the verdict to an action:

| Stage | Used by | pass | fail | inconclusive / pending_human |
|---|---|---|---|---|
| `dev` | local development, CI | - | - | - |
| `registration` | ERC-8004 publication | `publishable` | `not_publishable` | `not_publishable` |
| `pre_submit` | provider gate (ERC-8183) | `submit` | `retry_or_decline` | from `on_inconclusive` |
| `review` | client advice | `no_action` | `dispute` | `no_action` |
| `vote` | voter advice | `abstain` | `vote_reject` | `abstain` |

`on_inconclusive` maps to `retry_or_decline` (`retry`), `decline` or `submit`.

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, Suite
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types.case import AppMetrics

suite = Suite.load("suite.yaml")
assert [m.key for m in suite.metrics][:3] == ["text.exact_match", "text.token_f1", "json_ok"]

# A FakeJudge with the pinned config (provider "fake", samples 3) stands in for the model.
judge = FakeJudge({"reasoning": "Clear.", "score": "Excellent"}, samples=3)
case = EvalCase(
    id="day-1",
    output='{"price": "612.40", "trend": "up"}',
    expected='{"price": "612.40", "trend": "up"}',
    app_metrics=AppMetrics(latency_ms=1500),
)
response = EvalClient(judge=judge).evaluate_sync(stage="pre_submit", suite=suite, cases=[case])
print(response.verdict.status, response.verdict.action)  # pass submit
assert response.verdict.action == "submit"
assert response.cost.judge_calls == 3  # samples: 3
```

## Lint

`bnbagent-eval lint` checks that a suite is fit for its use. It never changes the suite.

```bash
bnbagent-eval lint suite.yaml                    # --use negotiated (default)
bnbagent-eval lint suite.yaml --use service      # also: methods the eval service refuses
bnbagent-eval lint suite.yaml --use dev --json
```

Exit code 1 means at least one error. Uses: `dev` (local work), `negotiated` (ERC-8183 jobs and
ERC-8004 registration: the suite is evidence in a dispute) and `service`.

| Code | Level | Finding |
|---|---|---|
| `unresolvable` | error | unknown metric, bad params, missing extra or lock mismatch |
| `judge_not_pinned` | error (negotiated) | judge metrics without `suite.judge` |
| `required_without_threshold` | error (negotiated) | a required metric has no threshold or (on a value metric) value limits |
| `not_service_allowed` | error (service) | the method may not run on the eval service |
| `few_samples` | warning | a judge metric with fewer than 3 samples |
| `judge_temperature` | warning | the pinned judge temperature is not 0 |
| `ambiguous_criterion` | warning | a vague or very short rubric item |
| `no_deterministic_metric` | warning | no rule metric (rule metrics replay exactly) |
| `no_required_metric` | warning | nothing is marked `required` |
| `value_limits_ignored` | warning | `value_min` / `value_max` / `value_target` on a metric that does not score a value |
| `unlocked` | warning | no `lock` |
| `no_inconclusive_band` | info | judge metrics with `inconclusive_band: 0` |

## Lock

A lock pins the metric and prompt versions installed when the suite was written. A suite with a
lock fails to resolve when a metric or prompt changed, so every party evaluates the same thing.
Entries are keyed by metric id; a metric id used more than once (e.g. with two methods or
prompts, each with its own `name`) gets one entry per use, keyed by that `name`.

```bash
bnbagent-eval lint suite.yaml --lock suite.locked.yaml
```

<!-- doctest -->
```python
from bnbagent_eval import Suite
from bnbagent_eval.authoring.lint import lint_suite, lock_suite
from bnbagent_eval.engine.resolve import resolve_suite

suite = Suite.load("suite.yaml")
print([i.code for i in lint_suite(suite)])  # ['unlocked']
locked = lock_suite(suite)
print(locked.lock["quality.clarity"])  # LockEntry(metric_version='1', prompt_hash='0x...')
assert [i.code for i in lint_suite(locked)] == []
# A lock changes the suite file, not the suite_hash (the hash always covers the versions).
assert resolve_suite(locked).suite_hash == resolve_suite(suite).suite_hash
```

## Eval maker

`bnbagent-eval make` drafts a lint-clean, locked suite from a task description. It needs a judge
(the `EVAL_JUDGE_*` environment variables, or `--judge module:factory`):

```bash
bnbagent-eval make "Summarise BNB Chain governance proposals in under 200 words, \
  citing the proposal number" --samples cases.jsonl --stage pre_submit -o suite.yaml
bnbagent-eval make @task.txt --json          # description from a file; full result as JSON
```

The maker assesses the description (it refuses vague input), plans one evaluation dimension per
requirement, matches catalogue metrics (unknown IDs are repaired or dropped), writes
`custom.rubric` / `custom.requirements_compliance` / `custom.judge_prompt` configs for what the
catalogue does not cover, reflects on lint findings up to `--max-reflections` times, then locks
the suite. For negotiated stages it pins the judge at temperature 0 with 3 samples, sets an
inconclusive band and prefers rule methods. It prints a rationale per metric and the remaining
lint warnings. From Python:

```python
from bnbagent_eval.authoring.eval_maker import make_suite

result = await make_suite(description, judge=judge, sample_cases=cases, stage="pre_submit")
result.suite, result.rationale, result.lint
```

To see which metrics a dataset can support without a judge, use
`bnbagent_eval.authoring.recommend.recommend(cases, stage=...)`: it checks the inputs present in
the cases, installed extras and data shape, and returns a ranked list ready for a suite.

<!-- doctest -->
```python
from bnbagent_eval import EvalCase
from bnbagent_eval.authoring.recommend import recommend

cases = [EvalCase(id="1", input="2+2?", output="4", expected="4")]
rec = recommend(cases, judge=False)
print([(m.config.id, m.config.method) for m in rec.metrics][:2])
# [('quality.answer_correctness', 'rule'), ('text.bleu', None)]  (None: the default method)
assert all(m.kind == "rule" for m in rec.metrics)  # judge=False: rule methods only
suite = rec.suite(name="from-recommendation")
```
