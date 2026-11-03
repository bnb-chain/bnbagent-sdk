from __future__ import annotations

import json

import httpx
import pytest

from bnbagent_eval import EvalClient, MetricConfig, Suite
from bnbagent_eval.metrics.custom.human_review import (
    HumanReview,
    WebhookSink,
    sign_human_score,
    verify_human_score,
)
from bnbagent_eval.types import EvalCase

from ._util import run_metric

pytest.importorskip("eth_account", reason="needs the [evm] extra")
from bnbagent_eval.chain.attestation import LocalKeySigner  # noqa: E402

KEY_A = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # secret-scan: allow
KEY_B = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # secret-scan: allow


async def test_pending_then_signed_score(tmp_path) -> None:
    reviewer = LocalKeySigner(KEY_A)
    params = {"sink": "file", "path": str(tmp_path), "reviewers": [reviewer.address]}
    case = EvalCase(id="1", input="q", output="answer")
    suite = Suite(
        name="h",
        version="1",
        metrics=(MetricConfig(id="custom.human_review", params=params, required=True),),
    )
    first = await EvalClient().evaluate(suite=suite, cases=[case])
    assert first.verdict.status == "pending_human"
    task_id = first.results[0].metrics[0].details["task_id"]
    task = json.loads((tmp_path / "tasks" / f"{task_id}.json").read_text())
    assert task["output"] == "answer" and task["reviewers"] == [reviewer.address]
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / f"{task_id}.json").write_text(
        json.dumps(sign_human_score(task_id, 0.9, reviewer, "solid"))
    )
    second = await EvalClient().evaluate(suite=suite, cases=[case])
    m = second.results[0].metrics[0]
    assert m.score == 0.9 and m.reason == "solid" and m.details["reviewer"] == reviewer.address
    assert second.verdict.status == "pass"


@pytest.mark.parametrize("problem", ["unsigned", "wrong_reviewer", "wrong_task", "tampered"])
async def test_rejected_scores_stay_pending(tmp_path, problem) -> None:
    reviewer, outsider = LocalKeySigner(KEY_A), LocalKeySigner(KEY_B)
    params = {"sink": "file", "path": str(tmp_path), "reviewers": [reviewer.address]}
    case = EvalCase(id="1", output="x")
    task_id = HumanReview().task(case, params)["task_id"]
    result = sign_human_score(task_id, 1.0, outsider if problem == "wrong_reviewer" else reviewer)
    if problem == "unsigned":
        result.pop("signature")
    if problem == "wrong_task":
        result["task_id"] = "0" * 16
    if problem == "tampered":
        result["score"] = 0.1  # the signature covers the original score
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / f"{task_id}.json").write_text(json.dumps(result))
    res = await run_metric("custom.human_review", case, params)
    assert res.status == "pending_human" and "rejected_result" in res.details
    with pytest.raises(ValueError):
        verify_human_score(result, task_id, [reviewer.address])


async def test_webhook_sink_and_param_errors() -> None:
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(202)
        return httpx.Response(404)

    sink = WebhookSink(
        "http://review.local/tasks",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await sink.result("abc") is None
    await sink.submit({"task_id": "abc"})
    assert posted == [{"task_id": "abc"}]
    bad = await run_metric(
        "custom.human_review", EvalCase(id="1", output="x"), {"sink": "file", "reviewers": ["0x1"]}
    )
    assert bad.status == "error" and "path" in bad.error.message
    with pytest.raises(ValueError):
        sign_human_score("t", 1.5, LocalKeySigner(KEY_A))
