"""The ``agentic`` method of custom.rubric / custom.requirements_compliance (FakeJudge only)."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from bnbagent_eval.engine.budget import BudgetTracker
from bnbagent_eval.engine.resolve import resolve_suite
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import Kind, MetricContext
from bnbagent_eval.metrics.vendors.agentic import calculate, derive, verdict_items
from bnbagent_eval.testing.fakes import MemoryStorage
from bnbagent_eval.types import Artifact, Budget, EvalCase, MetricConfig, MetricResult, Suite

RUBRICS = [
    {"id": "json", "text": "The response is JSON."},
    {"id": "title", "text": "The response has a title.", "weight": 2},
]
PLAN = {"checks": [{"name": "json"}, {"name": "title"}], "plan": "read the output"}


def data_uri(text: str, media: str = "text/plain") -> str:
    return f"data:{media};base64,{base64.b64encode(text.encode()).decode()}"


def verdict(**scores: float) -> dict[str, Any]:
    return {
        "action": "write_verdict",
        "scores": [{"name": k, "score": v} for k, v in scores.items()],
        "summary": "sum",
        "issues": ["i1"],
    }


async def agentic(
    metric_id: str,
    case: EvalCase,
    script: Any,
    params: dict | None = None,
    **ctx_kw: Any,
) -> tuple[MetricResult, FakeJudge]:
    cls, _ = registry.resolve(metric_id)
    params = params or {}
    judge = FakeJudge(script)
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params, method="agentic"),
        method=registry.get_spec(metric_id).method("agentic"),
        params=params,
        judge=judge,
        **ctx_kw,
    )
    return await cls().evaluate(case, ctx), judge


async def test_tool_loop_reads_output_artifacts_documents_and_calculates() -> None:
    storage = MemoryStorage()
    uri = await storage.upload({"text": "def add(a, b):\n    return a + b"}, "code.py")
    case = EvalCase(
        id="c",
        input="Write add().",
        output='{"title": "add"}',
        artifacts=(
            Artifact(uri=uri, hash="0x0", media_type="text/x-python", name="code.py"),
            Artifact(
                uri=data_uri("notes body"), hash="0x0", media_type="text/plain", name="notes"
            ),
        ),
    )
    params = {
        "rubrics": RUBRICS,
        "documents": [{"name": "spec", "text": "Must use type hints."}, data_uri("doc two")],
    }
    script = [
        PLAN,
        {"action": "read_output", "args": {}},
        {"action": "read_artifact", "args": {"name": "code.py"}},
        {"action": "read_artifact", "args": {"name": "1", "offset": 6}},
        {"action": "read_requirement_doc", "args": {"name": "spec"}},
        {"action": "read_requirement_doc", "args": {"name": "doc-2"}},
        {"action": "calculator", "args": {"expression": "2 * (3 + 4)"}},
        {"action": "nope", "args": {}},
        verdict(json=1.0, title=0.5),
    ]
    res, judge = await agentic("custom.rubric", case, script, params, storage=storage)
    assert res.status == "scored", res.error
    assert res.score == round((1.0 + 2 * 0.5) / 3, 3)
    last = judge.requests[-1].user
    for seen in (
        '{"title": "add"}',
        "return a + b",
        "body",
        "Must use type hints.",
        "doc two",
        "->\n14",
        "unknown tool",
    ):
        assert seen in last, seen
    assert "notes body" not in last  # offset 6
    assert (
        "document spec" in judge.requests[0].user and "artifact code.py" in judge.requests[0].user
    )
    assert [s["action"] for s in res.details["steps"]][:3] == [
        "read_output",
        "read_artifact",
        "read_artifact",
    ]
    assert res.details["issues"] == ["i1"] and res.reason == "sum"
    assert res.provenance.samples == 1 and res.provenance.prompt_hash
    assert len(judge.requests) == len(script)


async def test_artifact_reads_are_capped_and_need_storage() -> None:
    case = EvalCase(
        id="c",
        output="x",
        artifacts=(
            Artifact(uri=data_uri("x" * 500), hash="0x0", media_type="text/plain", name="big"),
            Artifact(uri="memory://abc/doc", hash="0x0", media_type="application/json", name="m"),
        ),
    )
    script = [
        PLAN,
        {"action": "read_artifact", "args": {"name": "big"}},
        {"action": "read_artifact", "args": {"name": "m"}},
        {"action": "read_artifact", "args": {"name": "missing"}},
        verdict(json=1, title=1),
    ]
    res, judge = await agentic(
        "custom.rubric", case, script, {"rubrics": RUBRICS, "max_artifact_bytes": 100}
    )
    last = judge.requests[-1].user
    assert "too_large" in last or "exceeds 100 bytes" in last
    assert "only data: URIs or ctx.storage" in last and "no such item" in last
    assert res.score == 1.0


async def test_window_limits_tool_output() -> None:
    case = EvalCase(id="c", output="a" * 300 + "TAIL")
    script = [
        PLAN,
        {"action": "read_output", "args": {"limit": 250}},
        verdict(json=1, title=1),
    ]
    _, judge = await agentic(
        "custom.rubric", case, script, {"rubrics": RUBRICS, "max_tool_chars": 200}
    )
    last = judge.requests[-1].user
    assert "TAIL" not in last and "more characters; use offset=200" in last


async def test_step_cap_then_verdict_call() -> None:
    case = EvalCase(id="c", output="x")
    script = (
        [PLAN]
        + [{"action": "calculator", "args": {"expression": "1+1"}}] * 2
        + [verdict(json=1, title=0)]
    )
    res, judge = await agentic("custom.rubric", case, script, {"rubrics": RUBRICS, "max_steps": 2})
    assert len(judge.requests) == 4
    assert res.details["steps"][-1]["action"] == "step_cap"
    assert judge.requests[-1].metadata["tag"].endswith(":v0")
    assert res.score == round(1 / 3, 3)


async def test_weighted_verdict_param_weights_and_missing_checks() -> None:
    case = EvalCase(id="c", output="x")
    # Param weights win over the judge's; a listed check missing from the verdict scores 0.
    script = [
        PLAN,
        {
            "action": "write_verdict",
            "scores": [{"name": "JSON", "score": 1.0, "weight": 500}],
            "summary": "s",
        },
    ]
    res, _ = await agentic("custom.rubric", case, script, {"rubrics": RUBRICS})
    assert res.score == round(1 / 3, 3)
    title = next(r for r in res.details["scores"] if r["name"] == "title")
    assert title["assessed"] is False and title["score"] == 0.0


async def test_requirements_extracted_by_plan_use_judge_weights() -> None:
    case = EvalCase(id="c", input="Do A and B.", output="A")
    plan = {"checks": [{"name": "REQ-1", "description": "A"}, {"name": "REQ-2"}], "plan": "p"}
    reply = {
        "action": "write_verdict",
        "scores": [
            {"name": "REQ-1", "score": 1.0, "weight": 3},
            {"name": "REQ-2", "score": 0.0, "weight": 1},
        ],
        "summary": "s",
    }
    res, judge = await agentic("custom.requirements_compliance", case, [plan, reply])
    assert res.score == 0.75 and res.details["checks_source"] == "extracted"
    assert "extract the requirements" in judge.requests[0].user


async def test_invalid_verdict_is_repaired_once_then_errors() -> None:
    case = EvalCase(id="c", output="x")
    dup = {"scores": [{"name": "a", "score": 1}, {"name": "a", "score": 0}], "summary": "s"}
    ok = {"scores": [{"name": "json", "score": 1}, {"name": "title", "score": 1}], "summary": "s"}
    script = [PLAN, {"action": "write_verdict", **dup}, dup, ok]
    res, judge = await agentic("custom.rubric", case, script, {"rubrics": RUBRICS})
    assert res.score == 1.0
    assert "duplicate metric name" in judge.requests[-1].user
    res, _ = await agentic(
        "custom.rubric", case, [PLAN, {"action": "finish"}, dup, dup], {"rubrics": RUBRICS}
    )
    assert res.status == "error" and res.error.code == "judge_parse_error"


def test_verdict_rules() -> None:
    with pytest.raises(ValueError, match="blank"):
        verdict_items({"scores": [{"name": "  ", "score": 1}]})
    with pytest.raises(ValueError, match="finite"):
        verdict_items({"scores": [{"name": "a", "score": 1.5}]})
    with pytest.raises(ValueError, match="weight"):
        verdict_items({"scores": [{"name": "a", "score": 1, "weight": 1001}]})
    with pytest.raises(ValueError, match="non-empty"):
        verdict_items({"scores": []})
    assert verdict_items({"summary": "no scores"}) is None
    items = verdict_items({"scores": [{"name": "a", "score": 1, "weight": 0}]})
    with pytest.raises(ValueError, match="sum to zero"):
        derive(items, [])
    native = verdict_items(
        {
            "evaluations": [
                {"rubric_id": "json", "verdict": "yes"},
                {"rubric_id": "x", "verdict": "n/a"},
            ]
        }
    )
    assert derive(native, []) == (
        [
            {"name": "json", "score": 1.0, "weight": 1.0, "assessed": True},
            {"name": "x", "score": None, "weight": 1.0, "assessed": True},
        ],
        1.0,
    )
    assert calculate("7 // 2 + 2 ** 3 - -1") == 12
    for bad in ("__import__('os')", "2 ** 1000", "a + 1"):
        with pytest.raises(ValueError):
            calculate(bad)


async def test_budget_exhaustion_mid_loop_skips() -> None:
    case = EvalCase(id="c", output="x")
    script = [PLAN, {"action": "read_output", "args": {}}, verdict(json=1, title=1)]
    budget = BudgetTracker(Budget(max_judge_calls=2))
    res, judge = await agentic("custom.rubric", case, script, {"rubrics": RUBRICS}, budget=budget)
    assert res.status == "skipped" and res.error.code == "budget_exhausted"
    assert len(judge.requests) == 2


async def test_deadline_is_checked_per_call() -> None:
    clock = iter([0.0, 0.0, 0.0, 100.0, 100.0, 100.0])
    case = EvalCase(id="c", output="x")
    script = [PLAN, {"action": "read_output", "args": {}}, verdict(json=1, title=1)]
    res, judge = await agentic(
        "custom.rubric",
        case,
        script,
        {"rubrics": RUBRICS},
        deadline=50.0,
        clock=lambda: next(clock),
    )
    assert res.status == "skipped" and res.error.code == "deadline"
    assert 1 <= len(judge.requests) < 3


async def test_needs_output_or_artifacts() -> None:
    res, judge = await agentic(
        "custom.rubric", EvalCase(id="c", input="q"), [PLAN], {"rubrics": RUBRICS}
    )
    assert res.status == "skipped" and res.error.code == "missing_input" and not judge.requests


def test_spec_kind_service_and_params() -> None:
    criteria = {"custom.rubric": {"rubrics": RUBRICS}, "custom.requirements_compliance": {}}
    for metric_id, given in criteria.items():
        spec = registry.get_spec(metric_id)
        method = spec.method("agentic")
        assert method.kind is Kind.JUDGE and method.service_allowed is False
        assert not spec.validate_params({"max_steps": 3, "documents": ["text"], **given})
        assert spec.validate_params({"max_steps": 0, **given})
    suite = Suite(
        name="s",
        version="1",
        metrics=(
            MetricConfig(
                id="custom.rubric", method="agentic", params={"rubrics": RUBRICS, "max_steps": 4}
            ),
        ),
    )
    assert resolve_suite(suite).metrics[0].method.name == "agentic"
