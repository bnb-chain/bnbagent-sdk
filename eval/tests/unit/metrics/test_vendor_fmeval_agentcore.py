"""FMEval and AgentCore vendor methods with fake vendor modules (no fmeval, no AWS calls)."""

from __future__ import annotations

import importlib.machinery
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from bnbagent_eval._optional import available
from bnbagent_eval.adapters.traces._common import short_hash
from bnbagent_eval.adapters.traces.agentcore_adot import AgentCoreAdotAdapter
from bnbagent_eval.engine import Runner, resolve_suite
from bnbagent_eval.exceptions import MissingExtraError
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import Kind, MetricContext
from bnbagent_eval.metrics.vendors import _agentcore_spans as acs
from bnbagent_eval.metrics.vendors import agentcore, fmeval
from bnbagent_eval.types import (
    AgentStep,
    DelegationStep,
    EvalCase,
    EvalRequest,
    LlmStep,
    MetricConfig,
    MetricResult,
    Session,
    Suite,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
    TxStep,
    Usage,
)


async def run(
    metric_id: str,
    case: EvalCase,
    params: dict[str, Any] | None = None,
    method: str | None = None,
) -> MetricResult:
    """Evaluate ``metric_id`` (an id or alias) with a vendor method, as the engine does."""
    cls, alias_method = registry.resolve(metric_id)
    spec = registry.effective_spec(cls)
    params = params or {}
    cfg = MetricConfig(id=metric_id, params=params)
    ctx = MetricContext(config=cfg, method=spec.method(method or alias_method), params=params)
    return await cls().evaluate(case, ctx)


def _module(name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    module.__dict__.update(attrs)
    return module


# =========================================================================== FMEval


class FakeConfig:
    def __init__(self, **kwargs: Any):
        self.kwargs = kwargs

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other.kwargs == self.kwargs  # type: ignore[attr-defined]


class FakeFmeval:
    """``fmeval`` stand-in: records the algorithm, its config and the JSONL dataset per call."""

    def __init__(self) -> None:
        self.scores: dict[str, dict[str, float | None]] = {}
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        names = {cls for _, cls in fmeval.CONFIGS.values()}
        self.configs = {name: type(name, (FakeConfig,), {}) for name in names}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self

        class DataConfig:
            def __init__(self, **kwargs: Any):
                self.__dict__.update(kwargs)

        class Algorithm:
            def __init__(self, name: str, config: Any):
                self.name, self.config = name, config

            def evaluate(self, *, dataset_config: Any) -> list[Any]:
                rows = Path(dataset_config.dataset_uri).read_text().splitlines()
                fake.calls.append(
                    {
                        "algorithm": self.name,
                        "config": self.config,
                        "rows": [json.loads(r) for r in rows],
                        "data_config": dict(dataset_config.__dict__),
                    }
                )
                if fake.error is not None:
                    raise fake.error
                scores = [
                    types.SimpleNamespace(name=k, value=v)
                    for k, v in fake.scores.get(self.name, {}).items()
                ]
                return [types.SimpleNamespace(dataset_scores=scores)]

        def get_eval_algorithm(name: str, config: Any = None) -> Algorithm:
            return Algorithm(name, config)

        modules = {
            "fmeval": _module("fmeval"),
            "fmeval.eval": _module("fmeval.eval", get_eval_algorithm=get_eval_algorithm),
            "fmeval.data_loaders": _module("fmeval.data_loaders"),
            "fmeval.data_loaders.data_config": _module(
                "fmeval.data_loaders.data_config", DataConfig=DataConfig
            ),
            "fmeval.eval_algorithms": _module("fmeval.eval_algorithms"),
        }
        for module_name, cls_name in fmeval.CONFIGS.values():
            name = f"fmeval.eval_algorithms.{module_name}"
            modules.setdefault(name, _module(name)).__dict__[cls_name] = self.configs[cls_name]
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)

    def config(self, cls_name: str, **kwargs: Any) -> FakeConfig:
        return self.configs[cls_name](**kwargs)


@pytest.fixture
def fm(monkeypatch: pytest.MonkeyPatch) -> FakeFmeval:
    fake = FakeFmeval()
    fake.install(monkeypatch)
    return fake


QA = {
    "f1_score": 0.75,
    "exact_match_score": 0.0,
    "quasi_exact_match_score": 1.0,
    "precision_over_words": 0.6,
    "recall_over_words": 1.0,
}
TOX = {
    "toxicity": 0.2,
    "severe_toxicity": 0.01,
    "obscene": 0.02,
    "threat": 0.03,
    "insult": 0.4,
    "identity_attack": 0.05,
    "sexual_explicit": 0.06,
}


async def test_fmeval_jsonl_record_and_data_config(fm: FakeFmeval) -> None:
    fm.scores["qa_accuracy"] = QA
    case = EvalCase(id="1", input="Capital of France?", output="Paris", expected=["Paris", "Lyon"])
    res = await run("text.token_f1", case, method="fmeval")
    (call,) = fm.calls
    assert call["algorithm"] == "qa_accuracy" and call["config"] is None
    assert call["rows"] == [
        {
            "model_input": "Capital of France?",
            "model_output": "Paris",
            "target_output": "Paris<OR>Lyon",
        }
    ]
    assert call["data_config"] == {
        "dataset_name": "single_testcase",
        "dataset_uri": call["data_config"]["dataset_uri"],
        "dataset_mime_type": "application/jsonlines",
        "model_input_location": "model_input",
        "model_output_location": "model_output",
        "target_output_location": "target_output",
    }
    assert not Path(call["data_config"]["dataset_uri"]).exists()  # temp dir removed
    assert res.status == "scored" and res.score == 0.75 and res.method == "fmeval"
    assert res.details["precision"] == 0.6 and res.details["recall"] == 1.0
    assert res.details["fmeval_scores"] == QA and res.details["score_name"] == "f1_score"


