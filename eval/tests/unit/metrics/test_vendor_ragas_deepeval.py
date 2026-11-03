"""RAGAS / DeepEval vendor methods: input and result mapping against fake vendor modules.

The real libraries are never imported: ``ragas`` / ``deepeval`` / ``langchain_*`` are replaced by
small recording fakes injected into ``sys.modules``.
"""

from __future__ import annotations

import enum
import math
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from bnbagent_eval._optional import available
from bnbagent_eval.engine.resolve import resolve_suite
from bnbagent_eval.exceptions import MissingExtraError, SuiteError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import Kind, MetricContext
from bnbagent_eval.metrics.vendors import _vendor_llm
from bnbagent_eval.metrics.vendors import deepeval as dv
from bnbagent_eval.metrics.vendors import ragas as rv
from bnbagent_eval.types import (
    AgentStep,
    Context,
    EvalCase,
    JudgeConfig,
    Message,
    MetricConfig,
    Session,
    Suite,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)
from bnbagent_eval.types.judge import DEFAULT_EMBEDDING_MODELS


# ----------------------------------------------------------------------------- fakes
class _Rec:
    """Records constructor kwargs as attributes."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.__dict__.update(kwargs)


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


@dataclass
class Recorder:
    value: Any = 0.8
    reason: str | None = "because"
    instances: list[Any] | None = None

    def __post_init__(self) -> None:
        self.instances = []

    @property
    def last(self) -> Any:
        return self.instances[-1]


@pytest.fixture
def fake_ragas(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()

    class _Metric(_Rec):
        llm = None

        def init(self, run_config: Any) -> None:
            self.run_config = run_config

        async def single_turn_ascore(self, sample: Any) -> Any:
            self.sample = sample
            return rec.value

        async def multi_turn_ascore(self, sample: Any) -> Any:
            self.sample = sample
            return rec.value

    def metric_cls(name: str, *, embeddings: bool = False) -> type:
        def __init__(self: Any, **kwargs: Any) -> None:  # noqa: N807
            _Rec.__init__(self, **kwargs)
            if embeddings:
                self.embeddings = None
            rec.instances.append(self)

        return type(name, (_Metric,), {"__init__": __init__})

    class _NoLlm(_Rec):
        def init(self, run_config: Any) -> None:
            pass

        async def multi_turn_ascore(self, sample: Any) -> Any:
            self.sample = sample
            return rec.value

    class ToolCallAccuracy(_NoLlm):
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("strict_order", True)
            super().__init__(**kwargs)
            rec.instances.append(self)

    metrics = _module(
        "ragas.metrics",
        AnswerRelevancy=metric_cls("AnswerRelevancy", embeddings=True),
        AnswerAccuracy=metric_cls("AnswerAccuracy"),
        ResponseGroundedness=metric_cls("ResponseGroundedness"),
        Faithfulness=metric_cls("Faithfulness"),
        ContextRelevance=metric_cls("ContextRelevance"),
        LLMContextPrecisionWithoutReference=metric_cls("LLMContextPrecisionWithoutReference"),
        ContextRecall=metric_cls("ContextRecall"),
        TopicAdherenceScore=metric_cls("TopicAdherenceScore"),
        ToolCallAccuracy=ToolCallAccuracy,
        AgentGoalAccuracyWithReference=metric_cls("AgentGoalAccuracyWithReference"),
        AgentGoalAccuracyWithoutReference=metric_cls("AgentGoalAccuracyWithoutReference"),
    )

    class SingleTurnSample(_Rec):
        pass

    class MultiTurnSample(_Rec):
        pass

    class HumanMessage(_Rec):
        pass

    class AIMessage(_Rec):
        pass

    class ToolMessage(_Rec):
        pass

    class RagasToolCall(_Rec):
        pass

    class BaseRagasLLM:
        def __init__(self) -> None:
            pass

    class BaseRagasEmbeddings:
        def __init__(self) -> None:
            pass

    class LangchainLLMWrapper:
        def __init__(self, llm: Any) -> None:
            self.langchain_llm = llm

    class LangchainEmbeddingsWrapper:
        def __init__(self, embeddings: Any) -> None:
            self.embeddings = embeddings

    class Generation(_Rec):
        pass

    class LLMResult(_Rec):
        pass

    mods = {
        "ragas": _module("ragas"),
        "ragas.metrics": metrics,
        "ragas.dataset_schema": _module(
            "ragas.dataset_schema",
            SingleTurnSample=SingleTurnSample,
            MultiTurnSample=MultiTurnSample,
        ),
        "ragas.messages": _module(
            "ragas.messages",
            HumanMessage=HumanMessage,
            AIMessage=AIMessage,
            ToolMessage=ToolMessage,
            ToolCall=RagasToolCall,
        ),
        "ragas.run_config": _module("ragas.run_config", RunConfig=_Rec),
        "ragas.llms": _module("ragas.llms"),
        "ragas.llms.base": _module(
            "ragas.llms.base", BaseRagasLLM=BaseRagasLLM, LangchainLLMWrapper=LangchainLLMWrapper
        ),
        "ragas.embeddings": _module("ragas.embeddings"),
        "ragas.embeddings.base": _module(
            "ragas.embeddings.base",
            BaseRagasEmbeddings=BaseRagasEmbeddings,
            LangchainEmbeddingsWrapper=LangchainEmbeddingsWrapper,
        ),
        "langchain_core": _module("langchain_core"),
        "langchain_core.outputs": _module(
            "langchain_core.outputs", Generation=Generation, LLMResult=LLMResult
        ),
        "langchain_aws": _module(
            "langchain_aws", ChatBedrockConverse=_Rec, BedrockEmbeddings=_Rec
        ),
        "langchain_openai": _module("langchain_openai", ChatOpenAI=_Rec, OpenAIEmbeddings=_Rec),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return rec


@pytest.fixture
def fake_deepeval(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()

    class _Metric(_Rec):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            rec.instances.append(self)

        async def a_measure(self, test_case: Any, _show_indicator: bool = True) -> Any:
            self.test_case = test_case
            self.score = rec.value
            self.reason = rec.reason
            self.success = isinstance(rec.value, float) and rec.value >= self.threshold
            return rec.value

    names = {m.cls for m in dv.ALL.values()}
    metrics = _module("deepeval.metrics", **{n: type(n, (_Metric,), {}) for n in names})

    class ToolCallParams(enum.Enum):
        INPUT_PARAMETERS = "input_parameters"
        OUTPUT = "output"

    class DeepEvalBaseLLM:
        def __init__(self, model: str | None = None) -> None:
            self.name = model
            self.model = self.load_model()

    test_case = _module(
        "deepeval.test_case",
        LLMTestCase=type("LLMTestCase", (_Rec,), {}),
        ConversationalTestCase=type("ConversationalTestCase", (_Rec,), {}),
        Turn=type("Turn", (_Rec,), {}),
        ToolCall=type("ToolCall", (_Rec,), {}),
        ToolCallParams=ToolCallParams,
        MCPServer=type("MCPServer", (_Rec,), {}),
        MCPToolCall=type("MCPToolCall", (_Rec,), {}),
    )
    for name, mod in {
        "deepeval": _module("deepeval", __version__="3.9.9"),  # violation-share polarity
        "deepeval.metrics": metrics,
        "deepeval.test_case": test_case,
        "deepeval.models": _module("deepeval.models", DeepEvalBaseLLM=DeepEvalBaseLLM),
        "mcp": _module("mcp"),  # single-turn MCPUseMetric needs mcp (shim result without spec)
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return rec


# ---------------------------------------------------------------------------- helpers
async def run(
    metric_id: str,
    case: EvalCase,
    method: str,
    params: dict[str, Any] | None = None,
    judge: Any = "default",
    **config: Any,
):
    cls, _ = registry.resolve(metric_id)
    spec = registry.get_spec(metric_id)
    params = params or {}
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, method=method, params=params, **config),
        method=spec.method(method),
        params=params,
        judge=FakeJudge("ok") if judge == "default" else judge,
    )
    return await cls().evaluate(case, ctx)


def rag_case(**kw: Any) -> EvalCase:
    base: dict[str, Any] = {
        "id": "c",
        "input": "What is the capital of France?",
        "output": "Paris.",
        "contexts": (Context(text="Paris is the capital of France."), Context(text="Lyon")),
    }
    base.update(kw)
    return EvalCase(**base)


def tool_session() -> Session:
    return Session(
        id="s",
        traces=(
            Trace(
                id="t",
                steps=(
                    AgentStep(
                        id="a",
                        user_prompt="Weather in Paris?",
                        response="Sunny.",
                        available_tools=({"name": "weather", "description": "get weather"},),
                    ),
                    ToolStep(
                        id="1",
                        parent_id="a",
                        call=ToolCall(name="weather", arguments={"city": "Paris"}, id="c1"),
                        result=ToolResult(content="sunny", call_id="c1"),
                    ),
                ),
            ),
        ),
    )


CONVERSATION = (
    Message(role="system", content="be nice"),
    Message(role="user", content="Weather in Paris?"),
    Message(
        role="assistant",
        content="",
        tool_calls=(
            ToolCall(name="weather", arguments={"city": "Paris"}, id="c1"),
            ToolCall(name="clock", arguments={}, id="c2"),
        ),
    ),
    Message(role="tool", content="12:00", tool_call_id="c2"),
    Message(role="tool", content="sunny"),
    Message(role="assistant", content="Sunny at noon."),
    Message(role="tool", content="orphan"),
)


# ------------------------------------------------------------------------ registration
def test_ragas_methods_and_aliases_registered() -> None:
    for m in rv.METRICS:
        method = registry.get_spec(m.metric_id).method("ragas")
        assert method.kind is Kind.VENDOR and method.extra == "ragas"
        assert method.extra_module == "ragas" and not method.inverted
        for name in m.names:
            cls, resolved = registry.resolve(f"ragas.{name}")
            assert cls.spec.id == m.metric_id and resolved == "ragas"
    assert len(rv.METRICS) == 10
    assert registry.aliases()["ragas.agent_goal_accuracy_with_reference"] == (
        "agent.goal_success",
        "ragas",
    )


def test_deepeval_methods_and_aliases_registered() -> None:
    assert len(dv.ALL) == 37
    assert sum(not m.conversational for m in dv.ALL.values()) == 24
    assert sum(m.conversational for m in dv.ALL.values()) == 13
    tops = dv.SINGLE_TURN + dv.CONVERSATIONAL
    assert len({m.metric_id for m in tops}) == len(tops) == 36
    for m in tops:
        method = registry.get_spec(m.metric_id).method("deepeval")
        assert method.kind is Kind.VENDOR and method.extra == "deepeval"
        assert method.inverted is (m.name in ("hallucination", "bias", "toxicity", "misuse"))
    aliases = registry.aliases()
    for name, m in dv.ALL.items():
        if name in ("exact_match", "pattern_match", "json_correctness"):
            assert aliases[f"deepeval.{name}"] == (m.metric_id, None)  # rule methods kept
        else:
            assert aliases[f"deepeval.{name}"] == (m.metric_id, "deepeval")
    assert aliases["deepeval.multi_turn_mcp_use"] == ("agent.mcp_use", "deepeval")


def test_alias_resolution_in_suite() -> None:
    suite = Suite(name="s", version="1", metrics=(MetricConfig(id="ragas.faithfulness"),))
    if available("ragas"):
        resolved = resolve_suite(suite)
        assert resolved.metrics[0].method.name == "ragas"
    else:
        with pytest.raises(MissingExtraError, match=r"bnbagent-eval\[ragas\]"):
            resolve_suite(suite)


@pytest.mark.skipif(available("deepeval"), reason="deepeval is installed")
def test_missing_deepeval_extra_at_resolution() -> None:
    suite = Suite(
        name="s", version="1", metrics=(MetricConfig(id="safety.bias", method="deepeval"),)
    )
    with pytest.raises(MissingExtraError, match=r"bnbagent-eval\[deepeval\]"):
        resolve_suite(suite)


async def test_missing_vendor_module_at_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ragas", None)
    monkeypatch.setitem(sys.modules, "ragas.metrics", None)
    monkeypatch.setitem(sys.modules, "deepeval", None)
    res = await run("rag.faithfulness", rag_case(), "ragas")
    assert res.status == "error" and "bnbagent-eval[ragas]" in res.error.message
    res = await run("safety.bias", rag_case(), "deepeval")
    assert res.status == "error" and "bnbagent-eval[deepeval]" in res.error.message


# ------------------------------------------------------------------------------- RAGAS
async def test_ragas_single_turn_sample_and_result(fake_ragas: Recorder) -> None:
    case = rag_case(expected={"reference": "Paris", "other": 1})
    res = await run("rag.faithfulness", case, "ragas")
    assert res.status == "scored" and res.score == pytest.approx(0.8)
    assert res.details == {"raw": 0.8, "vendor": "ragas", "vendor_metric": "faithfulness"}
    vm = fake_ragas.last
    assert vm.kwargs == {"name": "faithfulness"}
    assert vm.sample.kwargs == {
        "user_input": "What is the capital of France?",
        "response": "Paris.",
        "retrieved_contexts": ["Paris is the capital of France.", "Lyon"],
        "reference": "Paris",
    }
    assert vm.llm is not None and res.provenance.judge["provider"] == "fake"


async def test_ragas_embeddings_and_metric_names(fake_ragas: Recorder) -> None:
    await run("quality.relevance", rag_case(), "ragas")
    assert type(fake_ragas.last).__name__ == "AnswerRelevancy"
    assert fake_ragas.last.embeddings is not None
    expected = {
        "quality.answer_correctness": "AnswerAccuracy",
        "rag.groundedness": "ResponseGroundedness",
        "rag.context_relevance": "ContextRelevance",
        "rag.context_precision": "LLMContextPrecisionWithoutReference",
        "rag.context_recall": "ContextRecall",
    }
    for metric_id, cls_name in expected.items():
        res = await run(metric_id, rag_case(expected="Paris"), "ragas")
        assert res.status == "scored", res
        assert type(fake_ragas.last).__name__ == cls_name


async def test_ragas_nan_is_error(fake_ragas: Recorder) -> None:
    fake_ragas.value = math.nan
    res = await run("rag.groundedness", rag_case(), "ragas")
    assert res.status == "error" and "NaN" in res.error.message and res.score is None


async def test_ragas_multi_turn_messages(fake_ragas: Recorder) -> None:
    case = EvalCase(id="c", conversation=CONVERSATION)
    res = await run("conversation.topic_adherence", case, "ragas", {"topics": ["weather"]})
    assert res.status == "scored" and res.details["mode"] == "f1"
    vm = fake_ragas.last
    assert vm.kwargs == {"name": "topic_adherence", "mode": "f1"}
    msgs = vm.sample.user_input
    kinds = [type(m).__name__ for m in msgs]
    # system dropped; both tool results follow the tool-calling AI message; orphan dropped
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "AIMessage"]
    assert [(c.name, c.args) for c in msgs[1].tool_calls] == [
        ("weather", {"city": "Paris"}),
        ("clock", {}),
    ]
    assert msgs[4].tool_calls is None
    assert vm.sample.reference_topics == ["weather"]


async def test_ragas_topic_mode(fake_ragas: Recorder) -> None:
    case = EvalCase(id="c", conversation=CONVERSATION)
    params = {"topics": ["weather"], "mode": "recall"}
    res = await run("conversation.topic_adherence", case, "ragas", params)
    assert fake_ragas.last.mode == "recall" and res.details["mode"] == "recall"
    params["mode"] = "bogus"
    res = await run("conversation.topic_adherence", case, "ragas", params)
    assert res.status == "error" and "mode" in res.error.message


async def test_ragas_tool_call_accuracy_from_trajectory(fake_ragas: Recorder) -> None:
    expected = [{"name": "weather", "arguments": {"city": "Paris"}}]
    case = EvalCase(id="c", trajectory=tool_session(), expected=expected)
    res = await run("agent.tool_trajectory_match", case, "ragas", judge=None)
    assert res.status == "scored" and res.details["strict_order"] is True  # no judge needed
    sample = fake_ragas.last.sample
    kinds = [type(m).__name__ for m in sample.user_input]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert sample.user_input[1].tool_calls[0].args == {"city": "Paris"}
    assert sample.user_input[2].content == "sunny"
    assert [(c.name, c.args) for c in sample.reference_tool_calls] == [
        ("weather", {"city": "Paris"})
    ]
    params = {"match_type": "any_order", "compare_args": "ignore"}
    res = await run("agent.tool_trajectory_match", case, "ragas", params, judge=None)
    assert fake_ragas.last.kwargs["strict_order"] is False
    assert fake_ragas.last.sample.reference_tool_calls[0].args == {}
    assert fake_ragas.last.sample.user_input[1].tool_calls[0].args == {}


async def test_ragas_goal_accuracy_reference_choice(fake_ragas: Recorder) -> None:
    case = EvalCase(id="c", trajectory=tool_session(), expected={"expected_outcome": "weather"})
    res = await run("agent.goal_success", case, "ragas")
    assert res.details["vendor_metric"] == "agent_goal_accuracy_with_reference"
    assert type(fake_ragas.last).__name__ == "AgentGoalAccuracyWithReference"
    assert fake_ragas.last.sample.reference == "weather"
    res = await run("agent.goal_success", EvalCase(id="c", trajectory=tool_session()), "ragas")
    assert res.details["vendor_metric"] == "agent_goal_accuracy"
    assert type(fake_ragas.last).__name__ == "AgentGoalAccuracyWithoutReference"
    res = await run("agent.goal_success", EvalCase(id="c"), "ragas")
    assert res.status == "skipped" and res.error.code == "missing_input"


async def test_ragas_needs_judge(fake_ragas: Recorder) -> None:
    res = await run("rag.faithfulness", rag_case(), "ragas", judge=None)
    assert res.status == "error" and "needs a judge" in res.error.message


async def test_ragas_missing_inputs_skip(fake_ragas: Recorder) -> None:
    res = await run("rag.faithfulness", rag_case(contexts=()), "ragas")
    assert res.status == "skipped" and res.error.code == "missing_input"


# ----------------------------------------------------------------------- RAGAS models
class _Judge:
    def __init__(self, **cfg: Any) -> None:
        self.config = JudgeConfig(**cfg)


def test_ragas_models_bedrock(fake_ragas: Recorder) -> None:
    judge = _Judge(
        provider="bedrock",
        model="amazon.nova-micro-v1:0",
        region="us-east-1",
        base_url="https://bedrock.example",
        extra={"embeddings_model": "emb-1"},
    )
    llm, emb = _vendor_llm.ragas_models(judge)
    assert llm.langchain_llm.kwargs == {
        "model": "amazon.nova-micro-v1:0",
        "max_tokens": 2048,
        "temperature": 0.0,
        "region_name": "us-east-1",
        "endpoint_url": "https://bedrock.example",
    }
    assert emb.embeddings.kwargs == {"model_id": "emb-1", "region_name": "us-east-1"}


def test_ragas_models_openai_judge_api_key(fake_ragas: Recorder, monkeypatch) -> None:
    """The RAGAS OpenAI path uses EVAL_JUDGE_API_KEY like our OpenAI judge."""
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "test-key")  # secret-scan: allow
    llm, emb = _vendor_llm.ragas_models(_Judge(provider="openai", model="m", temperature=None))
    assert llm.langchain_llm.kwargs["api_key"] == emb.embeddings.kwargs["api_key"] == "test-key"


def test_ragas_models_openai(fake_ragas: Recorder, monkeypatch) -> None:
    monkeypatch.delenv("EVAL_JUDGE_API_KEY", raising=False)
    judge = _Judge(provider="openai", model="m", temperature=None, base_url="http://127.0.0.1/v1")
    llm, emb = _vendor_llm.ragas_models(judge)
    assert llm.langchain_llm.kwargs == {
        "model": "m",
        "max_tokens": 2048,
        "base_url": "http://127.0.0.1/v1",
    }
    assert emb.embeddings.kwargs == {
        "model": DEFAULT_EMBEDDING_MODELS["openai"],
        "base_url": "http://127.0.0.1/v1",
    }


def test_ragas_models_missing_langchain(
    fake_ragas: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "langchain_aws", None)
    with pytest.raises(MissingExtraError, match=r"langchain_aws.*bnbagent-eval\[ragas\]"):
        _vendor_llm.ragas_models(_Judge(provider="bedrock", model="m"))


async def test_ragas_judge_llm_uses_judge_model(fake_ragas: Recorder) -> None:
    judge = FakeJudge(["first", "second"])
    llm, emb = _vendor_llm.ragas_models(judge, metric_id="rag.faithfulness")
    prompt = types.SimpleNamespace(to_string=lambda: "PROMPT")
    result = await llm.agenerate_text(prompt, n=2)
    assert [g.text for g in result.generations[0]] == ["first", "second"]
    assert judge.requests[0].user == "PROMPT"
    assert judge.requests[0].metadata["metric_id"] == "rag.faithfulness"
    assert llm.generate_text(prompt).generations[0][0].text == "second"
    assert await emb.aembed_documents(["ab", "c"]) == [[2.0], [1.0]]
    assert emb.embed_query("abc") == [3.0]


# ---------------------------------------------------------------------------- DeepEval
async def test_deepeval_single_turn_test_case(fake_deepeval: Recorder) -> None:
    case = rag_case(expected="Paris", trajectory=tool_session())
    res = await run("rag.context_precision", case, "deepeval")
    assert res.status == "scored" and res.score == pytest.approx(0.8)
    assert res.reason == "because"
    assert res.details["vendor"] == "deepeval"
    assert res.details["vendor_metric"] == "contextual_precision"
    vm = fake_deepeval.last
    assert type(vm).__name__ == "ContextualPrecisionMetric"
    assert vm.include_reason is True and vm.async_mode is True and vm.threshold == 0.6
    assert vm.model.get_model_name() == "fake:fake"
    tc = vm.test_case.kwargs
    assert tc["input"] == "What is the capital of France?" and tc["actual_output"] == "Paris."
    assert tc["expected_output"] == "Paris"
    assert tc["context"] == tc["retrieval_context"] == ["Paris is the capital of France.", "Lyon"]
    [called] = tc["tools_called"]
    assert (called.name, called.input_parameters, called.output) == (
        "weather",
        {"city": "Paris"},
        "sunny",
    )


async def test_deepeval_inverted_metrics(fake_deepeval: Recorder) -> None:
    fake_deepeval.value = 0.2
    for metric_id in ("rag.hallucination", "safety.bias", "safety.toxicity", "safety.misuse"):
        res = await run(
            metric_id, rag_case(), "deepeval", threshold=0.7, params=_misuse(metric_id)
        )
        assert res.score == pytest.approx(0.8) and res.details["raw"] == 0.2
        assert res.passed is True
        assert fake_deepeval.last.threshold == pytest.approx(0.3)  # DeepEval polarity


def _misuse(metric_id: str) -> dict[str, Any]:
    return {"domain": "finance"} if metric_id == "safety.misuse" else {}


async def test_deepeval_4_2_pass_polarity(fake_deepeval: Recorder, monkeypatch) -> None:
    """DeepEval >= 4.2 scores bias / toxicity / hallucination / misuse as "1 is a pass"; the
    bridge must not invert that score again."""
    import sys

    monkeypatch.setattr(sys.modules["deepeval"], "__version__", "4.3.2")
    fake_deepeval.value = 0.8  # 1 is a pass: 20% violations
    for metric_id in ("rag.hallucination", "safety.bias", "safety.toxicity", "safety.misuse"):
        res = await run(
            metric_id, rag_case(), "deepeval", threshold=0.7, params=_misuse(metric_id)
        )
        assert res.score == pytest.approx(0.8) and res.details["raw"] == pytest.approx(0.2)
        assert res.passed is True
        assert fake_deepeval.last.threshold == pytest.approx(0.7)  # minimum passing score
    assert dv.passes_high("4.2.0") and not dv.passes_high("4.1.10") and dv.passes_high(None)


async def test_deepeval_nan_and_none_are_errors(fake_deepeval: Recorder) -> None:
    fake_deepeval.value = math.nan
    res = await run("safety.bias", rag_case(), "deepeval")
    assert res.status == "error" and "NaN" in res.error.message
    fake_deepeval.value = None
    res = await run("safety.bias", rag_case(), "deepeval")
    assert res.status == "error"


async def test_deepeval_pass_through_params(fake_deepeval: Recorder) -> None:
    case = rag_case()
    cases = [
        ("safety.misuse", {"domain": "finance"}, {"domain": "finance"}),
        ("safety.non_advice", {"advice_types": ["financial"]}, {"advice_types": ["financial"]}),
        ("safety.role_violation", {"role": "helper"}, {"role": "helper"}),
        (
            "quality.instruction_following",
            {"instructions": ["Be brief", " "]},
            {"prompt_instructions": ["Be brief"]},
        ),
        (
            "format.regex",
            {"pattern": "P.*", "flags": ["IGNORECASE"]},
            {"pattern": "P.*", "ignore_case": True},
        ),
    ]
    for metric_id, params, expected in cases:
        res = await run(metric_id, case, "deepeval", params)
        assert res.status == "scored", res
        for k, v in expected.items():
            assert fake_deepeval.last.kwargs[k] == v
    conv = EvalCase(id="c", conversation=CONVERSATION)
    await run("conversation.topic_adherence", conv, "deepeval", {"topics": ["weather"]})
    assert fake_deepeval.last.relevant_topics == ["weather"]
    await run("safety.bias", case, "deepeval", {"strict_mode": True})
    assert fake_deepeval.last.strict_mode is True
    res = await run("safety.bias", case, "deepeval", {"strict_mode": "yes"})
    assert res.status == "error" and "strict_mode" in res.error.message
    res = await run("safety.misuse", case, "deepeval", {})
    assert res.status == "error" and "params.domain" in res.error.message
    res = await run("quality.instruction_following", case, "deepeval", {})
    assert res.status == "error" and "instructions" in res.error.message


async def test_deepeval_no_model_metrics(fake_deepeval: Recorder) -> None:
    res = await run("text.exact_match", rag_case(expected="Paris."), "deepeval", judge=None)
    assert res.status == "scored" and "model" not in fake_deepeval.last.kwargs
    assert fake_deepeval.last.threshold == 1.0
    res = await run("safety.bias", rag_case(), "deepeval", judge=None)
    assert res.status == "error" and "needs a judge" in res.error.message


async def test_deepeval_json_correctness(
    fake_deepeval: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dv, "schema_model", lambda schema: ("model-for", schema))
    case = EvalCase(id="c", output='{"a": 1}')
    res = await run("format.json_schema", case, "deepeval", {"schema": {"type": "object"}}, None)
    assert res.status == "scored"
    vm = fake_deepeval.last
    assert vm.expected_schema == ("model-for", {"type": "object"})
    assert vm.include_reason is False  # no judge: deterministic score, no reason


async def test_deepeval_tool_correctness_options(fake_deepeval: Recorder) -> None:
    expected = {"tool_calls": [{"name": "weather", "arguments": {"city": "Paris"}}]}
    case = EvalCase(id="c", input="q", trajectory=tool_session(), expected=expected)
    await run("agent.tool_trajectory_match", case, "deepeval")
    vm = fake_deepeval.last
    tcp = sys.modules["deepeval.test_case"].ToolCallParams
    assert vm.should_exact_match is True and vm.should_consider_ordering is True
    assert vm.evaluation_params == [tcp.INPUT_PARAMETERS]
    assert [t.name for t in vm.test_case.expected_tools] == ["weather"]
    assert "expected_output" not in vm.test_case.kwargs
    params = {"match_type": "any_order", "compare_args": "ignore"}
    await run("agent.tool_trajectory_match", case, "deepeval", params)
    vm = fake_deepeval.last
    assert vm.should_exact_match is False and vm.should_consider_ordering is False
    assert vm.evaluation_params == []
    await run("agent.tool_trajectory_match", case, "deepeval", {"match_type": "in_order"})
    assert fake_deepeval.last.should_exact_match is False
    assert fake_deepeval.last.should_consider_ordering is True


async def test_deepeval_conversational_turns(fake_deepeval: Recorder) -> None:
    case = EvalCase(
        id="c",
        input="Ask about the weather",
        conversation=CONVERSATION,
        contexts=(Context(text="Paris is sunny."),),
        expected="The user learns the weather",
    )
    res = await run("conversation.turn_faithfulness", case, "deepeval")
    assert res.status == "scored", res
    tc = fake_deepeval.last.test_case.kwargs
    assert tc["scenario"] == "Ask about the weather"
    assert tc["expected_outcome"] == "The user learns the weather"
    assert tc["context"] == ["Paris is sunny."]
    turns = tc["turns"]
    assert [(t.role, t.content) for t in turns] == [
        ("user", "Weather in Paris?"),
        ("assistant", ""),
        ("assistant", "Sunny at noon."),
    ]
    calls = turns[1].tools_called
    # "12:00" matched by tool_call_id; "sunny" goes to the first call without output
    assert [(c.name, c.output) for c in calls] == [("weather", "sunny"), ("clock", "12:00")]
    assert turns[2].retrieval_context == ["Paris is sunny."]
    assert not hasattr(turns[0], "retrieval_context")
    await run("conversation.role_adherence", case, "deepeval", {"role": "weather bot"})
    assert fake_deepeval.last.test_case.chatbot_role == "weather bot"


async def test_deepeval_tool_only_turn_merges_into_assistant_turn(
    fake_deepeval: Recorder,
) -> None:
    conv = (
        Message(role="user", content="hi"),
        Message(role="assistant", content="Let me check."),
        Message(role="assistant", tool_calls=(ToolCall(name="t", arguments={}),)),
        Message(role="tool", content="done"),
    )
    await run("conversation.completeness", EvalCase(id="c", conversation=conv), "deepeval")
    turns = fake_deepeval.last.test_case.turns
    assert [(t.role, t.content) for t in turns] == [("user", "hi"), ("assistant", "Let me check.")]
    assert [(c.name, c.output) for c in turns[1].tools_called] == [("t", "done")]


async def test_deepeval_trajectory_conversation_and_trace(fake_deepeval: Recorder) -> None:
    case = EvalCase(id="c", trajectory=tool_session())
    await run("agent.goal_success", case, "deepeval")
    turns = fake_deepeval.last.test_case.turns
    assert [t.role for t in turns] == ["user", "assistant", "assistant"]
    assert turns[1].tools_called[0].output == "sunny"
    await run("agent.plan_adherence", case, "deepeval")
    trace = fake_deepeval.last.test_case._trace_dict
    assert trace["input"] == "" and trace["output"] == "Sunny."
    assert [c["type"] for c in trace["children"]] == ["agent", "tool"]
    assert trace["children"][1]["output"] == "sunny"
    res = await run("agent.tool_selection", case, "deepeval")
    assert res.status == "scored"
    assert [t.name for t in fake_deepeval.last.available_tools] == ["weather"]


async def test_deepeval_mcp_variants(
    fake_deepeval: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(
        id="s",
        traces=(
            Trace(
                id="t",
                steps=(
                    AgentStep(
                        id="a",
                        user_prompt="q",
                        response="r",
                        available_tools=({"name": "mcp__w__forecast", "description": "f"},),
                    ),
                    ToolStep(
                        id="1",
                        call=ToolCall(name="mcp__w__forecast", arguments={"d": 1}),
                        result=ToolResult(content="rain"),
                    ),
                ),
            ),
        ),
    )
    res = await run("agent.mcp_use", EvalCase(id="c", trajectory=session), "deepeval")
    assert res.details["vendor_metric"] == "mcp_use"
    tc = fake_deepeval.last.test_case
    [server] = tc.mcp_servers
    assert server.server_name == "w" and server.available_tools[0]["name"] == "forecast"
    [called] = tc.mcp_tools_called
    assert called.name == "forecast" and called.result.structuredContent == {"result": "rain"}
    conv = (
        Message(role="user", content="q"),
        Message(role="assistant", content="r", tool_calls=(ToolCall(name="w.forecast"),)),
    )
    res = await run("agent.mcp_use", EvalCase(id="c", conversation=conv), "deepeval")
    assert res.details["vendor_metric"] == "multi_turn_mcp_use"
    assert type(fake_deepeval.last).__name__ == "MultiTurnMCPUseMetric"
    assert fake_deepeval.last.test_case.turns[-1].mcp_tools_called[0].name == "forecast"
    res = await run("agent.mcp_use", rag_case(), "deepeval")
    assert res.status == "skipped" and res.error.code == "missing_input"
    monkeypatch.setitem(sys.modules, "mcp", None)
    res = await run("agent.mcp_use", EvalCase(id="c", trajectory=session), "deepeval")
    assert res.status == "error" and "'mcp'" in res.error.message
    assert "bnbagent-eval[deepeval]" in res.error.message


# ------------------------------------------------------------------- DeepEval model
async def test_deepeval_judge_model_wrapper(fake_deepeval: Recorder) -> None:
    class Schema:
        @staticmethod
        def model_json_schema() -> dict[str, Any]:
            return {"type": "object", "properties": {"verdict": {"type": "string"}}}

        @staticmethod
        def model_validate(data: Any) -> Any:
            return ("validated", data)

    judge = FakeJudge(["plain text", '```json\n{"verdict": "yes"}\n```'])
    model = _vendor_llm.deepeval_judge_model(judge, "safety.bias", "bias")
    assert model.load_model() is judge and model.get_model_name() == "fake:fake"
    assert await model.a_generate("P1") == "plain text"
    assert await model.a_generate("P2", schema=Schema) == ("validated", {"verdict": "yes"})
    second = judge.requests[1]
    assert second.user == "P2" and second.output_schema == Schema.model_json_schema()
    assert second.metadata == {
        "metric_id": "safety.bias",
        "vendor": "deepeval",
        "vendor_metric": "bias",
    }
    structured = _vendor_llm.deepeval_judge_model(FakeJudge({"verdict": "no"}), "m", "x")
    assert structured.generate("P", schema=Schema) == ("validated", {"verdict": "no"})
    none = _vendor_llm.deepeval_judge_model(None, "m", "x")
    assert none.get_model_name() == "none"
    with pytest.raises(SuiteError, match="needs a judge"):
        await none.a_generate("P")


def test_deepeval_wrapper_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "deepeval", None)
    monkeypatch.setitem(sys.modules, "deepeval.models", None)
    with pytest.raises(MissingExtraError, match=r"bnbagent-eval\[deepeval\]"):
        _vendor_llm.deepeval_judge_model(FakeJudge("x"))


# ------------------------------------------------------------------------ case views
def test_reference_text_rules() -> None:
    assert _vendor_llm.reference_text(None) is None
    assert _vendor_llm.reference_text("a") == "a"
    assert _vendor_llm.reference_text({"expected_outcome": "o"}) == "o"
    assert _vendor_llm.reference_text({"tool_calls": []}) is None
    assert _vendor_llm.reference_text(["a", "b"]) == "a\nb"
    assert _vendor_llm.reference_text({"x": 1}) == '{"x": 1}'


def test_input_and_output_text() -> None:
    case = EvalCase(id="c", input={"text": "q", "other": 1}, trajectory=tool_session())
    assert _vendor_llm.input_text(case) == "q"
    assert _vendor_llm.output_text(case) == "Sunny."
    assert _vendor_llm.case_turns(EvalCase(id="c", input="q", output="a")) == [
        {"role": "user", "content": "q", "tool_calls": [], "tool_call_id": None},
        {"role": "assistant", "content": "a", "tool_calls": [], "tool_call_id": None},
    ]


def test_vendor_outcome_nan() -> None:
    out = _vendor_llm.vendor_outcome(float("nan"), vendor="v", vendor_metric="m")
    assert out.status == "error" and out.details == {"vendor": "v", "vendor_metric": "m"}
    out = _vendor_llm.vendor_outcome(1, vendor="v", vendor_metric="m", reason="r", k=2)
    assert out.score == 1.0 and out.reason == "r" and out.details["k"] == 2


def test_vendor_params_accepted_by_metric_schemas() -> None:
    from bnbagent_eval.metrics import registry

    assert not registry.get_spec("safety.bias").validate_params({"strict_mode": True})
    topic = registry.get_spec("conversation.topic_adherence")
    assert not topic.validate_params({"mode": "f1", "topics": ["fees"]})
    assert topic.validate_params({"mode": "bogus", "topics": ["fees"]})
