# Judges

Judge metrics (method kind `judge`) ask an LLM to grade a case with a fixed prompt and a JSON
output schema. This guide covers how a judge is chosen and configured, what the judge layer does
for reproducibility, and how to check that a judge agrees with human labels.

## Providers

| `provider` | Class | Notes |
|---|---|---|
| `openai` | `OpenAICompatJudge` | any OpenAI-compatible chat-completions API: OpenAI, vLLM, Ollama, OpenRouter, ... (set `base_url`) |
| `anthropic` | `AnthropicJudge` | the Anthropic Messages API |
| `bedrock` | `BedrockJudge` | Amazon Bedrock (Converse); `[bedrock]` extra, AWS credential chain, `region` or `AWS_REGION` |
| `fake` | `FakeJudge` | scripted replies for tests; pass an instance, it cannot be built from config |

A judge is described by a `JudgeConfig`. API keys are never part of it; they come from the
environment.

| Field | Default | Meaning |
|---|---|---|
| `provider`, `model` | | as above; `model` is the provider's model ID |
| `temperature` | `0.0` | `None` never sends the parameter; models that reject it are retried without it |
| `max_tokens` | `2048` | output tokens per call |
| `samples` | `1` | calls per metric and case (a metric's `params.samples` overrides it) |
| `base_url` | provider default | OpenAI-compatible endpoint |
| `region` | | Bedrock region |
| `extra` | `{}` | `max_in_flight` (8), `timeout_s` (120), `retries` (3), `response_format` (`json_schema`, `json_object` or `none`, OpenAI only), `embedding_model` |

Other providers can be added with
`bnbagent_eval.judges.providers.register_provider(name, factory)`, where
`factory(JudgeConfig)` returns an object with `config`, `async complete(JudgeRequest)` and
`async embed(texts)`.

## Environment

`bnbagent_eval.config.EvalConfig` reads:

| Variable | Meaning |
|---|---|
| `EVAL_JUDGE_PROVIDER` | default judge provider (`openai`, `anthropic`, `bedrock`) |
| `EVAL_JUDGE_MODEL` | judge model; default per provider: Bedrock `global.anthropic.claude-sonnet-5-5`, Anthropic `claude-sonnet-5-5` (OpenAI-compatible endpoints must name one) |
| `EVAL_JUDGE_EMBEDDING_MODEL` | embedding model (STED, embedding similarity, RAGAS); default Bedrock `amazon.titan-embed-text-v2:0` (Titan-format models), OpenAI `text-embedding-3-small` |
| `EVAL_JUDGE_REGION` | Bedrock region (else `AWS_REGION` / the credential chain) |
| `EVAL_JUDGE_TEMPERATURE`, `EVAL_JUDGE_MAX_TOKENS` | sampling settings (defaults 0 and 2048) |
| `EVAL_JUDGE_BASE_URL` | default `base_url` |
| `EVAL_JUDGE_API_KEY` | API key; falls back to `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| `EVAL_JUDGE_CACHE` | judge response cache: `memory` or `sqlite:///path/to/judge.db` |
| `EVAL_MAX_CONCURRENCY` | concurrent metric evaluations (default 8) |
| `EVAL_TIMEOUT_S` | engine timeout in seconds (default 120) |
| `AWS_REGION` | Bedrock region when `region` is not set |

Empty values count as unset. Keep keys in a git-ignored `.env`, never in a suite.

The same settings (without keys) can live in a YAML file named by `EVAL_CONFIG` or
`bnbagent-eval --config FILE`; environment variables override it:

```yaml
judge:
  provider: bedrock
  model: global.anthropic.claude-sonnet-5-5
  embedding_model: amazon.titan-embed-text-v2:0
  region: us-east-1
  temperature: 0
  max_tokens: 2048
  cache: sqlite:///.eval-cache/judge.db
max_concurrency: 8
timeout_s: 120
```

## Which judge runs

1. the suite's pinned `judge` (negotiated suites must pin one);
2. `options.judge_override`, only when the suite pins none (a different override is an error);
3. the judge passed to `EvalClient(judge=...)`;
4. the `EVAL_JUDGE_*` environment variables, then the config file (`EVAL_CONFIG`), then the
   per-provider default model.

A suite with judge metrics and no judge from any of these fails before any call is made. When
the pinned config equals the client judge's `config`, the client's instance is used; this is how
tests pin `provider: fake`.

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, JudgeConfig, MetricConfig, Suite
from bnbagent_eval.judges.providers.fake import FakeJudge

judge = FakeJudge({"reasoning": "Answers the question.", "score": "Excellent"}, samples=3)
suite = Suite(
    name="pinned",
    version="1",
    metrics=(MetricConfig(id="quality.relevance"),),
    judge=JudgeConfig(provider="fake", model="fake", samples=3),
)
response = EvalClient(judge=judge).evaluate_sync(
    suite=suite, cases=[EvalCase(id="1", input="Gas token?", output="BNB")]
)
print(response.provenance.judge)  # {'provider': 'fake', 'model': 'fake', ...}
assert len(judge.requests) == 3 and response.cost.judge_calls == 3
```

## What the judge layer does

- **Fencing.** Case content is wrapped in delimiters with a random nonce, and the prompt tells
  the judge to treat it as data, so an agent output cannot rewrite the instructions.
- **Structured output.** Replies are parsed against the template's JSON schema; an unparseable
  reply gets one repair re-ask, then the metric reports `judge_parse_error`.
- **Samples.** With `samples: k` the judge is called k times; the score is the lower median and
  labels are decided by majority (ties go to the lower score). Use at least 3 in negotiated
  suites (lint warns otherwise).
- **Retries and limits.** Throttling and 5xx errors are retried with backoff (`retries`);
  context-overflow errors are not. `max_in_flight` caps concurrent calls per provider.
- **Truncation.** Inputs longer than `max_field_chars` (default 24 000) keep their head (70 %)
  and tail (30 %). In `pre_submit`, `review` and `vote`, a required metric refuses to truncate
  (status `skipped`, code `truncated`) unless `allow_truncation: true`.
- **Provenance.** Each result records the metric version, method, prompt hash, judge config and
  sample count. Prompt templates are versioned and never edited in place; `prompt_version`
  selects an older one, and a suite lock pins the prompt hash.

## Cache

`EVAL_JUDGE_CACHE=sqlite:///.eval-cache/judge.db` stores judge replies. The key covers the
provider, model, parameters, template hash, the unfenced inputs and the sample index (not the
fence nonce), so a replay of the same evaluation reads from the cache. Cached results report
`provenance.judge_source = "cache:<id>"`; this field is excluded from `result_hash`, so cached
and live runs hash the same. Do not share a cache with parties you do not trust.

## Budgets and deadlines

`RunOptions` limits what a run may spend:

<!-- doctest -->
```python
from bnbagent_eval import Budget, EvalCase, EvalClient, MetricConfig, RunOptions, Suite
from bnbagent_eval.judges.providers.fake import FakeJudge

suite = Suite(name="budget", version="1", metrics=(MetricConfig(id="quality.clarity"),))
cases = [EvalCase(id=str(i), output=f"answer {i}") for i in range(3)]
options = RunOptions(budget=Budget(max_judge_calls=2, max_usd=0.50), max_concurrency=1)
client = EvalClient(judge=FakeJudge({"reasoning": "ok", "score": "Good"}))
response = client.evaluate_sync(suite=suite, cases=cases, options=options)
codes = [r.metrics[0].error.code for r in response.results if r.metrics[0].error]
print(response.verdict.status, codes)  # inconclusive ['budget_exhausted']
assert response.cost.judge_calls == 2 and response.verdict.status == "inconclusive"
```

A call that would exceed `max_judge_calls`, `max_tokens` or `max_usd` is not made; the metric is
skipped with `budget_exhausted` and the verdict is `inconclusive`. `options.deadline` (unix
seconds) works the same way with the code `deadline`; the ERC-8183 gate sets it from the job
expiry. `response.cost` reports judge calls, tokens and the USD estimate.

## Calibration

Before a judge decides disputes, measure its agreement with human labels:

```bash
bnbagent-eval calibrate --suite suite.yaml --cases labelled.jsonl --min-kappa 0.6
bnbagent-eval calibrate --suite suite.yaml --cases cases.jsonl --labels labels.csv --json
bnbagent-eval calibrate --suite suite.yaml --cases cases.jsonl --inject --seed 7
```

The command evaluates the suite's judge metrics on the cases and compares each metric's scores
with the reference labels. It prints, per metric, the number of compared cases, the score type
(binary or ordinal), Cohen's kappa and MAE. The gate statistic is Cohen's kappa, computed on
pass / fail decisions when the labels are pass / fail; the command exits 1 if any metric is
below `--min-kappa` (default 0.4), has no labelled case or had judge errors.

- **Labels** come from `--labels` (JSON / YAML mapping `case_id -> label` or
  `case_id -> {metric: label}`, or rows with `case_id`, optional `metric` and `label` as JSON,
  JSONL or CSV), else from case metadata: `labels`, `human_label` or `label`. A label is a number
  in [0, 1], a boolean, `pass` / `fail`, `yes` / `no`, or a 5-tier name (`Poor` ... `Excellent`).
- **`--inject`** builds a balanced dataset without human labels: per judge metric, half of the
  cases are kept (`label: pass`) and half get one injected fault (`label: fail`); `--fault`
  restricts the fault types. See [GENERATION.md](GENERATION.md#error-injection).
- **`--judge module:factory`** calibrates a candidate judge instead of the suite's pinned one;
  `--prices` (a mapping `model -> {input_per_1k, output_per_1k}`) adds a cost estimate.

The same from Python, with `benchmark_agreement` (MAE, Cohen's kappa, weighted kappa for ordinal
scores, MCC for binary scores, accuracy, Spearman, fail precision / recall / F1):

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.analysis.calibration import benchmark_agreement
from bnbagent_eval.generation.error_injection import make_balanced_dataset
from bnbagent_eval.judges.providers.fake import FakeJudge

cases = [
    EvalCase(
        id=f"c{i}",
        input="Summarise the transfer.",
        output=f"The agent sent {i + 1} BNB to the user. The transaction is confirmed.",
    )
    for i in range(8)
]
dataset = make_balanced_dataset(cases, evaluators=["quality.completeness"], seed=0)
suite = Suite(name="cal", version="1", metrics=(MetricConfig(id="quality.completeness"),))

# A judge that approves everything: it never detects the injected faults.
lenient = FakeJudge({"reasoning": "Looks complete.", "score": "Excellent"})
response = EvalClient(judge=lenient).evaluate_sync(suite=suite, cases=dataset.cases)
candidate = {r.case_id: r.metrics[0].score for r in response.results}
report = benchmark_agreement(dataset.labels(), candidate)
print(report.score_type, report.n_compared, report.cohens_kappa, report.fail_recall)
assert report.n_compared == 8 and report.fail_recall == 0.0
```
