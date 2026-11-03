# Third-party methods

Every metric in `bnbagent-eval` has a **native** method implemented in this package: rule code, a
judge prompt or our implementation of a published metric (STED, BERTScore, ROUGE, ...). Some
metrics can also be computed by another evaluation tool. These **third-party methods** are
optional, never the default, and work like native ones: pick them with `method:` in a suite.

Use them to compare against a tool you already trust, to reproduce numbers from a RAGAS or
DeepEval setup, or to run AWS AgentCore's managed evaluators.

## Providers

| Provider | Role | Install | Needs |
|---|---|---|---|
| [RAGAS](https://github.com/explodinggradients/ragas) | evaluator | `pip install 'bnbagent-eval[ragas]'` | your judge config (RAGAS's LLM and embeddings are built from it) |
| [DeepEval](https://github.com/confident-ai/deepeval) | evaluator | `pip install 'bnbagent-eval[deepeval]'` | your judge config (wrapped as a DeepEval model) |
| [FMEval](https://github.com/aws/fmeval) | evaluator | `pip install 'bnbagent-eval[fmeval]'` | nothing else (local algorithms; toxicity downloads a model) |
| [AWS Bedrock AgentCore Evaluations](https://docs.aws.amazon.com/bedrock-agentcore/) | evaluator | `pip install 'bnbagent-eval[agentcore]'` | AWS credentials, a region with AgentCore, the case's trajectory (a cloud service) |
| [Hugging Face transformers](https://github.com/huggingface/transformers) | model | `pip install 'bnbagent-eval[semantic]'` | downloads encoder weights (BERTScore itself is ours) |
| [Detoxify](https://github.com/unitaryai/detoxify) | model | `pip install 'bnbagent-eval[toxicity]'` | downloads classifier weights |
| [LanguageTool](https://languagetool.org) | model | `pip install 'bnbagent-eval[fluency]'` | Java (starts a local LanguageTool server) |
| [gensim](https://radimrehurek.com/gensim/) | model | `pip install 'bnbagent-eval[embeddings]'` | a local GloVe / fastText vectors file |

**Evaluator**: the provider computes the score. **Model**: our code computes the metric with the
provider's model. Which metric has which method:

```bash
bnbagent-eval metrics --third-party
```

The same list, generated from the registry, is the "Third-party methods" section of
[METRICS.md](METRICS.md#third-party-methods).

<!-- doctest -->
```python
from bnbagent_eval.authoring.catalog_doc import third_party_methods

listed = third_party_methods()
print(sorted(listed))  # ['agentcore', 'deepeval', 'embeddings', 'fluency', 'fmeval', 'ragas', ...]
assert ("rag.faithfulness", "ragas") in listed["ragas"]
```

## Using a third-party method

Install the provider's extra, then name the method:

```yaml
name: rag-check
version: "1"
metrics:
  - id: rag.faithfulness              # native judge (the default)
  - id: rag.faithfulness
    name: faithfulness_ragas          # a second config of the same metric needs its own name
    method: ragas
  - id: deepeval.bias                 # an alias: safety.bias with method deepeval
```

Everything else is unchanged: the same cases, thresholds, pass rules, reports and result hashes.
Each result records the method that produced it (`metric.method`, `provenance.method`).

**Judge settings carry over.** RAGAS and DeepEval use the same judge as the rest of the suite
(`EVAL_JUDGE_*`, the config file or the suite's `judge` pin), so switching the method does not
switch the model. AgentCore runs on AWS: set `params: {region: us-east-1}` (or `AWS_REGION`).
A custom AgentCore evaluator runs with `params: {agentcore_evaluator_id: <id>, level: session}`.

**Compare native and third-party** on the same cases by listing both, as above; the report shows
them side by side (one row per config name).

**Missing install**: a suite that names a third-party method without its extra fails at load time
with the exact command, e.g. `'ragas' is required ... pip install 'bnbagent-eval[ragas]'`.

## Things to know

- **Reproducibility.** A third-party score depends on the provider's version and, for RAGAS /
  DeepEval / AgentCore, on their prompts. Pin the provider version in your environment when results
  must be comparable over time. Native methods are versioned by this package (prompt hashes,
  metric versions in the suite lock).
- **The eval service** runs only rule and judge methods unless the operator enables more:
  `EVAL_SERVER_ALLOWED_KINDS=rule,judge,vendor` ([SERVICE.md](SERVICE.md)).
- **Red-teaming** with PyRIT: `generation.redteam.run_red_team(..., backend="pyrit")` with
  `pip install 'bnbagent-eval[redteam]'`; the built-in `native` backend needs nothing extra
  ([GENERATION.md](GENERATION.md)).
- **Costs.** RAGAS, DeepEval and AgentCore make their own model calls, often several per case.
  RAGAS and DeepEval call your judge, so each of their calls is checked against the run's judge
  budget and deadline, but they bypass the judge cache. AgentCore calls are made by the AWS service and are not limited by the budget.
