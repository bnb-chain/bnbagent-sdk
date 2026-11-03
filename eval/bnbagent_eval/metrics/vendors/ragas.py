"""RAGAS methods (``method="ragas"``) on the canonical metrics.

The mapping is listed in docs/design/metric-catalog.md.

Each handler builds a RAGAS ``SingleTurnSample`` / ``MultiTurnSample`` from the ``EvalCase``,
creates the RAGAS LLM / embeddings from the suite's judge (``_vendor_llm.ragas_models``), scores
one sample with ``single_turn_ascore`` / ``multi_turn_ascore`` and maps NaN to an error.

Input mapping (ETK field -> ``EvalCase``):
``user_input`` <- ``input``; ``response`` <- ``output`` (else the trajectory's final response);
``retrieved_contexts`` <- ``contexts[].text``; ``reference`` <- ``expected`` (string, or a
mapping's ``reference`` / ``expected_outcome``); multi-turn ``user_input`` <- ``conversation``
(else turns derived from the trajectory); ``reference_tool_calls`` <- ``expected`` tool calls;
``reference_topics`` <- ``params.topics``.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..._optional import require
from ...types.case import EvalCase
from ..agent.trajectory import expected_calls
from ..base import Kind, MethodSpec, MetricContext, Outcome, StopScoring
from ..judge_base import LimitedJudge
from ..registry import add_alias, load_builtins, register_method
from ._vendor_llm import (
    case_turns,
    context_texts,
    input_text,
    judge_of,
    judge_provenance,
    output_text,
    ragas_models,
    reference_text,
    vendor_outcome,
)

VENDOR = "ragas"
EXTRA = "ragas"
TOPIC_MODES = ("precision", "recall", "f1")


@dataclass(frozen=True)
class RagasMetric:
    """One RAGAS metric bridged onto a canonical metric."""

    metric_id: str
    names: tuple[str, ...]  # ETK score names (aliases ``ragas.<name>``)
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    multi_turn: bool = False


METRICS: tuple[RagasMetric, ...] = (
    RagasMetric("quality.relevance", ("answer_relevancy",), frozenset({"input", "output"})),
    RagasMetric(
        "quality.answer_correctness",
        ("answer_accuracy",),
        frozenset({"input", "output", "expected"}),
    ),
    RagasMetric("rag.groundedness", ("response_groundedness",), frozenset({"output", "contexts"})),
    RagasMetric("rag.faithfulness", ("faithfulness",), frozenset({"input", "output", "contexts"})),
    RagasMetric("rag.context_relevance", ("context_relevancy",), frozenset({"input", "contexts"})),
    RagasMetric(
        "rag.context_precision",
        ("context_precision",),
        frozenset({"input", "output", "contexts"}),
    ),
    RagasMetric(
        "rag.context_recall", ("context_recall",), frozenset({"input", "contexts", "expected"})
    ),
    RagasMetric(
        "conversation.topic_adherence",
        ("topic_adherence",),
        frozenset({"conversation"}),
        multi_turn=True,
    ),
    RagasMetric(
        "agent.tool_trajectory_match",
        ("tool_call_accuracy",),
        frozenset({"trajectory", "expected"}),
        frozenset({"conversation"}),
        multi_turn=True,
    ),
    RagasMetric(
        "agent.goal_success",
        ("agent_goal_accuracy", "agent_goal_accuracy_with_reference"),
        frozenset(),  # conversation or trajectory (checked in the handler)
        frozenset({"conversation", "trajectory", "expected", "input"}),
        multi_turn=True,
    ),
)


# ------------------------------------------------------------------------- metric objects
def _vendor_metric(m: RagasMetric, case: EvalCase, params: Mapping[str, Any]) -> tuple[Any, str]:
    """``(ragas metric instance, ragas metric name)`` for ``case``."""
    rm = require("ragas.metrics", EXTRA)
    mid = m.metric_id
    if mid == "quality.relevance":
        return rm.AnswerRelevancy(name="answer_relevancy"), "answer_relevancy"
    if mid == "quality.answer_correctness":
        return rm.AnswerAccuracy(name="answer_accuracy"), "answer_accuracy"
    if mid == "rag.groundedness":
        return rm.ResponseGroundedness(name="response_groundedness"), "response_groundedness"
    if mid == "rag.faithfulness":
        return rm.Faithfulness(name="faithfulness"), "faithfulness"
    if mid == "rag.context_relevance":
        return rm.ContextRelevance(name="context_relevancy"), "context_relevancy"
    if mid == "rag.context_precision":
        return (
            rm.LLMContextPrecisionWithoutReference(name="context_precision"),
            "context_precision",
        )
    if mid == "rag.context_recall":
        return rm.ContextRecall(name="context_recall"), "context_recall"
    if mid == "conversation.topic_adherence":
        mode = topic_mode(params)
        return rm.TopicAdherenceScore(name="topic_adherence", mode=mode), "topic_adherence"
    if mid == "agent.tool_trajectory_match":
        strict = params.get("match_type", "exact") != "any_order"
        return (
            rm.ToolCallAccuracy(name="tool_call_accuracy", strict_order=strict),
            "tool_call_accuracy",
        )
    if mid == "agent.goal_success":
        if reference_text(case.expected):
            name = "agent_goal_accuracy_with_reference"
            return rm.AgentGoalAccuracyWithReference(name=name), name
        name = "agent_goal_accuracy"
        return rm.AgentGoalAccuracyWithoutReference(name=name), name
    raise KeyError(mid)


def topic_mode(params: Mapping[str, Any]) -> str:
    mode = params.get("mode", "f1")
    if mode not in TOPIC_MODES:
        raise ValueError(f"ragas topic_adherence mode must be one of {TOPIC_MODES}, got {mode!r}")
    return mode


# ------------------------------------------------------------------------------- samples
def single_turn_sample(case: EvalCase) -> Any:
    ds = require("ragas.dataset_schema", EXTRA)
    kwargs: dict[str, Any] = {}
    if case.input is not None:
        kwargs["user_input"] = input_text(case)
    if case.output is not None or case.trajectory is not None:
        kwargs["response"] = output_text(case)
    if case.contexts:
        kwargs["retrieved_contexts"] = context_texts(case)
    reference = reference_text(case.expected)
    if reference is not None:
        kwargs["reference"] = reference
    return ds.SingleTurnSample(**kwargs)


def ragas_messages(case: EvalCase, *, ignore_args: bool = False) -> list[Any]:
    """RAGAS messages from the case turns; tool messages RAGAS would reject are dropped."""
    msgs = require("ragas.messages", EXTRA)
    out: list[Any] = []
    for turn in case_turns(case):
        role, content = turn["role"], turn["content"] or ""
        if role == "user":
            out.append(msgs.HumanMessage(content=content))
        elif role == "assistant":
            calls = [
                msgs.ToolCall(name=c.name, args={} if ignore_args else dict(c.arguments))
                for c in turn["tool_calls"]
            ]
            out.append(msgs.AIMessage(content=content, tool_calls=calls or None))
        elif role == "tool":
            prev = out[-1] if out else None
            follows_call = isinstance(prev, msgs.ToolMessage) or (
                isinstance(prev, msgs.AIMessage) and prev.tool_calls
            )
            if follows_call:
                out.append(msgs.ToolMessage(content=content))
    return out


def multi_turn_sample(case: EvalCase, params: Mapping[str, Any], metric_id: str) -> Any:
    ds = require("ragas.dataset_schema", EXTRA)
    msgs = require("ragas.messages", EXTRA)
    ignore_args = (
        metric_id == "agent.tool_trajectory_match" and params.get("compare_args") == "ignore"
    )
    messages = ragas_messages(case, ignore_args=ignore_args)
    if not messages:
        raise StopScoring("missing_input", "missing required input(s): conversation or trajectory")
    kwargs: dict[str, Any] = {"user_input": messages}
    if metric_id == "agent.tool_trajectory_match":
        kwargs["reference_tool_calls"] = [
            msgs.ToolCall(name=c.name, args={} if ignore_args else dict(c.arguments))
            for c in expected_calls(case.expected)
        ]
    elif metric_id == "conversation.topic_adherence":
        kwargs["reference_topics"] = [str(t) for t in params.get("topics", ())]
    else:
        reference = reference_text(case.expected)
        if reference:
            kwargs["reference"] = reference
    return ds.MultiTurnSample(**kwargs)


# ------------------------------------------------------------------------------- handler
def _handler(m: RagasMetric) -> Callable[..., Any]:
    async def handle(metric: Any, case: EvalCase, ctx: MetricContext) -> Outcome:
        params = ctx.params
        vm, name = _vendor_metric(m, case, params)
        provenance: dict[str, Any] = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            if hasattr(vm, "llm"):  # deterministic metrics (tool_call_accuracy) need no judge
                judge = judge_of(ctx, m.metric_id)
                llm, embeddings = ragas_models(LimitedJudge(ctx), metric_id=m.metric_id)
                vm.llm = llm
                if hasattr(vm, "embeddings"):
                    vm.embeddings = embeddings
                provenance = judge_provenance(judge)
            run_config = require("ragas.run_config", EXTRA).RunConfig()
            vm.init(run_config)
            if m.multi_turn:
                sample = multi_turn_sample(case, params, m.metric_id)
                value = await vm.multi_turn_ascore(sample)
            else:
                sample = single_turn_sample(case)
                value = await vm.single_turn_ascore(sample)
        extra: dict[str, Any] = {}
        if m.metric_id == "conversation.topic_adherence":
            extra["mode"] = topic_mode(params)
        if m.metric_id == "agent.tool_trajectory_match":
            extra["strict_order"] = vm.strict_order
            if params.get("compare_args") == "subset":
                extra["compare_args_note"] = "ragas compares arguments exactly (subset ignored)"
        return vendor_outcome(
            value, vendor=VENDOR, vendor_metric=name, provenance=provenance, **extra
        )

    return handle


def method_spec(m: RagasMetric) -> MethodSpec:
    return MethodSpec(
        "ragas",
        Kind.VENDOR,
        required_inputs=m.required,
        optional_inputs=m.optional,
        extra=EXTRA,
        extra_module="ragas",
    )


# Method-specific params (merged into each target metric's schema; native params win).
RAGAS_PARAMS: dict[str, Any] = {"mode": {"enum": ["precision", "recall", "f1"]}}

load_builtins()  # canonical metrics first (re-entrant during auto-discovery)
for _m in METRICS:
    register_method(_m.metric_id, method_spec(_m), _handler(_m), params=RAGAS_PARAMS)
    for _name in _m.names:
        add_alias(f"ragas.{_name}", _m.metric_id, "ragas")
