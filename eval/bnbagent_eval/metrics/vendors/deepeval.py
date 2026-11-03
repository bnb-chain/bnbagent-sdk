"""DeepEval methods (``method="deepeval"``) on the canonical metrics.

The mapping is listed in docs/design/metric-catalog.md.

Each handler builds a DeepEval ``LLMTestCase`` (single-turn) or ``ConversationalTestCase`` from
the ``EvalCase``, instantiates the metric with our judge wrapped as a ``DeepEvalBaseLLM``
(``_vendor_llm.deepeval_judge_model``), awaits ``a_measure`` and returns DeepEval's score and
reason. Hallucination, bias, toxicity and misuse are ``inverted``: their raw score is the share
of violations (higher is worse). DeepEval < 4.2 reports that share; DeepEval >= 4.2 reports
``1 - share`` ("1 is a pass"), which the handler turns back into the share
(``violation_share``), so the result does not depend on the installed version.

Input mapping (ETK field -> ``EvalCase``):
``input`` <- ``input``; ``actual_output`` <- ``output`` (else the trajectory's final response);
``expected_output`` / ``expected_outcome`` <- ``expected`` (string, or a mapping's ``reference`` /
``expected_outcome``); ``context`` and ``retrieval_context`` <- ``contexts[].text``;
``tools_called`` <- trajectory tool calls with results; ``expected_tools`` <- ``expected`` tool
calls; ``turns`` <- ``conversation`` (else turns derived from the trajectory), tool messages
merged into the preceding assistant turn's ``tools_called[].output``; ``scenario`` <- ``input``;
``chatbot_role`` <- ``params.role``; per-turn ``retrieval_context`` <- ``contexts``.

Pass-through constructor params come from the canonical metric's (schema-validated) params:
``schema`` -> ``expected_schema``, ``pattern`` / ``flags`` -> ``pattern`` / ``ignore_case``,
``instructions`` -> ``prompt_instructions``, ``domain``, ``advice_types``, ``role``,
``topics`` -> ``relevant_topics``, ``match_type`` / ``compare_args`` -> ToolCorrectness'
``should_exact_match`` / ``should_consider_ordering`` / ``evaluation_params``. ``strict_mode``
(boolean) is read from params when the metric's schema admits it (checked here as well).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..._jsonschema import validate as validate_schema
from ..._optional import available, require
from ...exceptions import SuiteError
from ...types.case import EvalCase
from ...types.trajectory import AgentStep, LlmStep, ToolStep
from ..agent._mcp import conversation_mcp_calls, mcp_target, mcp_tools, session_mcp_calls
from ..agent.trajectory import expected_calls
from ..base import Kind, MethodSpec, MetricContext, Outcome, StopScoring
from ..judge_base import LimitedJudge
from ..quality.instruction_following import instruction_list
from ..registry import add_alias, load_builtins, register_method
from ..text._common import as_text
from ._vendor_llm import (
    case_turns,
    context_texts,
    deepeval_judge_model,
    input_text,
    judge_provenance,
    output_text,
    reference_text,
    tool_call_outputs,
    vendor_outcome,
)

VENDOR = "deepeval"
EXTRA = "deepeval"
Kwargs = Callable[[Mapping[str, Any], EvalCase, Any], dict[str, Any]]


@dataclass(frozen=True)
class DeepEvalMetric:
    """One DeepEval metric bridged onto a canonical metric."""

    name: str  # DeepEval / ETK score name (alias ``deepeval.<name>``)
    cls: str  # class in ``deepeval.metrics``
    metric_id: str
    required: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    conversational: bool = False
    inverted: bool = False
    uses_model: bool = True
    trace: bool = False  # needs ``test_case._trace_dict`` (plan / step metrics)
    mcp: bool = False
    turn_contexts: bool = False  # contexts become each assistant turn's retrieval_context
    kwargs: Kwargs | None = None  # pass-through constructor kwargs
    alias: bool = True  # False: ``deepeval.<name>`` already points to a rule method
    variants: tuple[DeepEvalMetric, ...] = field(default=())  # conversational variant(s)


def _fs(*names: str) -> frozenset[str]:
    return frozenset(names)


# ----------------------------------------------------------------- pass-through kwargs
def _json_schema_kwargs(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
    return {"expected_schema": schema_model(params["schema"])}


def _pattern_kwargs(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
    return {"pattern": params["pattern"], "ignore_case": "IGNORECASE" in params.get("flags", ())}


def _prompt_kwargs(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
    items = instruction_list(params.get("instructions"))
    if not items:
        raise ValueError("deepeval prompt_alignment needs params.instructions")
    return {"prompt_instructions": items}


def _param(src: str, dst: str) -> Kwargs:
    def build(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
        if src not in params:
            raise ValueError(f"deepeval {dst} needs params.{src}")
        value = params[src]
        return {dst: list(value) if isinstance(value, (list, tuple)) else value}

    return build


def _tool_correctness_kwargs(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
    match_type = params.get("match_type", "exact")
    compare = params.get("compare_args", "exact")
    tcp = de.test_case.ToolCallParams
    return {
        "should_exact_match": match_type == "exact",
        "should_consider_ordering": match_type in ("exact", "in_order"),
        "evaluation_params": [] if compare == "ignore" else [tcp.INPUT_PARAMETERS],
    }


def _tool_use_kwargs(params: Mapping[str, Any], case: EvalCase, de: Any) -> dict[str, Any]:
    tools = available_tool_calls(case, de)
    if not tools:
        raise ValueError("deepeval tool_use needs the trajectory's available tools")
    return {"available_tools": tools}


# --------------------------------------------------------------------------- catalogue
_CTX = _fs("contexts")
SINGLE_TURN: tuple[DeepEvalMetric, ...] = (
    DeepEvalMetric(
        "answer_relevancy", "AnswerRelevancyMetric", "quality.relevance", _fs("input", "output")
    ),
    DeepEvalMetric(
        "faithfulness",
        "FaithfulnessMetric",
        "rag.faithfulness",
        _fs("output", "contexts"),
        _fs("input"),
    ),
    DeepEvalMetric(
        "contextual_precision",
        "ContextualPrecisionMetric",
        "rag.context_precision",
        _fs("input", "contexts", "expected"),
        _fs("output"),
    ),
    DeepEvalMetric(
        "contextual_recall",
        "ContextualRecallMetric",
        "rag.context_recall",
        _fs("input", "contexts", "expected"),
        _fs("output"),
    ),
    DeepEvalMetric(
        "contextual_relevancy",
        "ContextualRelevancyMetric",
        "rag.context_relevance",
        _fs("input", "contexts"),
        _fs("output"),
    ),
    DeepEvalMetric(
        "hallucination",
        "HallucinationMetric",
        "rag.hallucination",
        _fs("output", "contexts"),
        _fs("input"),
        inverted=True,
    ),
    DeepEvalMetric(
        "bias", "BiasMetric", "safety.bias", _fs("output"), _fs("input"), inverted=True
    ),
    DeepEvalMetric(
        "toxicity",
        "ToxicityMetric",
        "safety.toxicity",
        _fs("output"),
        _fs("input"),
        inverted=True,
    ),
    DeepEvalMetric(
        "summarization", "SummarizationMetric", "quality.summarization", _fs("input", "output")
    ),
    DeepEvalMetric(
        "json_correctness",
        "JsonCorrectnessMetric",
        "format.json_schema",
        _fs("output"),
        _fs("input"),
        kwargs=_json_schema_kwargs,
        alias=False,
    ),
    DeepEvalMetric(
        "prompt_alignment",
        "PromptAlignmentMetric",
        "quality.instruction_following",
        _fs("input", "output"),
        kwargs=_prompt_kwargs,
    ),
    DeepEvalMetric(
        "exact_match",
        "ExactMatchMetric",
        "text.exact_match",
        _fs("output", "expected"),
        _fs("input"),
        uses_model=False,
        alias=False,
    ),
    DeepEvalMetric(
        "pattern_match",
        "PatternMatchMetric",
        "format.regex",
        _fs("output"),
        _fs("input"),
        uses_model=False,
        kwargs=_pattern_kwargs,
        alias=False,
    ),
    DeepEvalMetric(
        "task_completion",
        "TaskCompletionMetric",
        "agent.task_completion",
        _fs("input", "trajectory"),
        _fs("output"),
        trace=True,
    ),
    DeepEvalMetric(
        "tool_correctness",
        "ToolCorrectnessMetric",
        "agent.tool_trajectory_match",
        _fs("trajectory", "expected"),
        _fs("input"),
        kwargs=_tool_correctness_kwargs,
    ),
    DeepEvalMetric(
        "mcp_use",
        "MCPUseMetric",
        "agent.mcp_use",
        frozenset(),
        _fs("trajectory", "conversation", "input", "output"),
        mcp=True,
        variants=(
            DeepEvalMetric(
                "multi_turn_mcp_use",
                "MultiTurnMCPUseMetric",
                "agent.mcp_use",
                conversational=True,
                mcp=True,
            ),
        ),
    ),
    DeepEvalMetric(
        "argument_correctness",
        "ArgumentCorrectnessMetric",
        "agent.tool_parameter_accuracy",
        _fs("trajectory"),
        _fs("input", "output"),
    ),
    DeepEvalMetric(
        "misuse",
        "MisuseMetric",
        "safety.misuse",
        _fs("input", "output"),
        kwargs=_param("domain", "domain"),
        inverted=True,
    ),
    DeepEvalMetric(
        "non_advice",
        "NonAdviceMetric",
        "safety.non_advice",
        _fs("output"),
        _fs("input"),
        kwargs=_param("advice_types", "advice_types"),
    ),
    DeepEvalMetric(
        "pii_leakage", "PIILeakageMetric", "safety.pii_leakage", _fs("output"), _fs("input")
    ),
    DeepEvalMetric(
        "plan_adherence",
        "PlanAdherenceMetric",
        "agent.plan_adherence",
        _fs("trajectory"),
        _fs("input", "output"),
        trace=True,
    ),
    DeepEvalMetric(
        "plan_quality",
        "PlanQualityMetric",
        "agent.plan_quality",
        _fs("trajectory"),
        _fs("input", "output"),
        trace=True,
    ),
    DeepEvalMetric(
        "role_violation",
        "RoleViolationMetric",
        "safety.role_violation",
        _fs("output"),
        _fs("input"),
        kwargs=_param("role", "role"),
    ),
    DeepEvalMetric(
        "step_efficiency",
        "StepEfficiencyMetric",
        "agent.trajectory_efficiency",
        _fs("trajectory"),
        _fs("input", "output"),
        trace=True,
    ),
)

_CONV = _fs("conversation")
CONVERSATIONAL: tuple[DeepEvalMetric, ...] = (
    DeepEvalMetric(
        "conversation_completeness",
        "ConversationCompletenessMetric",
        "conversation.completeness",
        _CONV,
        conversational=True,
    ),
    DeepEvalMetric(
        "turn_relevancy",
        "TurnRelevancyMetric",
        "conversation.turn_relevancy",
        _CONV,
        conversational=True,
    ),
    DeepEvalMetric(
        "goal_accuracy",
        "GoalAccuracyMetric",
        "agent.goal_success",
        frozenset(),  # conversation or trajectory (checked in the handler)
        _fs("conversation", "trajectory", "expected", "input"),
        conversational=True,
    ),
    DeepEvalMetric(
        "knowledge_retention",
        "KnowledgeRetentionMetric",
        "conversation.knowledge_retention",
        _CONV,
        conversational=True,
    ),
    DeepEvalMetric(
        "role_adherence",
        "RoleAdherenceMetric",
        "conversation.role_adherence",
        _CONV,
        conversational=True,
    ),
    DeepEvalMetric(
        "tool_use",
        "ToolUseMetric",
        "agent.tool_selection",
        _fs("trajectory"),
        _fs("conversation", "input", "expected"),
        conversational=True,
        kwargs=_tool_use_kwargs,
    ),
    DeepEvalMetric(
        "topic_adherence",
        "TopicAdherenceMetric",
        "conversation.topic_adherence",
        _CONV,
        conversational=True,
        kwargs=_param("topics", "relevant_topics"),
    ),
    DeepEvalMetric(
        "mcp_task_completion",
        "MCPTaskCompletionMetric",
        "agent.mcp_task_completion",
        _fs("trajectory"),
        _fs("conversation", "input"),
        conversational=True,
        mcp=True,
    ),
    DeepEvalMetric(
        "turn_contextual_precision",
        "TurnContextualPrecisionMetric",
        "conversation.turn_context_precision",
        _CONV | _CTX | _fs("expected"),
        conversational=True,
        turn_contexts=True,
    ),
    DeepEvalMetric(
        "turn_contextual_recall",
        "TurnContextualRecallMetric",
        "conversation.turn_context_recall",
        _CONV | _CTX | _fs("expected"),
        conversational=True,
        turn_contexts=True,
    ),
    DeepEvalMetric(
        "turn_contextual_relevancy",
        "TurnContextualRelevancyMetric",
        "conversation.turn_context_relevancy",
        _CONV | _CTX,
        conversational=True,
        turn_contexts=True,
    ),
    DeepEvalMetric(
        "turn_faithfulness",
        "TurnFaithfulnessMetric",
        "conversation.turn_faithfulness",
        _CONV | _CTX,
        conversational=True,
        turn_contexts=True,
    ),
)

# Every DeepEval score name, including conversational variants (37 = 24 + 13).
ALL: dict[str, DeepEvalMetric] = {
    m.name: m for top in SINGLE_TURN + CONVERSATIONAL for m in (top, *top.variants)
}


# ------------------------------------------------------------------------------ helpers
def import_deepeval() -> Any:
    """Import ``deepeval`` (telemetry off) and return a namespace of the needed modules."""
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")
    top = require("deepeval", EXTRA)

    class _NS:
        __version__ = getattr(top, "__version__", None)
        metrics = require("deepeval.metrics", EXTRA)
        test_case = require("deepeval.test_case", EXTRA)

    return _NS


def schema_model(schema: Mapping[str, Any]) -> Any:
    """A pydantic model class validating JSON text against a JSON Schema (``expected_schema``)."""
    pydantic = require("pydantic", EXTRA)
    frozen = json.loads(json.dumps(dict(schema)))

    class JsonSchemaModel(pydantic.RootModel[Any]):  # type: ignore[misc, name-defined]
        @pydantic.model_validator(mode="after")
        def _check(self) -> Any:
            errors = validate_schema(self.root, frozen)
            if errors:
                raise ValueError("; ".join(errors[:5]))
            return self

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return dict(frozen)

    return JsonSchemaModel


def strict_mode(params: Mapping[str, Any]) -> bool | None:
    value = params.get("strict_mode")
    if value is not None and not isinstance(value, bool):
        raise ValueError("params.strict_mode must be a boolean")
    return value


def _tool_call(de: Any, name: str, args: Mapping[str, Any] | None, output: Any = None) -> Any:
    return de.test_case.ToolCall(name=name, input_parameters=dict(args or {}), output=output)


def available_tool_calls(case: EvalCase, de: Any) -> list[Any]:
    from ...adapters.traces.render import available_tools

    if case.trajectory is None:
        return []
    return [
        de.test_case.ToolCall(name=str(t.get("name", "")), description=t.get("description"))
        for t in available_tools(case.trajectory)
        if t.get("name")
    ]


def trace_dict(case: EvalCase) -> dict[str, Any]:
    """A DeepEval-style span tree (``name``/``type``/``input``/``output``/``children``)."""
    children: list[dict[str, Any]] = []
    if case.trajectory is not None:
        for step in case.trajectory.steps():
            if isinstance(step, ToolStep):
                result = step.result
                children.append(
                    {
                        "name": step.call.name,
                        "type": "tool",
                        "input": dict(step.call.arguments),
                        "output": None if result is None else (result.error or result.content),
                        "error": None if result is None else result.error,
                    }
                )
            elif isinstance(step, LlmStep):
                children.append(
                    {
                        "name": step.model or "llm",
                        "type": "llm",
                        "input": [dict(m) for m in step.messages],
                        "output": step.response,
                        "tools_called": [
                            {"name": c.name, "input_parameters": dict(c.arguments)}
                            for c in step.tool_calls
                        ],
                    }
                )
            elif isinstance(step, AgentStep):
                children.append(
                    {
                        "name": step.agent_id or "agent",
                        "type": "agent",
                        "description": step.system_prompt,
                        "input": step.user_prompt,
                        "output": step.response,
                    }
                )
    return {
        "name": "session",
        "type": "agent",
        "input": input_text(case),
        "output": output_text(case),
        "children": children,
    }


@dataclass(frozen=True)
class McpResult:
    """The MCP ``CallToolResult`` shape DeepEval reads (``structuredContent["result"]``)."""

    value: Any
    error: bool = False

    @property
    def structuredContent(self) -> dict[str, Any]:  # noqa: N802 - MCP field name
        return {"result": self.value}

    @property
    def isError(self) -> bool:  # noqa: N802 - MCP field name
        return self.error


def _mcp_fields(case: EvalCase, de: Any) -> tuple[list[Any], list[Any]]:
    """``(mcp_servers, mcp_tools_called)`` from the trajectory / conversation."""
    tc = de.test_case
    calls = session_mcp_calls(case.trajectory) or conversation_mcp_calls(case.conversation)
    declared = mcp_tools(case.trajectory)
    servers: dict[str, list[dict[str, Any]]] = {}
    for t in declared:
        target = mcp_target(str(t.get("name", "")))
        server = str(t.get("mcp_server") or (target[0] if target else "default"))
        tool = target[1] if target and not t.get("mcp_server") else str(t.get("name"))
        entry = {"name": tool, "description": t.get("description")}
        if t.get("input_schema") or t.get("parameters"):
            entry["inputSchema"] = t.get("input_schema") or t.get("parameters")
        servers.setdefault(server, []).append(entry)
    for c in calls:
        servers.setdefault(c.server, [])
    if not servers:
        raise StopScoring("missing_input", "no MCP tool calls or MCP tools found")
    mcp_servers = [
        tc.MCPServer(server_name=name, available_tools=tools or None)
        for name, tools in servers.items()
    ]
    called = [
        tc.MCPToolCall(
            name=c.tool,
            args=dict(c.arguments),
            result=mcp_result(c.error if c.error else c.result, error=c.error is not None),
        )
        for c in calls
    ]
    return mcp_servers, called


def mcp_result(value: Any, *, error: bool = False) -> Any:
    """``mcp.types.CallToolResult`` when ``mcp`` is installed, else a shape-compatible shim."""
    if not available("mcp"):
        return McpResult(value, error)
    types = require("mcp.types", EXTRA)
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent={"result": value},
        isError=error,
    )


def llm_test_case(m: DeepEvalMetric, case: EvalCase, de: Any) -> Any:
    kwargs: dict[str, Any] = {"input": input_text(case), "actual_output": output_text(case)}
    reference = reference_text(case.expected)
    if reference is not None and m.metric_id != "agent.tool_trajectory_match":
        kwargs["expected_output"] = reference
    if case.contexts:
        kwargs["context"] = context_texts(case)
        kwargs["retrieval_context"] = context_texts(case)
    called = tool_call_outputs(case)
    if called:
        kwargs["tools_called"] = [
            _tool_call(de, c.name, c.arguments, as_text(o) if o is not None else None)
            for c, o in called
        ]
    if m.metric_id == "agent.tool_trajectory_match":
        kwargs["expected_tools"] = [
            _tool_call(de, c.name, c.arguments) for c in expected_calls(case.expected)
        ]
        kwargs.setdefault("tools_called", [])
    if m.mcp:
        kwargs["mcp_servers"], kwargs["mcp_tools_called"] = _mcp_fields(case, de)
    test_case = de.test_case.LLMTestCase(**kwargs)
    if m.trace:
        test_case._trace_dict = trace_dict(case)
    return test_case


def deepeval_turns(case: EvalCase, de: Any, *, contexts: list[str] | None = None) -> list[Any]:
    """DeepEval ``Turn``s; tool results are merged into the preceding assistant turn's calls."""
    turns: list[dict[str, Any]] = []
    for t in case_turns(case):
        if t["role"] == "tool":
            prev = turns[-1] if turns and turns[-1]["role"] == "assistant" else None
            if prev is None or not prev["calls"]:
                continue
            calls = prev["calls"]
            target = next(
                (c for c in calls if t["tool_call_id"] and c["id"] == t["tool_call_id"]), None
            )
            if target is None:
                target = next((c for c in calls if c["output"] is None), calls[-1])
            target["output"] = t["content"]
            continue
        calls = [
            {"id": c.id, "name": c.name, "args": dict(c.arguments), "output": None}
            for c in t["tool_calls"]
        ]
        if (
            t["role"] == "assistant"
            and turns
            and turns[-1]["role"] == "assistant"
            and not t["content"]
        ):
            turns[-1]["calls"].extend(calls)  # tool-call-only turn continues the assistant turn
            continue
        turns.append({"role": t["role"], "content": t["content"], "calls": calls})
    out = []
    for t in turns:
        kwargs: dict[str, Any] = {"role": t["role"], "content": t["content"] or ""}
        if t["calls"]:
            kwargs["tools_called"] = [
                _tool_call(de, c["name"], c["args"], c["output"]) for c in t["calls"]
            ]
        if contexts and t["role"] == "assistant":
            kwargs["retrieval_context"] = list(contexts)
        out.append(de.test_case.Turn(**kwargs))
    return out


