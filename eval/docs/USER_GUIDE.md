# bnbagent-eval user guide

`bnbagent-eval` measures **how well an AI agent does its job**, with numbers you can reproduce
and, if you want, prove on BNB Chain. You give it examples of the agent's work (or let it call
the agent), choose what to measure, and get scores, a pass / fail verdict and a report.

Why BNB Chain agents need it, and what it gives: [OVERVIEW.md](OVERVIEW.md).

Contents:

1. [Concepts](#1-concepts)
2. [What it can measure](#2-what-it-can-measure)
3. [What it is used for](#3-what-it-is-used-for)
4. [Install](#4-install)
5. [Five-minute demo (command line)](#5-five-minute-demo-command-line)
6. [Cases](#6-cases)
7. [Python API](#7-python-api)
8. [Metrics that use an LLM judge](#8-metrics-that-use-an-llm-judge)
9. [Evaluating a live agent](#9-evaluating-a-live-agent)
10. [Tool use and transactions](#10-tool-use-and-transactions)
11. [Reading the result](#11-reading-the-result)
12. [On BNB Chain: publish, gate, sell](#12-on-bnb-chain-publish-gate-sell)
13. [Commands](#13-commands)
14. [Troubleshooting](#14-troubleshooting)
15. [Other guides](#15-other-guides)

## 1. Concepts

- A **case** is one example of the agent's work: the task (`input`), the agent's answer
  (`output`), and optionally the reference answer (`expected`), retrieved documents
  (`contexts`), a conversation, the agent's tool calls and transactions (`trajectory`), and
  measured latency or cost.
- A **suite** is the checklist: which metrics to apply, which must pass, and the overall pass
  rule. It is a small YAML file that can be versioned and shared ([SUITES.md](SUITES.md)).
- A **metric** turns one case into a score from 0 (worst) to 1 (best). Some metrics are rules
  (exact match, valid JSON, latency under 2 s), some ask an LLM judge (is the answer helpful?
  faithful to the documents?), and some check on-chain behaviour (did the transaction succeed?
  was the token approval safe?).

One call evaluates a suite on a set of cases and returns **one result**: a verdict, scores per
case and per metric, and a `result_hash` that anyone can recompute to check that the result was
not altered.

Sections 1–11 need neither BNB Chain nor the bnbagent SDK. For use without BNB (any agent, any
EVM chain or none), see also [STANDALONE.md](STANDALONE.md).

## 2. What it can measure

99 metrics in 12 categories. [METRICS.md](METRICS.md) describes each one (inputs, methods,
parameters, default threshold); `bnbagent-eval metrics` lists them in the terminal.

| Category | Count | Measures | Examples |
|---|---|---|---|
| `text` | 13 | answer vs reference answer | `exact_match`, `contains`, `token_f1`, `rouge`, `bleu`, `bertscore`, `length` |
| `format` | 6 | the shape of the output | `json_valid`, `json_schema`, `regex`, `xml`, `artifact_integrity` |
| `ops` | 5 | operations | `latency`, `cost`, `token_usage`, `reliability`, `consistency` across repeated runs |
| `quality` | 13 | how good the answer is | `relevance`, `helpfulness`, `clarity`, `completeness`, `instruction_following`, `answer_correctness` |
| `safety` | 9 | harmful or risky content | `toxicity`, `bias`, `harmfulness`, `pii_leakage`, `refusal`, `misuse` |
| `rag` | 7 | answers from retrieved documents | `faithfulness`, `groundedness`, `hallucination`, `context_precision`, `context_recall` |
| `conversation` | 12 | multi-turn chats | `coherence`, `knowledge_retention`, `role_adherence`, `topic_adherence`, `failure_modes` |
| `agent` | 18 | tool use and reasoning | `tool_trajectory_match`, `tool_selection`, `tool_parameter_accuracy`, `goal_success`, `plan_quality`, `confidence` |
| `onchain` | 6 | blockchain actions | `tx_success`, `gas_efficiency`, `approval_safety`, `spend_policy`, `balance_delta`, `intent_match` |
| `redteam` | 3 | resistance to attacks | `attack_resistance`, `prompt_injection`, `turns_to_break` |
| `code` | 2 | generated code | `syntax_valid`, `tests_pass` (sandboxed) |
| `custom` | 5 | your own criteria | `rubric`, `requirements_compliance`, `judge_prompt`, `python`, `human_review` |

Many metrics offer several **methods**, i.e. ways to compute them: a `rule` method is
deterministic, a `judge` method (often named `native`) asks an LLM, a `model` method runs a local
model. All of these are implemented in this package. Optional **third-party** methods (kind
`vendor`) hand the case to RAGAS, DeepEval, FMEval or AWS AgentCore instead; they are never the
default, and their metric names work as aliases, e.g. `ragas.faithfulness` or `deepeval.bias`
([THIRD_PARTY.md](THIRD_PARTY.md)).

## 3. What it is used for

| Use | You want to | How |
|---|---|---|
| **Development and CI** | catch regressions before release | run a suite on a fixed set of cases; fail the build on `fail` |
| **Comparing agents or prompts** | pick the better version | run the same suite on both; `EvalClient.compare(a, b)` |
| **S1: ERC-8004 reputation** | show verifiable quality metrics in the agent's on-chain registration | evaluate at stage `registration`, then `bnbagent-eval publish` |
| **S2: ERC-8183 jobs** | check a deliverable before submitting it; decide whether to dispute or how to vote | `EvalGate` / `bnbagent-eval watch` for providers, `bnbagent-eval review` for clients and voters |
| **S3: evaluation as a service** | offer or buy evaluations over HTTP, paid per request with x402 | `bnbagent-eval serve`; `EvalClient.remote(url)` |

## 4. Install

Python 3.10 or later.

```bash
pip install bnbagent-eval                       # core: rule metrics, CLI, reports
pip install 'bnbagent-eval[format]'             # JSON Schema / XML / safe-regex metrics
pip install 'bnbagent-eval[bnbagent]'           # ERC-8004 / ERC-8183 through the bnbagent SDK
pip install 'bnbagent-eval[all]'                # bnbagent, bedrock, format, server, crypto
pip install 'bnbagent-eval[standalone]'         # evm, bedrock, format, server, crypto: no SDK, no web3
```

From a checkout of the repository, install the package directory: `pip install -e 'eval[format]'`.

The core depends only on `httpx`, `pyyaml` and `eth-hash`. Optional features are extras:

| Extra | Adds | Needed for |
|---|---|---|
| `format` | jsonschema, lxml, regex | `format.json_schema`, `format.xml`, `format.regex` |
| `bedrock` | boto3 | the Bedrock judge provider (OpenAI-compatible and Anthropic judges need no extra) |
| `evm` | eth-account, eth-abi | signing and verifying attestations, calldata decoding, x402 payments |
| `bnbagent` | the bnbagent SDK, `evm` | ERC-8004 / ERC-8183 workflows on BNB Chain |
| `server` | fastapi, uvicorn, regex, lxml | `bnbagent-eval serve` (the HTTP eval service) |
| `crypto` | pynacl | encrypted reports |
| `exporters` | OpenTelemetry, Prometheus client, MLflow | telemetry exporters ([REPORTING.md](REPORTING.md)) |
| `semantic`, `embeddings`, `toxicity`, `fluency` | transformers + torch, gensim, Detoxify, LanguageTool | `model` methods: BERTScore's encoder, word vectors, toxicity, grammar |
| `ragas`, `deepeval`, `fmeval`, `agentcore` | the provider's library | third-party methods ([THIRD_PARTY.md](THIRD_PARTY.md)) |
| `redteam` | PyRIT | the PyRIT red-teaming backend ([GENERATION.md](GENERATION.md#red-teaming)) |

`import bnbagent_eval` never imports an extra, the SDK or web3, and makes no network call. A
metric whose extra is missing fails when the suite is resolved, before any work starts, with
the `pip install` command to run. `bnbagent-eval metrics --third-party` lists the third-party
methods and their install commands.

## 5. Five-minute demo (command line)

Make a folder with two files. `suite.yaml` says what to check:

<!-- doctest-file: suite.yaml -->
```yaml
name: quickstart
version: "1"
metrics:
  - id: text.contains            # the answer mentions the expected fact
    required: true               # if this fails in any case, the evaluation fails
  - id: text.token_f1            # word overlap with the reference answer
  - id: ops.latency              # response time; score 0 above 2 s
    value_max: 2000
pass_rule:
  min_aggregate: 0.6             # the mean case score must reach 0.6
```

`cases.jsonl` holds one example per line:

<!-- doctest-file: cases.jsonl -->
```json
{"id": "fees", "input": "What are BNB Smart Chain gas fees paid in?", "output": "Gas fees on BNB Smart Chain are paid in BNB.", "expected": "BNB", "app_metrics": {"latency_ms": 820}}
{"id": "blocks", "input": "How often are blocks produced?", "output": "Roughly every 3 seconds.", "expected": "3 seconds", "app_metrics": {"latency_ms": 640}}
{"id": "approve", "input": "What does approve do?", "output": "It sends your tokens to the spender.", "expected": "allowance", "app_metrics": {"latency_ms": 1900}}
```

Run it:

```bash
bnbagent-eval run --suite suite.yaml --cases cases.jsonl --format console
```

```
verdict: FAIL
score: 0.652  pass rate: 0.667  error rate: 0.000  cases: 3  stage: dev
  - required metric failed: text.contains

metric         mean   pass   scored  err  skip  p50      p95
-------------  -----  -----  ------  ---  ----  -------  --------
text.contains  0.667  0.667  3/3     0    0     -        -
text.token_f1  0.289  0.333  3/3     0    0     -        -
ops.latency    1.000  1.000  3/3     0    0     820.000  1792.000

case     score  passed  notes
-------  -----  ------  -----
fees     0.733  yes
blocks   0.889  yes
approve  0.333  no

result_hash: 0x…
suite_hash:  0x…
```

The `approve` answer is wrong: it never mentions an "allowance". Since `text.contains` is
required, one failing case fails the run, even though the mean score (0.652) is above
`min_aggregate`. Fix that answer and the verdict becomes `PASS`.

The exit code follows the verdict, so the command works as a CI gate: `0` pass, `1` fail, `2`
inconclusive or pending human review, `64` usage or input error. The same call from Python:

<!-- doctest -->
```python
from bnbagent_eval.cli import main

code = main(["run", "--suite", "suite.yaml", "--cases", "cases.jsonl", "--out", "report.json"])
assert code == 1  # "approve" fails the required text.contains
```

Options of `run`: `--format json` (the default: the full response), `md` (Markdown, e.g. for PR
comments) or `console`; `--out FILE` writes the report to a file; `--csv FILE` also writes a
CSV; `--stage` and `--subject` set the stage and the agent or job evaluated
([ONCHAIN.md](ONCHAIN.md)); `--diagnose FILE` writes failure root causes and recommendations
when the verdict is not `pass`. These demo files are also in `examples/quickstart/` of the
package source.

## 6. Cases

Every case field except `id` is optional; each metric declares the inputs it needs
(`text.exact_match` needs `output` and `expected`). Cases files can be JSON (a list, or an
object with `cases`), JSONL or YAML.

| Field | Meaning |
|---|---|
| `input` | the task or question (text, a JSON object, or content parts) |
| `output` | what the agent produced |
| `expected` | the reference answer (any JSON value; a list of tool calls for trajectory metrics) |
| `contexts` | retrieval contexts, `[{"text": ..., "source": ...}]` (RAG metrics) |
| `conversation` | messages, `[{"role": "user", "content": ...}]` (conversation metrics) |
| `trajectory` | the agent's trace: a session with LLM, tool, agent and transaction steps ([§10](#10-tool-use-and-transactions)) |
| `app_metrics` | measured latency, tokens and cost (`ops.*` metrics) |
| `artifacts` | non-text deliverables by URI and hash |
| `metadata` | free-form; also holds human labels for calibration |

`bnbagent-eval metrics --json` shows each metric's required and optional inputs.

## 7. Python API

`EvalClient.evaluate` is the one evaluation call. It is async; `evaluate_sync` is the blocking
variant. Pass an `EvalRequest`, or its fields as keywords (nested values may be objects or plain
dicts):

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, PassRule, Suite

suite = Suite(
    name="support-answers",
    version="1",
    metrics=(
        MetricConfig(id="text.contains", required=True),
        MetricConfig(id="text.token_f1", threshold=0.3),
    ),
    pass_rule=PassRule(min_aggregate=0.6),
)
cases = [
    EvalCase(id="fees", output="Gas fees are paid in BNB.", expected="BNB"),
    EvalCase(id="blocks", output="Roughly every 3 seconds.", expected="3 seconds"),
]
response = EvalClient().evaluate_sync(suite=suite, cases=cases)
print(response.verdict.status, round(response.aggregate.score, 3))  # pass 0.738
assert response.verdict.status == "pass"
```

Suites and cases can be loaded from files, here the demo files of §5:

<!-- doctest -->
```python
import asyncio

from bnbagent_eval import EvalClient, EvalRequest, Suite
from bnbagent_eval.engine.dataset import load_cases


async def main() -> None:
    request = EvalRequest(
        stage="dev", suite=Suite.load("suite.yaml"), cases=load_cases("cases.jsonl")
    )
    response = await EvalClient().evaluate(request)
    print(response.verdict.status, response.verdict.reasons)
    # fail ('required metric failed: text.contains',)
    assert response.verdict.status == "fail"


asyncio.run(main())
```

Instead of inline values, a request can reference them:

- `dataset_ref`: a local cases file, `path` or `path#0x<keccak of the file bytes>` (the eval
  service also fetches public http(s) URLs);
- `suite_ref`: `<uri>#<suite_hash>`, downloaded through the storage port
  (`EvalClient(ports=ChainPorts(storage=...))`); the suite may not use `$ref`, and the
  `#<suite_hash>` fragment, when present, must match the resolved suite;
- `benchmark_ref` is reserved: the local engine and the service reject it.

## 8. Metrics that use an LLM judge

Judge metrics (most of `quality`, `safety`, `rag`, `conversation` and `agent`) need an LLM, and a
few (STED consistency, embedding similarity) need an embedding model. Choose a provider; model
ids have defaults per provider:

```bash
# AWS Bedrock (the AWS credential chain). Defaults: judge global.anthropic.claude-sonnet-5-5,
# embeddings amazon.titan-embed-text-v2:0
export EVAL_JUDGE_PROVIDER=bedrock
export AWS_REGION=us-east-1
export EVAL_JUDGE_MODEL=global.anthropic.claude-haiku-5-5   # optional: another model id

# or any OpenAI-compatible API (OpenAI, vLLM, Ollama, OpenRouter, ...): name the model
export EVAL_JUDGE_PROVIDER=openai
export EVAL_JUDGE_MODEL=<model id>
export EVAL_JUDGE_API_KEY=...            # EVAL_JUDGE_BASE_URL for a non-OpenAI endpoint
```

The same settings, without keys, can live in a YAML file passed with `bnbagent-eval --config
FILE` or `EVAL_CONFIG`; environment variables override the file. A suite can pin its judge
(`judge: {provider: ..., model: ...}`) so everyone who runs it uses the same model; in Python,
pass `EvalClient(judge=JudgeConfig(...))` or a judge instance. All variables, the config file
and the precedence rules are in [JUDGES.md](JUDGES.md).

Judge metrics are then configured like any other:

```yaml
name: answers-quality
version: "1"
metrics:
  - id: quality.relevance
  - id: quality.helpfulness
  - id: rag.faithfulness          # needs "contexts" in the cases
    required: true
```

The judge sees each case inside a fenced block, so text in an answer cannot instruct it. Each
score comes with the judge's `reason`. With `EVAL_JUDGE_CACHE=sqlite:///.eval-cache/judge.db`,
re-runs read from the cache and return identical results. To try things out offline, or in
tests, use a scripted judge:

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.judges.providers.fake import FakeJudge

judge = FakeJudge({"reasoning": "Answers the question directly.", "score": "Good"})
suite = Suite(name="clarity", version="1", metrics=(MetricConfig(id="quality.clarity"),))
response = EvalClient(judge=judge).evaluate_sync(
    suite=suite, cases=[EvalCase(id="1", input="What is BNB?", output="BNB Chain's native token.")]
)
metric = response.results[0].metrics[0]
print(metric.score, metric.reason)  # 0.75 Answers the question directly.
assert metric.score == 0.75 and len(judge.requests) == 1
```

Judges use a 5-tier scale (Excellent 1.0, Good 0.75, Average 0.5, Below Average 0.25, Poor 0.0)
unless a metric says otherwise. Before relying on a judge, measure its agreement with your own
labels with `bnbagent-eval calibrate` ([JUDGES.md](JUDGES.md#calibration)).

Not sure which metrics to use? `bnbagent-eval make "description of what your agent does"`
drafts a suite with a judge, and `bnbagent_eval.authoring.recommend.recommend(cases)` lists the
metrics your data supports ([SUITES.md](SUITES.md#eval-maker)).

## 9. Evaluating a live agent

Instead of writing `output` into the cases, let `bnbagent-eval` call the agent: each case's
`input` is sent, the reply becomes the output, and latency is measured.

```bash
# an HTTP agent that accepts POST {"input": ...} and replies {"output": ...}
bnbagent-eval run --suite suite.yaml --cases questions.jsonl \
  --target https://my-agent.example.com/chat --target-header "Authorization: Bearer $TOKEN"

# other kinds: an A2A agent, one MCP tool, or a Python function
bnbagent-eval run ... --target a2a:https://my-agent.example.com/.well-known/agent-card.json
bnbagent-eval run ... --target mcp:ask@https://my-agent.example.com/mcp
bnbagent-eval run ... --target callable:my_package.agent:answer --runs 3
```

`--runs 3` calls the agent three times per case. That feeds `ops.consistency` (are the answers
stable?) and the latency percentiles. The metric's `method` sets how runs are compared:

| `method` | Compares runs by | Needs |
|---|---|---|
| `exact` | identical text (after whitespace normalization) | – |
| `rouge` | ROUGE-L word overlap | – |
| `ted` | tree edit distance of JSON outputs, values compared exactly | – |
| `sted` | STED, semantic tree edit distance ([paper](https://arxiv.org/abs/2512.23712)): JSON structure must match, values are compared by meaning (`"NYC"` ≈ `"New York"`) | a judge that can embed |
| `judge` | an LLM decides whether two answers are equivalent | a judge |
| `embedding` | cosine similarity of the judge's embeddings | a judge that can embed |

```yaml
metrics:
  - id: ops.consistency
    method: sted                 # score = validity rate × mean pairwise STED similarity
    params: {score: c_adj}       # or c_mean, consistency_coefficient, stability_score, ranking_score
```

STED embeds the string values and keys once per case with the judge's embedding model
(`EVAL_JUDGE_EMBEDDING_MODEL`). Its other parameters: `structural_weight` (0–1, default 0.5),
`exact_match_fields` (fields compared exactly, e.g. tool names), `order_sensitive_fields`
(arrays whose order matters, e.g. steps) and `exact_match_all_keys`.

In Python, pass `target=` to `evaluate`:

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.engine.targets import register_target
from bnbagent_eval.types import TargetRef


def my_agent(question: str) -> str:  # stands in for your agent
    return "BNB" if "token" in question else "I don't know."


register_target("my_agent", my_agent)
suite = Suite(name="live", version="1", metrics=(MetricConfig(id="text.exact_match"),))
response = EvalClient().evaluate_sync(
    suite=suite,
    cases=[EvalCase(id="1", input="What is the native token?", expected="BNB")],
    target=TargetRef(kind="callable", uri="my_agent"),
)
print(response.verdict.status)  # pass
assert response.verdict.status == "pass"
```

## 10. Tool use and transactions

For agents that call tools or send transactions, a case carries a **trajectory**: the steps the
agent took. Traces from OpenTelemetry GenAI, Langfuse, LangSmith, AWS AgentCore (ADOT),
smolagents and other formats convert into a trajectory with
`bnbagent_eval.adapters.traces.load_trace` (the format is detected). Or build one directly:

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.types import Session, ToolCall, ToolStep, Trace


def trajectory(*calls):
    steps = tuple(
        ToolStep(id=str(i), call=ToolCall(name=n, arguments=a)) for i, (n, a) in enumerate(calls)
    )
    return Session(id="s", traces=(Trace(id="t", steps=steps),))


expected = [
    {"name": "get_balance", "arguments": {"token": "BNB"}},
    {"name": "transfer", "arguments": {"token": "BNB", "amount": "0.5"}},
]
good = EvalCase(id="good", expected=expected, trajectory=trajectory(
    ("get_balance", {"token": "BNB"}), ("transfer", {"token": "BNB", "amount": "0.5"})))
bad = EvalCase(id="bad", expected=expected, trajectory=trajectory(
    ("transfer", {"token": "BNB", "amount": "5"})))  # skipped the balance check, wrong amount

suite = Suite(
    name="tools",
    version="1",
    metrics=(MetricConfig(id="agent.tool_trajectory_match", params={"match_type": "in_order"}),),
)
response = EvalClient().evaluate_sync(suite=suite, cases=[good, bad])
print([(r.case_id, r.metrics[0].score) for r in response.results])  # [('good', 1.0), ('bad', 0.0)]
assert [r.metrics[0].score for r in response.results] == [1.0, 0.0]
```

Transaction steps (`TxStep`) feed the `onchain.*` metrics: `tx_success` and `gas_efficiency`
read receipts when a chain reader is configured, `approval_safety` flags unlimited token
approvals, `spend_policy` checks spending limits, `balance_delta` checks balance changes, and
`intent_match` checks the transaction against what the user asked for. A chain reader for any
EVM node is described in [STANDALONE.md](STANDALONE.md#4-on-chain-metrics-on-any-evm-chain).

## 11. Reading the result

An `EvalResponse` holds:

| Field | Content |
|---|---|
| `verdict.status` | `pass`, `fail`, `inconclusive` (a required metric could not score, too many errors, or a budget or deadline stopped the run) or `pending_human` (waiting for a human score) |
| `verdict.action` | what the stage implies: `publishable` (registration), `submit` (pre_submit), `dispute` (review), `vote_reject` (vote), ... ([SUITES.md](SUITES.md#stages-and-actions)) |
| `verdict.reasons` | why, e.g. `required metric failed: text.contains` |
| `aggregate` | `score` (mean case score), `pass_rate`, `error_rate`, per-metric statistics |
| `results` | one `CaseResult` per case, with one `MetricResult` per metric |
| `cost` | judge calls, tokens and estimated USD |
| `provenance` | package and metric versions, judge, `suite_hash`, `request_hash` |
| `result_hash`, `response_hash` | keccak hashes that make the result verifiable |
| `attestation` | an EIP-712 signature when the client has a signer |

Each `MetricResult` has a `score` in [0, 1] (higher is better), an optional raw `value` with its
`unit` (e.g. latency in ms), `passed` against the threshold, a `status` (`scored`, `skipped`,
`error`, `pending_human`), a `reason`, and an `error.code` when it did not score. A metric that
cannot run is `skipped`, never silently scored:

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite

suite = Suite(name="demo", version="1", metrics=(MetricConfig(id="text.exact_match"),))
response = EvalClient().evaluate_sync(
    suite=suite,
    cases=[EvalCase(id="a", output="4", expected="4"), EvalCase(id="b", output="5")],
)
for case in response.results:
    for m in case.metrics:
        print(case.case_id, m.name, m.status, m.score, m.error.code if m.error else "")
# a text.exact_match scored 1.0
# b text.exact_match skipped None missing_input
print(response.verdict.status, response.verdict.reasons)
assert response.results[1].metrics[0].error.code == "missing_input"
```

The response never contains the cases' inputs or outputs, only hashes of the outputs; the
`--report-contents` mode (`hashes` by default, `redacted`, `full`) controls the case text that
metrics put in their details. Report formats and privacy are in [REPORTING.md](REPORTING.md).

## 12. On BNB Chain: publish, gate, sell

These use the `[bnbagent]` extra and the bnbagent SDK's environment variables (network, RPC,
wallet). [ONCHAIN.md](ONCHAIN.md) describes the flows; [TESTNET.md](TESTNET.md) runs them on BSC
testnet.

**S1: publish metrics to the agent's ERC-8004 registration.**

```bash
bnbagent-eval run --suite suite.yaml --cases cases.jsonl --stage registration \
  --subject agent:97:<registry address>:<agent id> --out result.json
bnbagent-eval publish result.json --agent-id <agent id>
```

Only a passing registration-stage result for that agent is published. It adds an entry such as
`{"overall": 0.92, "latency_p95_ms": 810, ...}` with the suite hash, the result hash and a link
to the full report; publishing the same suite again replaces its entry.

**S2: ERC-8183 jobs.**
- **Client and provider** agree on a suite when the job is created; its URI and hash go in the
  job's success criteria.
- **Provider:** `bnbagent-eval watch --work my_agent.jobs:deliver` (or an `EvalGate` in its own
  watcher) evaluates each funded job's deliverable and submits only if it passes, with signed
  evidence.
- **Client:** `bnbagent-eval review <job id>` re-checks the deliverable and the provider's
  evidence, and advises `dispute` or `no_action`.
- **Voter:** in a dispute, `bnbagent-eval review <job id> --as voter` advises `vote_reject` or
  `abstain`.

**S3: sell or buy evaluations.**

```bash
bnbagent-eval serve --port 8080                                              # free
bnbagent-eval serve --pay-to 0x… --chain-id 97 --facilitator-url https://…   # paid with x402
```

Clients call it with `EvalClient.remote("https://evals.example.com", payer=..., max_payment=...)`:
the service quotes a price, the client pays with x402 and verifies the returned hashes and
signature. The service runs only built-in metrics under a restrictive policy and refuses
private-network targets ([SERVICE.md](SERVICE.md)).

## 13. Commands

| Command | Does |
|---|---|
| `bnbagent-eval metrics [--json \| --third-party \| --markdown]` | list the metrics |
| `bnbagent-eval run --suite S --cases C [--format json\|md\|console] [--out F] [--csv F]` | evaluate |
| `bnbagent-eval run ... --target T [--runs N]` | evaluate a live agent |
| `bnbagent-eval run ... --stage registration --subject agent:…` | evaluate for ERC-8004 publication |
| `bnbagent-eval lint S [--use dev\|negotiated\|service] [--lock OUT]` | check a suite; pin metric and prompt versions |
| `bnbagent-eval make "description" [-o suite.yaml]` | draft a suite with an LLM |
| `bnbagent-eval calibrate --suite S --cases labelled.jsonl` | measure judge agreement with human labels |
| `bnbagent-eval publish result.json --agent-id N` | S1: publish to ERC-8004 |
| `bnbagent-eval review JOB [--as client\|voter] [--act]` | S2: advise on a job |
| `bnbagent-eval watch --work module:function` | S2: provider gate on funded jobs |
| `bnbagent-eval serve [--pay-to …]` | S3: run the HTTP service |
| `bnbagent-eval schemas [--check]` | write or check the JSON Schema and OpenAPI files |

`bnbagent-eval --config FILE` applies to every command. Every command has `--help`.

## 14. Troubleshooting

| Message or symptom | Meaning and fix |
|---|---|
| `'xyz' is required for this feature. Install it with: pip install 'bnbagent-eval[extra]'` | the metric's method needs an optional extra; install it, or choose another method (e.g. `method: rule`) |
| `the suite has judge metrics but no judge is configured` | set `EVAL_JUDGE_PROVIDER` (and `EVAL_JUDGE_MODEL` where there is no default) (§8) |
| a metric is `skipped` with `missing_input` | the case lacks a field the metric needs (e.g. `expected`, `contexts`); `bnbagent-eval metrics --json` shows each metric's inputs |
| verdict `inconclusive` | a required metric errored or was skipped, too many errors, or a budget or deadline was hit; see `verdict.reasons` |
| `invalid params: ...` when loading a suite | a metric parameter is misspelled or out of range; the message names it (e.g. `text.length` needs at least one of `min_words`, `max_words`, …) |
| `format.regex` fails although the pattern appears in the text | it matches the whole output by default; set `params: {full_match: false}` to search anywhere |
| exit code `64` | a usage or input error (bad file, bad option); the message says which |

## 15. Other guides

| Guide | For |
|---|---|
| [OVERVIEW.md](OVERVIEW.md) | what the package is, why it is needed, the results it gives |
| [SUITES.md](SUITES.md) | every suite option: weights, thresholds, pass rule, stages, lint, lock, eval maker |
| [JUDGES.md](JUDGES.md) | judge providers, configuration, caching, sampling, budgets, calibration |
| [METRICS.md](METRICS.md) | the full metric catalogue |
| [THIRD_PARTY.md](THIRD_PARTY.md) | optional methods from RAGAS, DeepEval, FMEval, AWS AgentCore and local models |
| [ONCHAIN.md](ONCHAIN.md), [TESTNET.md](TESTNET.md) | ERC-8004, ERC-8183, x402 clients; BSC testnet |
| [SERVICE.md](SERVICE.md) | running or calling the eval service |
| [GENERATION.md](GENERATION.md) | generating test cases, simulated users, red-teaming, error injection |
| [REPORTING.md](REPORTING.md) | report formats, CSV, OpenTelemetry / Prometheus / MLflow, privacy |
| [STANDALONE.md](STANDALONE.md) | use without BNB: any agent, any EVM chain or none |
| [MAINTAINER_GUIDE.md](MAINTAINER_GUIDE.md) | how the package is built and maintained |
