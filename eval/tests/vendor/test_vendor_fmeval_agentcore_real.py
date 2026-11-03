"""Opt-in checks against the real FMEval library and the real AgentCore Evaluations API.

Not run by default (marker ``vendor``); importable without either vendor installed.

    pip install fmeval && pytest -m vendor tests/vendor -k fmeval
    EVAL_VENDOR_AGENTCORE=1 AWS_REGION=us-east-1 \
        pytest -m vendor tests/vendor -k agentcore

AgentCore uses the standard boto3 credential chain and incurs (small) AWS costs; nothing is read
from or written to the repo.
"""

from __future__ import annotations

import os
import socket

import pytest

from bnbagent_eval import EvalCase, EvalClient, MetricConfig, Suite
from bnbagent_eval._optional import available

pytestmark = pytest.mark.vendor

needs_fmeval = pytest.mark.skipif(not available("fmeval"), reason="needs the [fmeval] extra")
needs_agentcore = pytest.mark.skipif(
    os.environ.get("EVAL_VENDOR_AGENTCORE") != "1" or not available("boto3"),
    reason="set EVAL_VENDOR_AGENTCORE=1 (boto3 + AWS credentials) to call AgentCore",
)


@pytest.fixture(autouse=True)
def allow_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import _real_connect

    monkeypatch.setattr(socket.socket, "connect", _real_connect)


async def _evaluate(metric: MetricConfig, case: EvalCase):
    suite = Suite(name="vendor", version="1", metrics=(metric,))
    resp = await EvalClient().evaluate(suite=suite, cases=[case])
    return resp.results[0].metrics[0]


@needs_fmeval
@pytest.mark.parametrize(
    ("metric_id", "best"),
    [
        ("fmeval.qa_accuracy.exact_match_score", 1.0),
        ("fmeval.qa_accuracy.f1_score", 1.0),
        ("fmeval.factual_knowledge", 1.0),
    ],
)
async def test_fmeval_real_qa(metric_id: str, best: float) -> None:
    case = EvalCase(id="1", input="Capital of France?", output="Paris", expected="Paris")
    res = await _evaluate(MetricConfig(id=metric_id), case)
    assert res.status == "scored", res.error
    assert res.score == pytest.approx(best)


@needs_fmeval
async def test_fmeval_real_toxicity() -> None:
    case = EvalCase(id="1", output="Have a lovely day, friend.")
    res = await _evaluate(MetricConfig(id="safety.toxicity", method="fmeval"), case)
    assert res.status == "scored", res.error
    assert res.score > 0.9 and "severe_toxicity" in res.details


@needs_agentcore
@pytest.mark.parametrize(
    "metric_id",
    [
        "vendor.agentcore.Builtin.Helpfulness",
        "vendor.agentcore.Builtin.GoalSuccessRate",
        "vendor.agentcore.Builtin.ToolSelectionAccuracy",
        "vendor.agentcore.Builtin.Refusal",
    ],
)
async def test_agentcore_real(metric_id: str) -> None:
    from bnbagent_eval.adapters.traces.agentcore_adot import AgentCoreAdotAdapter
    from tests.unit.metrics.test_vendor_fmeval_agentcore import REAL_TRACE

    session = AgentCoreAdotAdapter().to_session(REAL_TRACE)
    res = await _evaluate(MetricConfig(id=metric_id), EvalCase(id="1", trajectory=session))
    assert res.status == "scored", res.error
    assert res.details["evaluator_id"] == metric_id.removeprefix("vendor.agentcore.")
    # Record the verdict shape (label / value polarity) for the mapping notes.
    print(metric_id, res.details.get("label"), res.details.get("raw", res.score), res.reason)