def conversational_test_case(
    m: DeepEvalMetric, case: EvalCase, params: Mapping[str, Any], de: Any
) -> Any:
    turns = deepeval_turns(case, de, contexts=context_texts(case) if m.turn_contexts else None)
    if not turns:
        raise StopScoring("missing_input", "missing required input(s): conversation or trajectory")
    kwargs: dict[str, Any] = {"turns": turns}
    if case.input is not None:
        kwargs["scenario"] = input_text(case)
    reference = reference_text(case.expected)
    if reference:
        kwargs["expected_outcome"] = reference
    if params.get("role"):
        kwargs["chatbot_role"] = params["role"]
    if case.contexts:
        kwargs["context"] = context_texts(case)
    if m.mcp:
        kwargs["mcp_servers"], called = _mcp_fields(case, de)
        if called:
            for turn in reversed(turns):
                if turn.role == "assistant":
                    turn.mcp_tools_called = called
                    break
    return de.test_case.ConversationalTestCase(**kwargs)


def _select(m: DeepEvalMetric, case: EvalCase) -> DeepEvalMetric:
    """Pick the conversational variant (``multi_turn_mcp_use``) when the case is a conversation."""
    if m.variants and len(case.conversation) >= 2:
        return m.variants[0]
    return m


def _threshold(m: DeepEvalMetric, ctx: MetricContext, metric: Any, de: Any = None) -> float:
    """DeepEval's own threshold (only used by ``strict_mode``), on DeepEval's polarity."""
    ours = ctx.config.threshold
    if ours is None:
        ours = metric.spec.default_threshold if metric.spec.default_threshold is not None else 0.5
    return 1.0 - ours if m.inverted and not passes_high(deepeval_version(de)) else ours


