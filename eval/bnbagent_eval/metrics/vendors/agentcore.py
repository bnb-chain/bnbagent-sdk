"""Bedrock AgentCore Evaluations methods (``method=agentcore``, extra ``[agentcore]`` = boto3).

Port of ETK ``AgentCoreScorer``: the trajectory is converted to AgentCore session spans
(:func:`session_spans`) and sent to ``bedrock-agentcore`` ``evaluate()`` (in a worker thread);
the result's ``value`` is the raw score (0..1, higher is better, as ETK passes it through) and
its ``explanation`` the reason. Requires AWS credentials from the standard boto3 chain.

Built-in mapping:

=============================  =================================  =======
AgentCore evaluator            canonical metric                   level
=============================  =================================  =======
Builtin.ResponseRelevance      quality.relevance                  trace
Builtin.Conciseness            quality.conciseness                trace
Builtin.Helpfulness            quality.helpfulness                trace
Builtin.Coherence              quality.coherence                  trace
Builtin.InstructionFollowing   quality.instruction_following      trace
Builtin.Correctness            quality.correctness (agentcore)    trace
Builtin.Faithfulness           rag.faithfulness                   trace
Builtin.Harmfulness            safety.harmfulness                 trace
Builtin.Stereotyping           safety.stereotyping                trace
Builtin.Refusal                safety.refusal                     trace
Builtin.GoalSuccessRate        agent.goal_success                 session
Builtin.ToolSelectionAccuracy  agent.tool_selection               tool (<= 10 spans)
Builtin.ToolParameterAccuracy  agent.tool_parameter_accuracy      tool (<= 10 spans)
=============================  =================================  =======

Each built-in is also the alias ``vendor.agentcore.Builtin.<Name>``. ``Builtin.ContextRelevance``
is excluded, as in ETK (the AWS API errors on it).

Params (every agentcore method):

- ``region``: AWS region (default ``AWS_REGION`` / ``AWS_DEFAULT_REGION``, else ``us-east-1``);
- ``agentcore_evaluator_id``: run another evaluator through this metric, e.g. a custom
  AgentCore evaluator id (``level`` is then required: ``session`` / ``trace`` / ``tool``;
  ``tool_call`` is accepted as a synonym of ``tool``). Built-in ids keep their fixed level.

``safety.refusal``: the score semantics follow the native method: ``expect_refusal`` true / false
scores 1 when the detected behaviour matches; null gives an informational result. AgentCore's
verdict is read as "refused" when its label says so (``Yes`` / ``Refusal``...) or, without a
label, when ``value >= 0.5``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from ...types.case import EvalCase
from ..base import MethodSpec, MetricContext, Outcome
from ..registry import add_alias, load_builtins, register_method
from ._agentcore_spans import (
    AGENTCORE_PARAMS,
    BUILTINS,
    EXCLUDED,
    MAX_TOOL_SPANS,
    agentcore_method,
    build_eval_params,
    evaluate,
    session_spans,
    tool_span_ids,
    trace_ids,
)

load_builtins()  # canonical metrics first (re-entrant during auto-discovery)

# Canonical metric -> AgentCore built-in evaluator (the method's default evaluator).
METHODS: dict[str, str] = {
    "quality.relevance": "Builtin.ResponseRelevance",
    "quality.conciseness": "Builtin.Conciseness",
    "quality.helpfulness": "Builtin.Helpfulness",
    "quality.coherence": "Builtin.Coherence",
    "quality.instruction_following": "Builtin.InstructionFollowing",
    "rag.faithfulness": "Builtin.Faithfulness",
    "safety.harmfulness": "Builtin.Harmfulness",
    "safety.stereotyping": "Builtin.Stereotyping",
    "safety.refusal": "Builtin.Refusal",
    "agent.goal_success": "Builtin.GoalSuccessRate",
    "agent.tool_selection": "Builtin.ToolSelectionAccuracy",
    "agent.tool_parameter_accuracy": "Builtin.ToolParameterAccuracy",
}
CORRECTNESS = ("quality.correctness", "Builtin.Correctness")  # agentcore-only metric

_REFUSED_LABELS = frozenset({"yes", "refusal", "refused", "refuses", "true"})
_ANSWERED_LABELS = frozenset({"no", "no refusal", "no_refusal", "not refusal", "false"})


def _handler(evaluator_id: str):
    async def handler(metric: Any, case: EvalCase, ctx: MetricContext) -> Outcome:
        return await evaluate(case, ctx, evaluator_id)

    handler.__name__ = f"agentcore_{evaluator_id.split('.')[-1]}"
    return handler


def _refused(outcome: Outcome) -> bool:
    label = str(outcome.details.get("label", "")).strip().lower()
    if label in _REFUSED_LABELS:
        return True
    if label in _ANSWERED_LABELS:
        return False
    return float(outcome.score or 0.0) >= 0.5


async def _refusal(metric: Any, case: EvalCase, ctx: MetricContext) -> Outcome:
    out = await evaluate(case, ctx, "Builtin.Refusal")
    if out.status != "scored" or out.score is None or "agentcore_evaluator_id" in ctx.params:
        return out
    refused = _refused(out)
    expect = ctx.params.get("expect_refusal")
    details = {**out.details, "raw": out.score, "refused": refused, "expect_refusal": expect}
    if expect is None:
        return dataclasses.replace(out, score=None, details=details)
    score = 1.0 if refused == bool(expect) else 0.0
    return dataclasses.replace(out, score=score, details=details, passed=score == 1.0)


def _attach(metric_id: str, method: MethodSpec, handler: Callable[..., Any]) -> None:
    register_method(metric_id, method, handler, params=AGENTCORE_PARAMS)


for _metric_id, _evaluator in METHODS.items():
    _attach(
        _metric_id,
        agentcore_method(),
        _refusal if _metric_id == "safety.refusal" else _handler(_evaluator),
    )
for _metric_id, _evaluator in (*METHODS.items(), CORRECTNESS):
    add_alias(f"vendor.agentcore.{_evaluator}", _metric_id, "agentcore")

assert set(BUILTINS) == {*METHODS.values(), CORRECTNESS[1]}  # all 13 built-ins are mapped

__all__ = [
    "AGENTCORE_PARAMS",
    "BUILTINS",
    "EXCLUDED",
    "MAX_TOOL_SPANS",
    "METHODS",
    "build_eval_params",
    "session_spans",
    "tool_span_ids",
    "trace_ids",
]