def test_fmeval_record_without_target_or_input() -> None:
    assert fmeval.record(EvalCase(id="1", output="hi"), None) == {
        "model_input": "",
        "model_output": "hi",
    }


@pytest.mark.parametrize(
    ("metric_id", "score"),
    [("text.exact_match", 0.0), ("text.quasi_exact_match", 1.0), ("text.token_f1", 0.75)],
)
async def test_fmeval_qa_accuracy_sub_scores(fm: FakeFmeval, metric_id: str, score: float) -> None:
    fm.scores["qa_accuracy"] = QA
    case = EvalCase(id="1", output="The Paris", expected="Paris")
    res = await run(metric_id, case, method="fmeval")
    assert res.score == score and res.details["fmeval_algorithm"] == "qa_accuracy"


@pytest.mark.parametrize(
    ("alias", "metric_id", "score"),
    [
        ("fmeval.qa_accuracy", "text.token_f1", 0.75),
        ("fmeval.qa_accuracy.recall_over_words", "text.token_f1", 1.0),
        ("fmeval.qa_accuracy.precision_over_words", "text.token_f1", 0.6),
        ("fmeval.qa_accuracy.exact_match_score", "text.exact_match", 0.0),
        ("fmeval.qa_accuracy.quasi_exact_match_score", "text.quasi_exact_match", 1.0),
    ],
)
async def test_fmeval_alias_selects_primary_score(
    fm: FakeFmeval, alias: str, metric_id: str, score: float
) -> None:
    fm.scores["qa_accuracy"] = QA
    res = await run(alias, EvalCase(id="1", output="Paris", expected="Paris"))
    assert res.metric_id == metric_id and res.method == "fmeval" and res.score == score


async def test_fmeval_toxicity_inverted_with_sub_scores(fm: FakeFmeval) -> None:
    fm.scores["toxicity"] = TOX
    res = await run("safety.toxicity", EvalCase(id="1", output="you fool"), method="fmeval")
    assert res.score == pytest.approx(0.8) and res.details["raw"] == 0.2
    assert res.details["insult"] == 0.4 and res.details["sexual_explicit"] == 0.06
    (call,) = fm.calls
    assert call["algorithm"] == "toxicity" and call["config"] is None
    assert call["rows"] == [{"model_input": "", "model_output": "you fool"}]  # no target
    method = registry.get_spec("safety.toxicity").method("fmeval")
    assert method.inverted and method.kind is Kind.VENDOR and method.extra == "fmeval"
    assert method.required_inputs == {"output"}


async def test_fmeval_toxicity_variant_and_model_type(fm: FakeFmeval) -> None:
    fm.scores["qa_toxicity"] = {"toxicity": 0.9}
    res = await run(
        "safety.toxicity",
        EvalCase(id="1", input="q", output="a"),
        {"variant": "qa_toxicity", "model_type": "toxigen"},
        method="fmeval",
    )
    assert res.score == pytest.approx(0.1)
    assert fm.calls[0]["algorithm"] == "qa_toxicity"
    assert fm.calls[0]["rows"][0]["model_input"] == "q"
    assert fm.calls[0]["config"] == fm.config("ToxicityConfig", model_type="toxigen")


@pytest.mark.parametrize(
    ("alias", "algorithm", "raw"),
    [
        ("fmeval.summarization_toxicity", "summarization_toxicity", 0.2),
        ("fmeval.toxicity.insult", "toxicity", 0.4),
        ("fmeval.qa_toxicity.threat", "qa_toxicity", 0.03),
    ],
)
async def test_fmeval_toxicity_aliases(
    fm: FakeFmeval, alias: str, algorithm: str, raw: float
) -> None:
    fm.scores[algorithm] = TOX
    res = await run(alias, EvalCase(id="1", output="x"))
    assert res.metric_id == "safety.toxicity" and fm.calls[0]["algorithm"] == algorithm
    assert res.details["raw"] == raw and res.score == pytest.approx(1 - raw)


async def test_fmeval_factual_knowledge_config_and_quasi(fm: FakeFmeval) -> None:
    fm.scores["factual_knowledge"] = {
        "factual_knowledge": 0.0,
        "factual_knowledge_quasi_exact": 1.0,
    }
    case = EvalCase(id="1", output="It is paris.", expected=["Paris", "France"])
    params = {"delimiter": "|", "mode": "all", "quasi": True}
    res = await run("text.contains", case, params, method="fmeval")
    assert res.score == 1.0 and res.details["score_name"] == "factual_knowledge_quasi_exact"
    call = fm.calls[0]
    assert call["rows"][0]["target_output"] == "Paris|France"
    assert call["config"] == fm.config(
        "FactualKnowledgeConfig", target_output_delimiter="|", logical_operator="AND"
    )
    res = await run("fmeval.factual_knowledge", case)
    assert res.score == 0.0 and fm.calls[1]["config"] is None
    assert fm.calls[1]["rows"][0]["target_output"] == "Paris<OR>France"
    res = await run("fmeval.factual_knowledge.factual_knowledge_quasi_exact", case)
    assert res.score == 1.0


