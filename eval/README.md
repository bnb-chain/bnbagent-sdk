# bnbagent-eval

Evaluation for AI agents built on BNB Chain with the bnbagent SDK, and for any other agent
([STANDALONE.md](docs/STANDALONE.md)). One call (`EvalClient.evaluate`) scores an agent's outputs, conversations, tool trajectories and
transactions against a suite of metrics, and returns one hashed, optionally signed result. The
same result can be published to an agent's ERC-8004 registration, used as evidence for an
ERC-8183 job, or bought from an eval service with x402 ([ONCHAIN.md](docs/ONCHAIN.md)).

- 99 canonical metrics in 12 categories (text, format, ops, quality, safety, rag, conversation,
  agent, onchain, redteam, code, custom), with rule, LLM-judge, local-model and vendor methods
  ([METRICS.md](docs/METRICS.md)).
- Judges from OpenAI-compatible APIs, Anthropic or Bedrock, with prompt fencing, sampling,
  caching, budgets and calibration against human labels.
- Reproducible results: suites are hashed and can be locked to metric and prompt versions;
  results carry `result_hash`, provenance and an optional EIP-712 attestation.
- No chain code in the core: chain access goes through small ports, with an adapter for the
  bnbagent SDK.

## Install

```bash
pip install bnbagent-eval                    # core: httpx, pyyaml, eth-hash
pip install 'bnbagent-eval[format]'          # JSON Schema / XML / safe regex metrics
pip install 'bnbagent-eval[bnbagent]'        # ERC-8004 / ERC-8183 through the bnbagent SDK
pip install 'bnbagent-eval[server,evm]'      # the HTTP eval service with x402
```

Python 3.10 or later. All extras are listed in the [user guide](docs/USER_GUIDE.md#4-install).

## Example

<!-- doctest -->
```python
from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite

suite = Suite(name="demo", version="1", metrics=(MetricConfig(id="text.exact_match"),))
resp = EvalClient().evaluate_sync(suite=suite, cases=[EvalCase(id="1", output="4", expected="4")])
print(resp.verdict.status, resp.aggregate.score, resp.result_hash)  # pass 1.0 0x...
```

Or from the command line:

```bash
bnbagent-eval run --suite suite.yaml --cases cases.jsonl --format console
bnbagent-eval metrics                      # list the metrics
```

## Documentation

| Guide | Content |
|---|---|
| [OVERVIEW.md](docs/OVERVIEW.md) | **read first**: what the package is, why BNB Chain agents need it, what it offers and the results it gives |
| [USER_GUIDE.md](docs/USER_GUIDE.md) | **how to use it**: concepts, the metrics, install, a five-minute demo, cases, the Python API, results, commands, troubleshooting |
| [SUITES.md](docs/SUITES.md) | suite format, pass rule and verdict precedence, stages, lint, lock, the eval maker |
| [JUDGES.md](docs/JUDGES.md) | judge providers and configuration, cache, samples, budgets, calibration |
| [METRICS.md](docs/METRICS.md) | the metric catalogue (generated) |
| [THIRD_PARTY.md](docs/THIRD_PARTY.md) | optional RAGAS, DeepEval, FMEval, AgentCore and local-model methods |
| [ONCHAIN.md](docs/ONCHAIN.md) | ports and adapters, S1 ERC-8004 publication, S2 ERC-8183 gate and advisors, S3 x402 clients |
| [SERVICE.md](docs/SERVICE.md) | the HTTP service: endpoints, policy, limits, payments, idempotency, credits |
| [GENERATION.md](docs/GENERATION.md) | RAG and agentic case generation, filters, simulated users, red-teaming, error injection |
| [REPORTING.md](docs/REPORTING.md) | JSON, Markdown, console and CSV reports, exporters, privacy |
| [TESTNET.md](docs/TESTNET.md) | opt-in BSC testnet flows |
| [STANDALONE.md](docs/STANDALONE.md) | use without BNB: any agent, any EVM chain or none, no bnbagent SDK |
| [MAINTAINER_GUIDE.md](docs/MAINTAINER_GUIDE.md) | for maintainers: layout, architecture, the source → canonical metric map, invariants, adding a metric, common tasks |

Reference design documents are in [docs/design/](docs/design/): architecture, metric catalogue,
implementation, testing, security model and source map.

## Development

```bash
pip install -e '.[dev,format,evm,server,crypto]'
ruff format . && ruff check . && lint-imports && pytest
bnbagent-eval metrics --markdown > docs/METRICS.md     # after adding or changing a metric
```

The test suite runs offline: network access is blocked except loopback, judges are scripted
with `FakeJudge`, and chain flows use the in-memory ports in `bnbagent_eval.testing.fakes`.

## License

bnbagent-eval is licensed under the Apache License 2.0 ([LICENSE](LICENSE)). It is an
Apache-2.0 package inside the MIT-licensed bnbagent-sdk repository; the licence applies to this
directory only. [NOTICE](NOTICE) lists the projects whose evaluation methods it re-implements
(Gen AI Evaluation Toolkit on AWS, AgentSight, STED, Google ADK evaluation).
