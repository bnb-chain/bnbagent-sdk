"""The fixed request behind the determinism suite.

Also imported by ``scripts/check_hash_stability.py`` to compare hashes across Python versions, so
this module depends on the core package only (no optional extras, no network, no clock).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bnbagent_eval.engine import Runner
from bnbagent_eval.engine.targets import register_target
from bnbagent_eval.hashing import hash_obj
from bnbagent_eval.judges.base import JudgeRequest
from bnbagent_eval.judges.cache import SqliteCache
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import (
    EvalCase,
    EvalRequest,
    EvalResponse,
    LlmStep,
    Message,
    MetricConfig,
    PassRule,
    RunOptions,
    Session,
    Suite,
    TargetRef,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    Usage,
)

GOLDEN = Path(__file__).with_name("golden_hashes.json")
TARGET = "determinism-agent"
RUNS = 3
SAMPLES = 3


def scripted_reply(request: JudgeRequest) -> dict[str, Any]:
    """A schema-valid reply that depends only on (metric, sample index): order-independent, so
    concurrent scoring cannot change which sample gets which label."""
    schema = request.output_schema or {}
    props: Mapping[str, Any] = schema.get("properties", {})
    meta = request.metadata
    seed = int(
        hash_obj([meta.get("metric_id"), meta.get("tag"), meta.get("sample_idx")])[2:10], 16
    )
    out: dict[str, Any] = {}
    for name in schema.get("required", list(props)):
        spec = props.get(name, {})
        kind = spec.get("type")
        if "enum" in spec:
            # Vary the label per sample (exercises median-low / majority) but stay upper-half.
            labels = spec["enum"]
            out[name] = labels[len(labels) // 2 + seed % (len(labels) - len(labels) // 2)]
        elif kind == "array":
            out[name] = []
        elif kind == "boolean":
            out[name] = True
        elif kind in ("number", "integer"):
            lo, hi = spec.get("minimum", 0), spec.get("maximum", 1)
            out[name] = hi if kind == "integer" else round(lo + (hi - lo) * 0.75, 6)
        elif kind == "object":
            out[name] = {}
        else:
            out[name] = f"deterministic reasoning {seed % 97}"
    return out


def make_judge() -> FakeJudge:
    return FakeJudge(scripted_reply, model="fake-judge-v1", samples=SAMPLES)


def _trajectory(case_id: str) -> Session:
    steps = (
        LlmStep(
            id="1",
            model="agent-m1",
            start_ms=0,
            end_ms=300,
            usage=Usage(input_tokens=120, output_tokens=40),
            tool_calls=(ToolCall(name="price_lookup", arguments={"symbol": "BNB"}, id="c1"),),
        ),
        ToolStep(
            id="2",
            parent_id="1",
            call=ToolCall(name="price_lookup", arguments={"symbol": "BNB"}, id="c1"),
            start_ms=300,
            end_ms=450,
            result=ToolResult(content={"usd": 600.5}, call_id="c1"),
        ),
        LlmStep(
            id="3",
            model="agent-m1",
            start_ms=450,
            end_ms=700,
            usage=Usage(input_tokens=200, output_tokens=30),
            response=f"answer for {case_id}",
        ),
    )
    return Session(id=f"s-{case_id}", traces=(Trace(id="t", steps=steps),))


ANSWERS = {"1": "BNB trades at about 600 USD.", "2": "Gas fees on BNB Chain are low."}


def agent(case: EvalCase) -> dict[str, Any]:
    """Deterministic target: fixed output, trajectory and latency per case."""
    return {
        "output": ANSWERS[case.id],
        "trajectory": _trajectory(case.id).to_dict(),
        "app_metrics": {"latency_ms": 250.0, "cost_usd": 0.0012},
    }


register_target(TARGET, agent)

SUITE = Suite(
    name="determinism",
    version="1",
    metrics=(
        MetricConfig(id="text.exact_match", required=True),
        MetricConfig(id="text.token_f1"),
        MetricConfig(id="text.contains", params={"mode": "any"}),
        MetricConfig(id="ops.consistency", method="exact"),
        MetricConfig(id="ops.reliability"),
        MetricConfig(id="ops.latency", value_max=1000),
        MetricConfig(id="agent.trajectory_stats"),
        MetricConfig(id="quality.relevance"),
        MetricConfig(id="conversation.coherence"),
        MetricConfig(id="agent.task_completion"),
    ),
    pass_rule=PassRule(min_aggregate=0.5, min_pass_rate=0.0),
)


def _cases() -> tuple[EvalCase, ...]:
    convo = (
        Message(role="user", content="Hi, I want BNB facts."),
        Message(role="assistant", content="Sure, ask away."),
    )
    return (
        EvalCase(
            id="1",
            input="What is the BNB price?",
            expected="BNB trades at about 600 USD.",
            conversation=(*convo, Message(role="user", content="What is the BNB price?")),
        ),
        EvalCase(
            id="2",
            input="Are fees low on BNB Chain?",
            expected="Fees on BNB Chain are low.",
            conversation=(*convo, Message(role="user", content="Are fees low on BNB Chain?")),
        ),
    )


def build_request() -> EvalRequest:
    return EvalRequest(
        stage="review",
        suite=SUITE,
        cases=_cases(),
        target=TargetRef(kind="callable", uri=TARGET, config={"pass_case": True}),
        options=RunOptions(runs=RUNS, max_concurrency=4),
    )


async def evaluate(**runner_kw: Any) -> EvalResponse:
    runner_kw.setdefault("judge", make_judge())
    return await Runner(**runner_kw).run(build_request())


def hashes(resp: EvalResponse) -> dict[str, str]:
    return {"result_hash": resp.result_hash, "suite_hash": resp.suite_hash}


def main(argv: list[str] | None = None) -> None:
    """Print the fixed request's hashes as JSON (used by scripts/check_hash_stability.py and the
    cache-restart test). ``--cache PATH`` uses a sqlite judge cache; ``--offline`` makes every
    judge call fail, so a result can only come from the cache."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    kw: dict[str, Any] = {}
    if args.cache:
        kw["cache"] = SqliteCache(args.cache)
    if args.offline:
        kw["judge"] = FakeJudge(
            RuntimeError("offline: the judge must not be called"),
            model="fake-judge-v1",
            samples=SAMPLES,
        )
    resp = asyncio.run(evaluate(**kw))
    print(json.dumps({**hashes(resp), "judge_sources": judge_sources(resp)}, sort_keys=True))


def judge_sources(resp: EvalResponse) -> list[str]:
    return sorted(
        {
            m.provenance.judge_source
            for c in resp.results
            for m in c.metrics
            if m.provenance and m.provenance.judge_source
        }
    )


if __name__ == "__main__":
    main()
