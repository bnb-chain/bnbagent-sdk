"""Core-only smoke test: registry + an evaluation, with no optional extras installed."""

from __future__ import annotations

import sys

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval.metrics import registry

FORBIDDEN = ("bnbagent", "web3", "eth_account", "fastapi", "boto3", "torch", "nacl")

specs = registry.list_specs()
suite = Suite(
    name="core",
    version="1",
    metrics=(
        MetricConfig(id="text.exact_match", required=True),
        MetricConfig(id="text.length", params={"max_words": 10}),
    ),
)
resp = EvalClient().evaluate_sync(
    suite=suite, cases=[EvalCase(id="1", output="BNB", expected="BNB")]
)
import bnbagent_eval.chain.erc8004  # noqa: E402,F401 - chain code imports without extras
import bnbagent_eval.chain.erc8183  # noqa: E402,F401
import bnbagent_eval.cli  # noqa: E402,F401

leaked = sorted({m.split(".")[0] for m in sys.modules} & set(FORBIDDEN))
print(f"metrics={len(specs)} verdict={resp.verdict.status} leaked={leaked}")
sys.exit(0 if resp.verdict.status == "pass" and not leaked and len(specs) == 99 else 1)