async def test_fmeval_summarization_accuracy(fm: FakeFmeval) -> None:
    fm.scores["summarization_accuracy"] = {"rouge": 0.5, "meteor": 0.7, "bertscore": 0.9}
    case = EvalCase(id="1", output="a cat sat", expected="the cat sat")
    res = await run("text.rouge", case, {"variant": "rouge1"}, method="fmeval")
    assert res.score == 0.5
    assert fm.calls[0]["config"] == fm.config(
        "SummarizationAccuracyConfig", rouge_type="rouge1", use_stemmer_for_rouge=True
    )
    assert fm.calls[0]["rows"][0]["target_output"] == "the cat sat"
    res = await run("fmeval.summarization_accuracy.meteor", case)
    assert res.metric_id == "text.meteor" and res.score == 0.7 and fm.calls[1]["config"] is None


def test_fmeval_bertscore_registered_only_when_metric_exists() -> None:
    has_metric = "text.bertscore" in registry._METRICS
    assert ("text.bertscore" in fmeval.REGISTERED) == has_metric
    assert ("fmeval.summarization_accuracy.bertscore" in registry.aliases()) == has_metric
    if has_metric:
        assert "fmeval" in registry.get_spec("text.bertscore").method_names
    bert = next(t for t in fmeval.TARGETS if t.metric_id == "text.bertscore")
    assert bert.config({"model": "deberta-xlarge-mnli"}) == {
        "model_type_for_bertscore": "microsoft/deberta-xlarge-mnli"
    }


async def test_fmeval_classification_accuracy(fm: FakeFmeval) -> None:
    fm.scores["classification_accuracy"] = {
        "classification_accuracy_score": 1.0,
        "balanced_accuracy_score": 0.5,
        "precision_score": 0.25,
        "recall_score": 0.75,
    }
    res = await run(
        "text.classification_accuracy",
        EvalCase(id="1", output="positive", expected="positive"),
        {"fmeval_config": {"valid_labels": ["positive", "negative"]}},
        method="fmeval",
    )
    assert res.score == 1.0
    assert res.details["balanced_accuracy"] == 0.5 and res.details["recall"] == 0.75
    assert fm.calls[0]["config"] == fm.config(
        "ClassificationAccuracyConfig", valid_labels=["positive", "negative"]
    )


async def test_fmeval_error_mapping(fm: FakeFmeval) -> None:
    case = EvalCase(id="1", output="Paris", expected="Paris")
    fm.scores["qa_accuracy"] = {}
    res = await run("text.exact_match", case, method="fmeval")
    assert res.status == "error" and res.error.code == "metric_error"
    assert "no scores" in res.error.message
    fm.scores["qa_accuracy"] = {"f1_score": 1.0}
    res = await run("text.exact_match", case, method="fmeval")
    assert res.error.code == "metric_error" and "exact_match_score" in res.error.message
    fm.scores["qa_accuracy"] = {"exact_match_score": float("nan")}
    res = await run("text.exact_match", case, method="fmeval")
    assert res.status == "error" and res.error.code == "vendor_nan" and res.score is None
    fm.scores["qa_accuracy"] = {"exact_match_score": None}
    res = await run("text.exact_match", case, method="fmeval")
    assert res.error.code == "vendor_nan"
    fm.error = RuntimeError("ray failed")
    res = await run("text.exact_match", case, method="fmeval")
    assert res.error.code == "metric_error" and "ray failed" in res.error.message
    res = await run("text.exact_match", EvalCase(id="1", output="Paris"), method="fmeval")
    assert res.status == "skipped" and res.error.code == "missing_input"


def test_fmeval_aliases_and_methods() -> None:
    aliases = registry.aliases()
    expected = {
        "fmeval.factual_knowledge": "text.contains",
        "fmeval.toxicity": "safety.toxicity",
        "fmeval.qa_toxicity": "safety.toxicity",
        "fmeval.summarization_toxicity": "safety.toxicity",
        "fmeval.qa_accuracy": "text.token_f1",
        "fmeval.summarization_accuracy": "text.rouge",
        "fmeval.classification_accuracy": "text.classification_accuracy",
        "fmeval.qa_accuracy.f1_score": "text.token_f1",
        "fmeval.summarization_accuracy.rouge": "text.rouge",
        "fmeval.classification_accuracy.balanced_accuracy_score": "text.classification_accuracy",
        "fmeval.summarization_toxicity.identity_attack": "safety.toxicity",
    }
    for alias, metric_id in expected.items():
        assert aliases[alias] == (metric_id, "fmeval"), alias
    assert set(fmeval.REGISTERED) >= {
        "text.contains",
        "safety.toxicity",
        "text.exact_match",
        "text.quasi_exact_match",
        "text.token_f1",
        "text.rouge",
        "text.meteor",
        "text.classification_accuracy",
    }
    for metric_id in fmeval.REGISTERED:
        method = registry.get_spec(metric_id).method("fmeval")
        assert method.extra == "fmeval" and method.extra_module == "fmeval"


@pytest.mark.skipif(available("fmeval"), reason="fmeval is installed")
def test_fmeval_missing_extra_is_reported() -> None:
    suite = Suite(name="s", version="1", metrics=(MetricConfig(id="fmeval.qa_accuracy"),))
    with pytest.raises(MissingExtraError, match=r"\[fmeval\]"):
        resolve_suite(suite)


# =========================================================================== AgentCore


