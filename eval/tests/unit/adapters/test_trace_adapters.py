"""Trace adapters: golden snapshots, detection, robustness, rendering.

Fixtures live in ``tests/fixtures/traces/<format>/<name>.json`` with the expected Session in
``<name>.golden.json``. Regenerate goldens after an intentional mapping change with
``BNBAGENT_UPDATE_GOLDEN=1 pytest tests/unit/adapters``, then review the diff.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from bnbagent_eval.adapters.traces import (
    BUILTIN_ADAPTERS,
    AdapterRegistry,
    adapter_names,
    detect_format,
    get_adapter,
    load_trace,
    render_trajectory,
)
from bnbagent_eval.adapters.traces._otel import parse_spans
from bnbagent_eval.adapters.traces.otel_genai import is_latest
from bnbagent_eval.exceptions import AdapterError
from bnbagent_eval.types.trajectory import AgentStep, LlmStep, Session

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "traces"
UPDATE = os.environ.get("BNBAGENT_UPDATE_GOLDEN") == "1"

FORMATS = {
    "native",
    "otel_genai",
    "agentcore_adot",
    "langfuse_strands",
    "langfuse_langchain",
    "langfuse_langgraph",
    "langsmith_langgraph",
    "smolagents",
    "taubench",
    "cx0",
    "eb_habitat",
}
INPUTS = sorted(p for p in FIXTURES.glob("*/*.json") if not p.name.endswith(".golden.json"))


def _id(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _fixture(fmt: str, name: str) -> Any:
    return _load(FIXTURES / fmt / f"{name}.json")


# --------------------------------------------------------------------------- golden + detection


def test_fixtures_cover_every_format() -> None:
    assert {p.parent.name for p in INPUTS} == FORMATS


@pytest.mark.parametrize("path", INPUTS, ids=_id)
def test_golden_session(path: Path) -> None:
    session = load_trace(_load(path))
    actual = _canonical(session.to_dict())
    golden = path.with_name(f"{path.stem}.golden.json")
    if UPDATE or not golden.exists():
        golden.write_text(actual, encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"golden snapshot created: {golden.name}; review and commit it")
    assert actual == golden.read_text(encoding="utf-8")
    assert Session.from_dict(json.loads(actual)) == session


@pytest.mark.parametrize("path", INPUTS, ids=_id)
def test_autodetect(path: Path) -> None:
    name, candidates = detect_format(_load(path))
    assert name == path.parent.name
    assert candidates[0][0] == name
    assert len(candidates) == 1 or candidates[1][1] < candidates[0][1]


@pytest.mark.parametrize("path", INPUTS, ids=_id)
def test_explicit_format_matches_autodetect(path: Path) -> None:
    data = _load(path)
    assert load_trace(data, format=path.parent.name) == load_trace(data)


@pytest.mark.parametrize("path", INPUTS, ids=_id)
def test_mapping_is_deterministic(path: Path) -> None:
    data = _load(path)
    pristine = copy.deepcopy(data)
    assert load_trace(data) == load_trace(copy.deepcopy(data))
    assert data == pristine  # adapters never mutate their input


@pytest.mark.parametrize("path", INPUTS, ids=_id)
def test_render_mapped_session(path: Path) -> None:
    text = render_trajectory(load_trace(_load(path)))
    assert "# Conversation record" in text


def test_all_formats_registered() -> None:
    assert set(adapter_names()) == FORMATS
    assert len(FORMATS - {"native"}) == 10
    assert "otel_genai" in adapter_names()  # StrandsInMemory, which AgentSight never registered


def test_session_id_override() -> None:
    session = load_trace(_fixture("taubench", "airline_task"), session_id="custom")
    assert session.id == "custom"


def test_load_trace_from_jsonl_path(tmp_path: Path) -> None:
    spans = _fixture("otel_genai", "legacy")
    path = tmp_path / "spans.jsonl"
    path.write_text("\n".join(json.dumps(s) for s in spans) + "\n", encoding="utf-8")
    assert load_trace(path) == load_trace(spans)


# --------------------------------------------------------------------------- registry errors


class _Fake:
    def __init__(self, name: str, confidence: float) -> None:
        self.name = name
        self.confidence = confidence

    def detect(self, data: Any) -> float:
        return self.confidence

    def to_session(self, data: Any, *, session_id: str | None = None) -> Session:
        return Session(id=session_id or self.name)


def test_ambiguous_detection_lists_candidates() -> None:
    registry = AdapterRegistry([_Fake("alpha", 0.7), _Fake("beta", 0.7), _Fake("gamma", 0.2)])
    with pytest.raises(AdapterError, match="ambiguous") as info:
        registry.detect({"anything": 1})
    assert "alpha" in str(info.value) and "beta" in str(info.value)
    assert "gamma" not in str(info.value)
    assert registry.load({"x": 1}, format="alpha").id == "alpha"  # explicit format still works


def test_most_confident_adapter_wins() -> None:
    registry = AdapterRegistry([_Fake("alpha", 0.4), _Fake("beta", 0.9)])
    assert registry.detect({})[0] == "beta"


def test_unknown_input_and_format() -> None:
    with pytest.raises(AdapterError, match="could not detect"):
        detect_format({"hello": "world"})
    with pytest.raises(AdapterError, match="unknown trace format"):
        load_trace({}, format="nope")


def test_duplicate_registration_rejected() -> None:
    registry = AdapterRegistry([_Fake("alpha", 0.5)])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_Fake("alpha", 0.1))
    registry.register(_Fake("alpha", 0.1), replace=True)
    with pytest.raises(TypeError):
        registry.register(object())  # type: ignore[arg-type]


def test_failing_detector_is_ignored() -> None:
    class Broken(_Fake):
        def detect(self, data: Any) -> float:
            raise RuntimeError("boom")

    registry = AdapterRegistry([Broken("broken", 1.0), _Fake("ok", 0.5)])
    assert registry.detect({})[0] == "ok"


# --------------------------------------------------------------- CX0 detection vs AgentSight


def _agentsight_detect_cx0(sample: Any) -> bool:
    """AgentSight ``SessionMapperFactory._detect_cx0`` (applied to ``data[0]``), verbatim logic."""
    if isinstance(sample, list) and sample and isinstance(sample[0], dict):
        fields = ["span_id", "trace_id", "operation_name", "start_time", "end_time"]
        return all(f in sample[0] for f in fields)
    return False


def test_cx0_detection_matches_the_parsed_shape() -> None:
    trajectory = _fixture("cx0", "weather_transfer")
    blocks = trajectory["steps"]
    # The AgentSight heuristic never recognises what its own mapper parses ...
    assert not _agentsight_detect_cx0(trajectory)
    assert not _agentsight_detect_cx0(blocks)
    assert not _agentsight_detect_cx0(blocks[0])
    # ... ours does, for every accepted wrapping.
    for data in (trajectory, blocks, {"trajectory": trajectory}, {"trajectory": blocks}):
        assert detect_format(data)[0] == "cx0"
        assert len(load_trace(data).tool_steps()) == 2


def test_cx0_does_not_claim_span_shaped_input() -> None:
    spans = [
        {
            "span_id": "1",
            "trace_id": "t",
            "operation_name": "agent",
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:01Z",
        }
    ]
    assert _agentsight_detect_cx0(spans)  # AgentSight routed [spans] to CX0 ...
    assert get_adapter("cx0").detect(spans) == 0.0  # ... a shape the CX0 mapper cannot read
    assert get_adapter("cx0").detect([spans]) == 0.0


# --------------------------------------------------------------------------- OTel GenAI versions


def test_otel_convention_detected_per_span() -> None:
    legacy, _ = parse_spans(_fixture("otel_genai", "legacy"))
    latest, _ = parse_spans(_fixture("otel_genai", "latest"))
    assert legacy and not any(is_latest(s) for s in legacy)
    assert latest and all(is_latest(s) for s in latest)


def test_otel_legacy_and_latest_map_alike() -> None:
    legacy = load_trace(_fixture("otel_genai", "legacy"))
    latest = load_trace(_fixture("otel_genai", "latest"))
    for session in (legacy, latest):
        agent = next(s for s in session.steps() if isinstance(s, AgentStep))
        assert agent.agent_id == "Strands Agents"
        assert agent.user_prompt == "What is the status of my package?"
        assert agent.response and agent.response.startswith("Your package is ready for pickup")
        first_call = session.tool_calls()[0]
        assert (first_call.name, dict(first_call.arguments)) == (
            "check_package_status",
            {"user_name": "Bob"},
        )
        assert session.tool_steps()[0].result.content == "Package is ready for pickup"
        llm = next(s for s in session.steps() if isinstance(s, LlmStep))
        assert llm.model == "example-model-v1" and llm.usage and llm.usage.input_tokens == 500
        assert llm.tool_calls and llm.tool_calls[0].id == first_call.id
    # Legacy: gen_ai.choice is the model output, not one more input message.
    first = next(s for s in legacy.steps() if isinstance(s, LlmStep))
    assert [m["role"] for m in first.messages] == ["user"]
    assert first.response == "I'll check the status."
    # >=1.37: tool definitions, system instructions and error status are read.
    agent = next(s for s in latest.steps() if isinstance(s, AgentStep))
    assert agent.system_prompt and agent.available_tools[0]["description"]
    failed = latest.tool_steps()[1]
    assert failed.error and failed.result and failed.result.error


def test_otel_latest_messages_as_span_attributes() -> None:
    """The >=1.37 semconv also allows messages directly on the span (no details event)."""
    span = {
        "traceId": "ab" * 16,
        "spanId": "01" * 8,
        "name": "chat",
        "startTimeUnixNano": "1700000000000000000",
        "endTimeUnixNano": "1700000001000000000",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
            {"key": "gen_ai.provider.name", "value": {"stringValue": "openai"}},
            {
                "key": "gen_ai.input.messages",
                "value": {
                    "stringValue": json.dumps(
                        [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]
                    )
                },
            },
            {
                "key": "gen_ai.output.messages",
                "value": {
                    "stringValue": json.dumps(
                        [{"role": "assistant", "parts": [{"type": "text", "content": "hello"}]}]
                    )
                },
            },
        ],
    }
    data = {"resourceSpans": [{"scopeSpans": [{"scope": {"name": "x"}, "spans": [span]}]}]}
    assert detect_format(data)[0] == "otel_genai"
    (step,) = load_trace(data).steps()
    assert isinstance(step, LlmStep)
    assert step.messages[0]["content"] == "hi" and step.response == "hello"
    assert step.end_ms is not None and step.start_ms is not None
    assert step.end_ms - step.start_ms == 1000


# --------------------------------------------------------------------------- AgentCore scopes


@pytest.mark.parametrize(
    ("name", "agent_prefix", "tool"),
    [
        ("strands_multi_agent", "Strands Agents_", "flight_booking_assistant"),
        ("openllmetry_langchain", "LangGraph_", "calculate_bmi"),
        ("openinference_langchain", "LangGraph_", "check_package_status"),
    ],
)
def test_agentcore_scope_mappers(name: str, agent_prefix: str, tool: str) -> None:
    session = load_trace(_fixture("agentcore_adot", name))
    agents = [s for s in session.steps() if isinstance(s, AgentStep)]
    assert agents and all(a.agent_id and a.agent_id.startswith(agent_prefix) for a in agents)
    assert all(a.user_prompt and a.response for a in agents)
    assert tool in {c.name for c in session.tool_calls()}
    assert any(isinstance(s, LlmStep) and s.response for s in session.steps())
    roots = [s for s in session.steps() if s.parent_id is None]
    assert len(roots) == 1 and isinstance(roots[0], AgentStep)
    # Every LLM / tool step hangs under an agent of the same trace.
    for step in session.steps():
        assert step.agent_id is not None


def test_agentcore_multi_agent_hierarchy() -> None:
    session = load_trace(_fixture("agentcore_adot", "strands_multi_agent"))
    steps = {s.id: s for s in session.steps()}
    tool = next(s for s in session.tool_steps() if s.call.name == "hotel_booking_assistant")
    sub_agent = next(
        s for s in steps.values() if isinstance(s, AgentStep) and s.parent_id == tool.id
    )
    assert sub_agent.agent_id != steps[tool.parent_id].agent_id  # type: ignore[index]
    assert sub_agent.system_prompt and sub_agent.system_prompt.startswith("You are a hotel")


def test_agentcore_mixed_scopes_and_unsupported_spans() -> None:
    data = (
        _fixture("agentcore_adot", "strands_multi_agent")
        + _fixture("agentcore_adot", "openllmetry_langchain")
        + _fixture("agentcore_adot", "openinference_langchain")
    )
    assert detect_format(data)[0] == "agentcore_adot"
    session = load_trace(data)
    assert len(session.traces) == 3
    for name in ("strands_multi_agent", "openllmetry_langchain", "openinference_langchain"):
        single = load_trace(_fixture("agentcore_adot", name))
        assert single.traces[0] in session.traces


def test_agentcore_without_supported_scope() -> None:
    data = [
        {"traceId": "aa" * 16, "spanId": "01" * 8, "scope": {"name": "other"}, "name": "x"},
        {"traceId": "aa" * 16, "spanId": "01" * 8, "body": {}, "attributes": {"event.name": "y"}},
    ]
    session = get_adapter("agentcore_adot").to_session(data)
    assert not session.traces and any("supported scopes" in w for w in session.warnings)


# --------------------------------------------------------------------------- robustness


def _otel_span(span_id: str, parent: str | None, op: str = "execute_tool", **times: Any) -> dict:
    span: dict[str, Any] = {
        "traceId": "cd" * 16,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": op,
        "attributes": {"gen_ai.operation.name": op, "gen_ai.tool.name": f"tool_{span_id}"},
        "events": [{"name": "gen_ai.tool.message", "attributes": {"content": "{}"}}],
    }
    span.update(times)
    return span


def test_missing_timestamps_are_warnings() -> None:
    data = [_otel_span("a1", None), _otel_span("a2", "a1", startTimeUnixNano=10**18)]
    session = load_trace(data)
    assert [(s.start_ms, s.end_ms) for s in session.steps()] == [(None, None), (1e12, None)]
    assert any("without timestamps" in w for w in session.warnings)


def test_orphan_spans_are_warnings() -> None:
    data = [_otel_span("a1", "missing-parent", startTimeUnixNano=10**18, endTimeUnixNano=10**18)]
    (step,) = load_trace(data).steps()
    assert step.parent_id is None
    assert any("orphan span" in w and "missing-parent" in w for w in load_trace(data).warnings)


def test_parent_cycles_are_broken_with_a_warning() -> None:
    # Mapped steps pointing at each other.
    data = [_otel_span("a1", "a2"), _otel_span("a2", "a1")]
    session = load_trace(data)
    assert any("cycle" in w for w in session.warnings)
    assert any(s.parent_id is None for s in session.steps())
    # A cycle through unmapped spans.
    data = [
        _otel_span("b1", "x1"),
        {**_otel_span("x1", "x2"), "attributes": {}},
        {**_otel_span("x2", "x1"), "attributes": {}},
    ]
    session = load_trace(data, format="otel_genai")
    assert any("cycle" in w for w in session.warnings)
    assert [s.parent_id for s in session.steps()] == [None]


def test_langfuse_robustness() -> None:
    trace = _fixture("langfuse_strands", "package_status")
    observations = trace["observations"]
    observations[0]["startTime"] = None
    observations[1]["parentObservationId"] = observations[1]["id"]  # self-cycle
    observations[2]["parentObservationId"] = "gone"
    session = load_trace(trace)
    joined = " | ".join(session.warnings)
    assert "startTime" in joined and "cycle" in joined and "orphan" in joined
    assert session.traces and session.traces[0].steps


def test_smolagents_cycle_and_orphan() -> None:
    data = _fixture("smolagents", "code_agent")
    root = data["spans"][0]
    root["parent_span_id"] = "nowhere"
    session = load_trace(data)
    assert any("orphan" in w for w in session.warnings)
    agent = root["child_spans"][0]
    agent["parent_span_id"] = agent["child_spans"][1]["span_id"]  # agent <- its own step
    session = load_trace(data)
    assert any("cycle" in w for w in session.warnings)


def test_langsmith_cycle() -> None:
    runs = _fixture("langsmith_langgraph", "single_agent")
    root = next(r for r in runs if r["name"] == "LangGraph")
    root["parent_run_id"] = runs[0]["id"]
    session = load_trace(runs)
    assert any("cycle" in w for w in session.warnings)
    assert any(s.parent_id is None for s in session.steps())


@pytest.mark.parametrize(
    "adapter", [a for a in BUILTIN_ADAPTERS if a.name != "native"], ids=lambda a: a.name
)
@pytest.mark.parametrize("data", [{}, [], [{}], {"spans": [None]}, [1, "x"]], ids=repr)
def test_adapters_never_crash_on_junk(adapter: Any, data: Any) -> None:
    assert adapter.detect(data) == 0.0
    session = adapter.to_session(data)
    assert isinstance(session, Session)
    assert not list(session.steps())
    assert session.warnings


def test_bad_json_strings_inside_traces_do_not_crash() -> None:
    trace = _fixture("langfuse_strands", "package_status")
    for obs in trace["observations"]:
        if isinstance(obs.get("output"), dict) and "message" in obs["output"]:
            obs["output"]["message"] = '[{"text": "unterminated'
        if isinstance(obs.get("input"), list):
            obs["input"] = [{"role": "user", "content": "{not json"}]
    session = load_trace(trace)
    assert session.traces