def build_metric(
    m: DeepEvalMetric, case: EvalCase, ctx: MetricContext, metric: Any, de: Any
) -> Any:
    cls = getattr(de.metrics, m.cls)
    kwargs: dict[str, Any] = {"threshold": _threshold(m, ctx, metric, de)}
    if m.uses_model:
        judge = LimitedJudge(ctx) if ctx.judge is not None else None
        kwargs["model"] = deepeval_judge_model(judge, m.metric_id, m.name)
        kwargs["async_mode"] = True
        kwargs["include_reason"] = judge is not None
        strict = strict_mode(ctx.params)
        if strict is not None:
            kwargs["strict_mode"] = strict
    if m.kwargs is not None:
        kwargs.update(m.kwargs(ctx.params, case, de))
    return cls(**kwargs)


# DeepEval 4.2 flipped these metrics to "1 is a pass" (``warn_score_direction_flipped``);
# before, they scored the share of violations with ``threshold`` as a maximum.
FLIPPED_IN = (4, 2)


def deepeval_version(de: Any = None) -> str | None:
    """The DeepEval version: the module's ``__version__``, else the installed distribution's."""
    version = getattr(de, "__version__", None)
    if isinstance(version, str):
        return version
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as dist_version

    try:
        return dist_version("deepeval")
    except PackageNotFoundError:
        return None