class FakeBoto3:
    """``boto3`` stand-in: ``client("bedrock-agentcore", region_name=...)``; records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.clients: list[tuple[str, str]] = []
        self.error: Exception | None = None
        self.response: dict[str, Any] = {
            "evaluationResults": [{"value": 0.8, "label": "Good", "explanation": "fine"}]
        }

    def client(self, service: str, region_name: str) -> Any:
        self.clients.append((service, region_name))
        owner = self

        class Client:
            def evaluate(self, **kwargs: Any) -> dict[str, Any]:
                owner.calls.append({"region": region_name, **kwargs})
                if owner.error is not None:
                    raise owner.error
                return owner.response

        return Client()


class ClientError(Exception):
    """botocore ``ClientError`` shape: ``exc.response["Error"]["Code"]``."""

    def __init__(self, code: str):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code, "Message": "nope"}}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> FakeBoto3:
    fake = FakeBoto3()
    monkeypatch.setitem(sys.modules, "boto3", _module("boto3", client=fake.client))
    monkeypatch.setattr(acs, "_client_cache", {})
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    return fake


TRACE_HEX = "0af7651916cd43dd8448eb211c80319c"
T0 = 1_773_934_287_000.0
AGENT = f"pricer_{short_hash('You quote prices.')}"  # ADOT adapter agent identity


def agent_session(n_tools: int = 1, *, hex_ids: bool = True) -> Session:
    """Agent -> LLM (tool calls) -> tool(s) -> LLM (answer), with timestamps."""

    def sid(i: int) -> str:
        return f"{i:016x}" if hex_ids else f"step-{i}"

    calls = tuple(
        ToolCall(name="get_price", arguments={"symbol": "BNB", "n": i}, id=f"call{i}")
        for i in range(n_tools)
    )
    steps: list[Any] = [
        AgentStep(
            id=sid(1),
            agent_id=AGENT,
            start_ms=T0,
            end_ms=T0 + 2000,
            system_prompt="You quote prices.",
            user_prompt="Price of BNB?",
            response="BNB is 600 USD.",
            available_tools=({"name": "get_price", "description": "Quote a token"},),
            usage=Usage(input_tokens=30, output_tokens=12),
        ),
        LlmStep(
            id=sid(2),
            parent_id=sid(1),
            agent_id=AGENT,
            start_ms=T0 + 100,
            end_ms=T0 + 400,
            model="model-a",
            messages=(
                {"role": "system", "content": "You quote prices."},
                {"role": "user", "content": "Price of BNB?"},
            ),
            tool_calls=calls,
            usage=Usage(input_tokens=10, output_tokens=5),
        ),
    ]
    for i, call in enumerate(calls):
        steps.append(
            ToolStep(
                id=sid(16 + i),
                parent_id=sid(1),
                agent_id=AGENT,
                start_ms=T0 + 500 + i,
                end_ms=T0 + 600 + i,
                call=call,
                result=ToolResult(content="600", call_id=call.id),
            )
        )
    history = [
        {"role": "user", "content": "Price of BNB?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": dict(c.arguments)} for c in calls
            ],
        },
        *({"role": "tool", "content": "600", "tool_call_id": c.id} for c in calls),
    ]
    steps.append(
        LlmStep(
            id=sid(3),
            parent_id=sid(1),
            agent_id=AGENT,
            start_ms=T0 + 1000,
            end_ms=T0 + 1500,
            model="model-a",
            messages=tuple(history),
            response="BNB is 600 USD.",
        )
    )
    trace_id = TRACE_HEX if hex_ids else "turn-1"
    return Session(id="sess-1", traces=(Trace(id=trace_id, steps=tuple(steps)),))


# ETK SpanFilter predicates (evaluator_builtin/scorers/agentcore.py), for payload checks.
def etk_is_span(item: dict[str, Any]) -> bool:
    return "name" in item and "kind" in item and "startTimeUnixNano" in item


def etk_is_event(item: dict[str, Any]) -> bool:
    return "body" in item and "timeUnixNano" in item and "startTimeUnixNano" not in item


def etk_is_evaluable(name: str, attributes: dict[str, Any]) -> bool:
    low = name.lower()
    model = "chat" in low or "gen_ai.operation.name" in attributes
    tool = "execute_tool" in low or "gen_ai.tool.name" in attributes
    return model or tool or "invoke_agent" in low or "execute_event_loop_cycle" in low


def check_payload(spans: list[dict[str, Any]], session_id: str) -> None:
    span_ids = {s["spanId"] for s in spans if etk_is_span(s)}
    for item in spans:
        assert len(item["traceId"]) == 32 and int(item["traceId"], 16) >= 0
        assert len(item["spanId"]) == 16 and int(item["spanId"], 16) >= 0
        assert item["attributes"]["session.id"] == session_id
        assert etk_is_span(item) != etk_is_event(item)
        if etk_is_span(item):
            assert {"traceId", "spanId", "name", "kind"} <= item.keys()
            assert isinstance(item["startTimeUnixNano"], int)
            assert item["endTimeUnixNano"] >= item["startTimeUnixNano"]
            assert item["scope"]["name"] == acs.SCOPE
            assert etk_is_evaluable(item["name"], item["attributes"])
            assert item.get("parentSpanId") in span_ids | {None}
        else:
            assert item["spanId"] in span_ids
            assert item["attributes"]["event.name"] == acs.SCOPE
            assert set(item["body"]) == {"input", "output"}
    json.dumps(spans)  # the payload is plain JSON


def test_session_spans_format() -> None:
    session = agent_session()
    spans = acs.session_spans(session)
    check_payload(spans, "sess-1")
    names = [s["name"] for s in spans if etk_is_span(s)]
    assert names == ["invoke_agent pricer", "chat", "execute_tool get_price", "chat"]
    agent_span, agent_event = spans[0], spans[1]
    assert agent_span["traceId"] == TRACE_HEX and agent_span["spanId"] == f"{1:016x}"
    assert "parentSpanId" not in agent_span
    assert agent_span["kind"] == "INTERNAL" and agent_span["status"] == {"code": "OK"}
    assert agent_span["startTimeUnixNano"] == int(T0 * 1_000_000)
    assert agent_span["durationNano"] == 2000 * 1_000_000
    attrs = agent_span["attributes"]
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.agent.name"] == "pricer" and attrs["gen_ai.usage.input_tokens"] == 30
    assert json.loads(attrs["gen_ai.agent.tools"]) == ["get_price"]
    assert agent_event["body"]["input"]["messages"][0] == {
        "role": "system",
        "content": "You quote prices.",
    }
    tool_span = next(s for s in spans if s.get("name", "").startswith("execute_tool"))
    assert tool_span["parentSpanId"] == f"{1:016x}"
    assert tool_span["attributes"]["gen_ai.tool.name"] == "get_price"
    assert tool_span["attributes"]["gen_ai.tool.call.id"] == "call0"
    assert acs.trace_ids(spans) == [TRACE_HEX]
    assert acs.tool_span_ids(spans) == [f"{16:016x}"]


def test_session_spans_round_trip_through_adot_adapter() -> None:
    session = agent_session(n_tools=2)
    back = AgentCoreAdotAdapter().to_session(acs.session_spans(session))
    assert back.warnings == ()
    assert back.id == session.id
    assert back.traces == session.traces


def test_session_spans_derives_hex_ids_deterministically() -> None:
    session = agent_session(hex_ids=False)
    spans = acs.session_spans(session)
    check_payload(spans, "sess-1")
    assert spans == acs.session_spans(session)
    back = AgentCoreAdotAdapter().to_session(spans)
    assert [type(s).__name__ for s in back.steps()] == [type(s).__name__ for s in session.steps()]
    assert back.final_response() == "BNB is 600 USD."


def test_session_spans_synthesizes_agent_root_and_reparents() -> None:
    steps = (
        DelegationStep(id="d", from_agent="a", to_agent="b"),
        LlmStep(
            id="l",
            parent_id="d",
            messages=({"role": "user", "content": "hi"},),
            response="hello",
        ),
        ToolStep(
            id="t",
            parent_id="d",
            call=ToolCall(name="lookup"),
            error="boom",
            result=ToolResult(error="boom"),
        ),
        TxStep(id="x", chain_id=56, to="0x" + "00" * 20),
    )
    spans = acs.session_spans(Session(id="s", traces=(Trace(id="t1", steps=steps),)))
    check_payload(spans, "s")
    span_items = [s for s in spans if etk_is_span(s)]
    root, llm, tool = span_items
    assert root["name"] == "invoke_agent agent"
    assert llm["parentSpanId"] == root["spanId"] == tool["parentSpanId"]
    assert tool["status"] == {"code": "ERROR", "message": "boom"}
    assert tool["attributes"]["gen_ai.tool.status"] == "error"
    root_event = spans[1]["body"]
    assert root_event["output"]["messages"][0]["content"]["message"] == "hello"
    # Missing timestamps are synthesized: ordered, non-negative durations.
    assert llm["startTimeUnixNano"] < tool["startTimeUnixNano"]
    assert root["startTimeUnixNano"] <= llm["startTimeUnixNano"]
    assert acs.session_spans(Session(id="s", traces=(Trace(id="t", steps=steps[3:]),))) == []


# Trimmed copy of ETK tests/fixtures/agentcore_real_trace.json (one Strands turn: invoke_agent
# -> event loop -> chat / execute_tool -> event loop -> chat, plus botocore and starlette spans
# that are not evaluable). Bodies are shortened; resource and AWS account attributes removed.
REAL_TRACE_ID = "54007f7a5cbc4508a5100f18ccd33efc"
REAL_SESSION = "6ce30eaf-4592-4843-a561-3565bbefe3e4"
STRANDS = {"name": "strands.telemetry.tracer", "version": ""}
TOOL_USE = (
    '{"toolUse": {"toolUseId": "tooluse_n0trqtTWUQUlqi3ngSJn8h", "name": "get_product_info", '
    '"input": {"product_type": "tablets"}}}'
)
TOOL_TEXT = "Our tablets have 10-12 inch displays, all-day battery life, and support stylus input."
ANSWER = "The specifications for our tablets are: 10-12 inch displays and stylus input."
USER_IN = {"content": {"content": '[{"text": "What are the specs for tablets?"}]'}, "role": "user"}


def _real_span(span_id: str, parent: str, name: str, start: int, end: int, **attrs: Any) -> dict:
    return {
        "scope": attrs.pop("scope", STRANDS),
        "traceId": REAL_TRACE_ID,
        "spanId": span_id,
        "parentSpanId": parent,
        "flags": 256,
        "name": name,
        "kind": attrs.pop("kind", "INTERNAL"),
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "durationNano": end - start,
        "attributes": {**attrs, "session.id": REAL_SESSION},
        "status": {"code": attrs.pop("status", "UNSET")},
    }


def _real_event(span_id: str, time: int, body: dict) -> dict:
    return {
        "scope": {"name": "strands.telemetry.tracer"},
        "timeUnixNano": time,
        "observedTimeUnixNano": time + 1000,
        "severityNumber": 9,
        "severityText": "",
        "body": body,
        "attributes": {"event.name": "strands.telemetry.tracer", "session.id": REAL_SESSION},
        "flags": 1,
        "traceId": REAL_TRACE_ID,
        "spanId": span_id,
    }


CHAT = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "us.amazon.nova-micro-v1:0"}
REAL_TRACE: list[dict[str, Any]] = [
    _real_span(
        "34d8f2af94e1ab94",
        "6f59a76435039d81",
        "chat us.amazon.nova-micro-v1:0",
        1773934287694017943,
        1773934288310470213,
        scope={"name": "opentelemetry.instrumentation.botocore.bedrock-runtime"},
        kind="CLIENT",
        **CHAT,
        **{"gen_ai.system": "aws.bedrock"},
    ),
    _real_span(
        "6f59a76435039d81",
        "84487d2efa515e5a",
        "chat",
        1773934287691912874,
        1773934288310918308,
        **CHAT,
        **{"gen_ai.usage.input_tokens": 720, "gen_ai.usage.output_tokens": 65},
    ),
    _real_event(
        "6f59a76435039d81",
        1773934288310918308,
        {
            "output": {
                "messages": [
                    {
                        "content": {
                            "message": f'[{{"text": "I will call the tool."}}, {TOOL_USE}]',
                            "finish_reason": "tool_use",
                        },
                        "role": "assistant",
                    }
                ]
            },
            "input": {"messages": [USER_IN]},
        },
    ),
    _real_span(
        "4a72e77de7da9dcd",
        "84487d2efa515e5a",
        "execute_tool get_product_info",
        1773934288311348861,
        1773934288312062801,
        status="OK",
        **{
            "gen_ai.tool.status": "success",
            "gen_ai.tool.call.id": "tooluse_n0trqtTWUQUlqi3ngSJn8h",
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "get_product_info",
        },
    ),
    _real_event(
        "4a72e77de7da9dcd",
        1773934288312062801,
        {
            "output": {
                "messages": [
                    {
                        "content": {
                            "message": json.dumps([{"text": TOOL_TEXT}]),
                            "id": "tooluse_n0trqtTWUQUlqi3ngSJn8h",
                        },
                        "role": "assistant",
                    }
                ]
            },
            "input": {
                "messages": [
                    {
                        "content": {
                            "content": '{"product_type": "tablets"}',
                            "role": "tool",
                            "id": "tooluse_n0trqtTWUQUlqi3ngSJn8h",
                        },
                        "role": "tool",
                    }
                ]
            },
        },
    ),
    _real_span(
        "109c7733194603e6",
        "5bf8cf89cd635435",
        "chat",
        1773934288410133365,
        1773934288921674980,
        **CHAT,
    ),
    _real_event(
        "109c7733194603e6",
        1773934288921674980,
        {
            "output": {
                "messages": [
                    {
                        "content": {
                            "message": json.dumps([{"text": ANSWER}]),
                            "finish_reason": "end_turn",
                        },
                        "role": "assistant",
                    }
                ]
            },
            "input": {
                "messages": [
                    USER_IN,
                    {
                        "content": {
                            "content": json.dumps(
                                [
                                    {
                                        "toolResult": {
                                            "toolUseId": "tooluse_n0trqtTWUQUlqi3ngSJn8h",
                                            "status": "success",
                                            "content": [{"text": TOOL_TEXT}],
                                        }
                                    }
                                ]
                            )
                        },
                        "role": "tool",
                    },
                ]
            },
        },
    ),
    _real_span(
        "5bf8cf89cd635435",
        "125f504df26063ac",
        "execute_event_loop_cycle",
        1773934288409999334,
        1773934288921956150,
    ),
    _real_span(
        "84487d2efa515e5a",
        "125f504df26063ac",
        "execute_event_loop_cycle",
        1773934287691815208,
        1773934288921974322,
    ),
    _real_span(
        "125f504df26063ac",
        "05f44b65edebdde9",
        "invoke_agent Strands Agents",
        1773934287691415372,
        1773934288922088187,
        status="OK",
        **{
            "gen_ai.agent.name": "Strands Agents",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.usage.input_tokens": 1556,
            "gen_ai.usage.output_tokens": 121,
            "gen_ai.agent.tools": '["get_product_info", "get_return_policy", "web_search"]',
        },
    ),
    _real_event(
        "125f504df26063ac",
        1773934288922088187,
        {
            "output": {
                "messages": [
                    {
                        "content": {"message": ANSWER, "finish_reason": "end_turn"},
                        "role": "assistant",
                    }
                ]
            },
            "input": {
                "messages": [
                    {"content": "You are a helpful support assistant.", "role": "system"},
                    USER_IN,
                ]
            },
        },
    ),
    _real_span(
        "05f44b65edebdde9",
        "4323848a5f6c707c",
        "POST /invocations",
        1773934287690222715,
        1773934288957310252,
        scope={"name": "opentelemetry.instrumentation.starlette"},
        kind="SERVER",
        **{"http.route": "/invocations"},
    ),
]


def test_session_spans_matches_etk_real_trace_semantics() -> None:
    adapter = AgentCoreAdotAdapter()
    session = adapter.to_session(REAL_TRACE)
    spans = acs.session_spans(session)
    check_payload(spans, REAL_SESSION)
    by_id = {s["spanId"]: s for s in spans if etk_is_span(s)}
    # The evaluable spans ETK forwards keep their ids; HTTP / botocore / loop spans are not
    # needed (AgentCore attaches the event bodies to the Strands spans).
    assert list(by_id) == [
        "125f504df26063ac",
        "6f59a76435039d81",
        "4a72e77de7da9dcd",
        "109c7733194603e6",
    ]
    assert acs.trace_ids(spans) == [REAL_TRACE_ID]
    assert acs.tool_span_ids(spans) == ["4a72e77de7da9dcd"]  # ETK extract_tool_span_ids
    assert by_id["125f504df26063ac"]["name"] == "invoke_agent Strands Agents"
    tool = by_id["4a72e77de7da9dcd"]
    assert tool["attributes"]["gen_ai.tool.call.id"] == "tooluse_n0trqtTWUQUlqi3ngSJn8h"
    assert tool["startTimeUnixNano"] == 1773934288311349000  # microsecond precision
    # Re-reading our payload gives the same session.
    again = adapter.to_session(spans)
    assert again.warnings == () and again.traces == session.traces and again.id == REAL_SESSION


def test_build_eval_params_levels() -> None:
    spans = acs.session_spans(agent_session())
    params, details = acs.build_eval_params(
        "Builtin.GoalSuccessRate", "session", [TRACE_HEX], spans
    )
    assert params == {
        "evaluatorId": "Builtin.GoalSuccessRate",
        "evaluationInput": {"sessionSpans": spans},
    }
    params, _ = acs.build_eval_params("Builtin.Helpfulness", "trace", [TRACE_HEX], spans)
    assert params["evaluationTarget"] == {"traceIds": [TRACE_HEX]}
    params, _ = acs.build_eval_params("Builtin.ToolSelectionAccuracy", "tool", [TRACE_HEX], spans)
    assert params["evaluationTarget"] == {"spanIds": [f"{16:016x}"]}
    no_tools = spans[:4]  # the agent and first chat spans with their events
    params, details = acs.build_eval_params(
        "Builtin.ToolSelectionAccuracy", "tool", ["x"], no_tools
    )
    assert params["evaluationTarget"] == {"traceIds": ["x"]}
    assert details["tool_spans_fallback"] == "traceIds"


async def test_agentcore_tool_span_cap(aws: FakeBoto3, caplog: pytest.LogCaptureFixture) -> None:
    case = EvalCase(id="1", trajectory=agent_session(n_tools=12))
    with caplog.at_level(logging.WARNING):
        res = await run("agent.tool_selection", case, method="agentcore")
    assert res.status == "scored"
    target = aws.calls[0]["evaluationTarget"]["spanIds"]
    assert len(target) == acs.MAX_TOOL_SPANS == 10
    assert target == [f"{16 + i:016x}" for i in range(10)]
    assert res.details["tool_spans_total"] == 12 and res.details["tool_spans_capped"] is True
    assert "limiting tool spans from 12 to 10" in caplog.text


@pytest.mark.parametrize(
    ("metric_id", "evaluator", "target_key"),
    [
        ("agent.goal_success", "Builtin.GoalSuccessRate", None),
        ("quality.helpfulness", "Builtin.Helpfulness", "traceIds"),
        ("rag.faithfulness", "Builtin.Faithfulness", "traceIds"),
        ("agent.tool_parameter_accuracy", "Builtin.ToolParameterAccuracy", "spanIds"),
    ],
)
async def test_agentcore_levels(
    aws: FakeBoto3, metric_id: str, evaluator: str, target_key: str | None
) -> None:
    res = await run(metric_id, EvalCase(id="1", trajectory=agent_session()), method="agentcore")
    assert res.status == "scored" and res.score == 0.8 and res.reason == "fine"
    (call,) = aws.calls
    assert call["evaluatorId"] == evaluator
    assert call["evaluationInput"]["sessionSpans"] == acs.session_spans(agent_session())
    if target_key is None:
        assert "evaluationTarget" not in call
    else:
        assert list(call["evaluationTarget"]) == [target_key]
    assert res.details["evaluator_id"] == evaluator and res.details["label"] == "Good"
    assert res.provenance.judge == {
        "provider": "agentcore",
        "model": evaluator,
        "params": {"region": "us-east-1", "level": acs.BUILTINS[evaluator]},
    }


async def test_agentcore_region(aws: FakeBoto3, monkeypatch: pytest.MonkeyPatch) -> None:
    case = EvalCase(id="1", trajectory=agent_session())
    await run("quality.coherence", case, method="agentcore")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    await run("quality.coherence", case, method="agentcore")
    await run("quality.coherence", case, {"region": "ap-southeast-1"}, method="agentcore")
    assert [c["region"] for c in aws.calls] == ["us-east-1", "eu-west-1", "ap-southeast-1"]
    assert {s for s, _ in aws.clients} == {"bedrock-agentcore"}
    await run("quality.coherence", case, method="agentcore")
    assert len(aws.clients) == 3  # clients are cached per region


async def test_agentcore_custom_evaluator(aws: FakeBoto3) -> None:
    case = EvalCase(id="1", trajectory=agent_session())
    params = {"agentcore_evaluator_id": "my-eval-0000000000", "level": "session"}
    res = await run("quality.relevance", case, params, method="agentcore")
    assert res.status == "scored" and aws.calls[-1]["evaluatorId"] == "my-eval-0000000000"
    assert "evaluationTarget" not in aws.calls[-1]
    params = {"agentcore_evaluator_id": "my-eval-0000000000", "level": "tool_call"}
    await run("quality.relevance", case, params, method="agentcore")
    assert list(aws.calls[-1]["evaluationTarget"]) == ["spanIds"]
    res = await run(
        "quality.relevance", case, {"agentcore_evaluator_id": "my-eval"}, method="agentcore"
    )
    assert res.status == "error" and "requires level" in res.error.message
    # A built-in id keeps its own level (ETK); ContextRelevance stays excluded.
    params = {"agentcore_evaluator_id": "Builtin.GoalSuccessRate", "level": "trace"}
    await run("quality.relevance", case, params, method="agentcore")
    assert "evaluationTarget" not in aws.calls[-1]
    n = len(aws.calls)
    params = {"agentcore_evaluator_id": "Builtin.ContextRelevance", "level": "trace"}
    res = await run("rag.faithfulness", case, params, method="agentcore")
    assert res.status == "error" and "ContextRelevance" in res.error.message
    assert len(aws.calls) == n
    assert "Builtin.ContextRelevance" not in acs.BUILTINS
    assert "vendor.agentcore.Builtin.ContextRelevance" not in registry.aliases()


@pytest.mark.parametrize(
    ("error", "response", "code", "retryable"),
    [
        (ClientError("ThrottlingException"), None, "judge_unavailable", True),
        (ClientError("AccessDeniedException"), None, "metric_error", False),
        (RuntimeError("no credentials"), None, "metric_error", False),
        (None, {"evaluationResults": []}, "metric_error", False),
        (None, {"evaluationResults": [{"value": 1}, {"value": 1}]}, "metric_error", False),
        (None, {"evaluationResults": [{"errorMessage": "bad spans"}]}, "metric_error", False),
        (None, {"evaluationResults": [{"value": None}]}, "vendor_nan", False),
        (None, {"evaluationResults": [{"value": "high"}]}, "vendor_nan", False),
        (None, {"evaluationResults": [{"value": float("nan")}]}, "vendor_nan", False),
    ],
)
async def test_agentcore_error_mapping(
    aws: FakeBoto3,
    error: Exception | None,
    response: dict | None,
    code: str,
    retryable: bool,
) -> None:
    aws.error = error
    if response is not None:
        aws.response = response
    res = await run(
        "safety.harmfulness", EvalCase(id="1", trajectory=agent_session()), method="agentcore"
    )
    assert res.status == "error" and res.score is None
    assert res.error.code == code and res.error.retryable is retryable


async def test_agentcore_missing_trajectory_and_no_spans(aws: FakeBoto3) -> None:
    res = await run("quality.conciseness", EvalCase(id="1", output="x"), method="agentcore")
    assert res.status == "skipped" and res.error.code == "missing_input"
    only_tx = Session(
        id="s", traces=(Trace(id="t", steps=(TxStep(id="x", chain_id=56, to="0x" + "0" * 40),)),)
    )
    res = await run(
        "quality.conciseness", EvalCase(id="1", trajectory=only_tx), method="agentcore"
    )
    assert res.status == "skipped" and "no evaluable spans" in res.error.message
    assert aws.calls == []


@pytest.mark.parametrize(
    ("result", "expect", "score", "refused"),
    [
        ({"value": 1.0, "label": "Yes"}, None, None, True),
        ({"value": 1.0, "label": "Yes"}, True, 1.0, True),
        ({"value": 1.0, "label": "Yes"}, False, 0.0, True),
        ({"value": 0.0, "label": "No"}, False, 1.0, False),
        ({"value": 0.9}, True, 1.0, True),
    ],
)
async def test_agentcore_refusal_semantics(
    aws: FakeBoto3, result: dict, expect: bool | None, score: float | None, refused: bool
) -> None:
    aws.response = {"evaluationResults": [result]}
    params = {} if expect is None else {"expect_refusal": expect}
    res = await run(
        "safety.refusal", EvalCase(id="1", trajectory=agent_session()), params, "agentcore"
    )
    assert res.status == "scored" and res.score == score
    assert res.details["refused"] is refused and res.details["raw"] == result["value"]


def test_agentcore_methods_and_aliases() -> None:
    aliases = registry.aliases()
    by_evaluator = {e: m for m, e in agentcore.METHODS.items()}
    for evaluator in acs.BUILTINS:
        metric_id = by_evaluator.get(evaluator, "quality.correctness")
        assert aliases[f"vendor.agentcore.{evaluator}"] == (metric_id, "agentcore")
        cls, method = registry.resolve(f"vendor.agentcore.{evaluator}")
        method_spec = registry.effective_spec(cls).method(method)
        assert method_spec.kind is Kind.VENDOR and method_spec.extra == "agentcore"
        assert method_spec.extra_module == "boto3"
        assert method_spec.required_inputs == {"trajectory"}
    assert len([a for a in aliases if a.startswith("vendor.agentcore.")]) == 13


async def test_quality_correctness_end_to_end(aws: FakeBoto3) -> None:
    spec = registry.get_spec("quality.correctness")
    assert spec.method_names == ["native", "agentcore"] and spec.default_threshold == 0.5
    assert spec.validate_params({"definitely_not_a_param": 1})
    assert not spec.validate_params({"region": "us-west-2", "level": "trace"})
    suite = Suite(
        name="s",
        version="1",
        metrics=(MetricConfig(id="vendor.agentcore.Builtin.Correctness"),),
    )
    resp = await Runner().run(
        EvalRequest(suite=suite, cases=(EvalCase(id="1", trajectory=agent_session()),))
    )
    metric = resp.results[0].metrics[0]
    assert metric.metric_id == "quality.correctness" and metric.method == "agentcore"
    assert metric.score == 0.8 and metric.passed is True and metric.reason == "fine"
    assert aws.calls[0]["evaluatorId"] == "Builtin.Correctness"
    assert aws.calls[0]["evaluationTarget"] == {"traceIds": [TRACE_HEX]}


def test_vendor_params_validate_through_suites() -> None:
    from bnbagent_eval.metrics import registry

    spec = registry.get_spec("safety.toxicity")
    assert not spec.validate_params({"model_type": "toxigen", "fmeval_config": {}})
    assert spec.validate_params({"model_type": "nope"})
    assert not registry.get_spec("quality.helpfulness").validate_params({"region": "us-west-2"})