def passes_high(version: str | None) -> bool:
    """Whether DeepEval ``version`` scores bias / toxicity / hallucination / misuse as "1 is a
    pass" (>= 4.2; an unknown version is taken as current)."""
    if not version:
        return True
    parts = []
    for piece in version.split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts) >= FLIPPED_IN


def violation_share(score: float, version: str | None) -> float:
    """An inverted metric's DeepEval score as the share of violations (higher is worse)."""
    return 1.0 - score if passes_high(version) else score


def _needs_judge(m: DeepEvalMetric) -> bool:
    # json_correctness only uses the model for its reason; tool_correctness only with
    # available_tools (not passed). Both run without a judge.
    return m.uses_model and m.name not in ("json_correctness", "tool_correctness")


def _handler(top: DeepEvalMetric) -> Callable[..., Any]:
    async def handle(metric: Any, case: EvalCase, ctx: MetricContext) -> Outcome:
        m = _select(top, case)
        if ctx.judge is None and _needs_judge(m):
            raise SuiteError(f"{m.metric_id}: vendor method 'deepeval' needs a judge model")
        de = import_deepeval()
        if m.mcp and not m.conversational:
            require("mcp", EXTRA)  # DeepEval's single-turn MCPUseMetric needs mcp.types
        vm = build_metric(m, case, ctx, metric, de)
        if m.conversational:
            test_case = conversational_test_case(m, case, ctx.params, de)
        else:
            test_case = llm_test_case(m, case, de)
        value = await vm.a_measure(test_case, _show_indicator=False)
        if value is None:
            value = getattr(vm, "score", None)
        if m.inverted and value is not None:
            value = violation_share(float(value), deepeval_version(de))
        provenance = judge_provenance(ctx.judge) if ctx.judge is not None and m.uses_model else {}
        details: dict[str, Any] = {}
        if getattr(vm, "success", None) is not None:
            details["vendor_success"] = bool(vm.success)
        return vendor_outcome(
            value,
            vendor=VENDOR,
            vendor_metric=m.name,
            reason=getattr(vm, "reason", None),
            provenance=provenance,
            **details,
        )

    return handle


def method_spec(m: DeepEvalMetric) -> MethodSpec:
    return MethodSpec(
        "deepeval",
        Kind.VENDOR,
        required_inputs=m.required,
        optional_inputs=m.optional,
        inverted=m.inverted,
        extra=EXTRA,
        extra_module="deepeval",
    )


load_builtins()  # canonical metrics first (re-entrant during auto-discovery)
for _top in SINGLE_TURN + CONVERSATIONAL:
    register_method(
        _top.metric_id,
        method_spec(_top),
        _handler(_top),
        params={"strict_mode": {"type": "boolean"}},
    )
    for _m in (_top, *_top.variants):
        if _m.alias:
            add_alias(f"deepeval.{_m.name}", _m.metric_id, "deepeval")
