"""Real RAGAS / DeepEval runs, one per vendor method (opt-in, marker ``vendor``).

Run:  pip install 'bnbagent-eval[ragas,deepeval]' && pytest -m vendor tests/vendor

By default the vendor libraries run against a ``FakeJudge`` that answers every prompt with a
minimal instance of the JSON schema the vendor asks for (DeepEval passes it as the structured
output schema; RAGAS embeds it in the prompt), so no network is used. Set
``EVAL_VENDOR_BEDROCK_MODEL`` to run against a real Bedrock judge instead (AWS credential chain).
The checks are about the bridge (the vendor accepts our samples / test cases and returns a
score), not about the vendor's scores.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

import pytest

from bnbagent_eval._optional import available
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import (
    AgentStep,
    Context,
    EvalCase,
    JudgeConfig,
    Message,
    MetricConfig,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

pytestmark = pytest.mark.vendor
BEDROCK_MODEL = os.environ.get("EVAL_VENDOR_BEDROCK_MODEL")


def _minimal(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in schema:
        return _minimal(defs[schema["$ref"].split("/")[-1]], defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return _minimal(options[0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: _minimal(v, defs) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [_minimal(schema.get("items", {}), defs)]
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return True
    return "yes"


def schema_responder(request: Any) -> str:
    """A minimal schema-valid JSON reply; ``2`` for RAGAS' plain rating prompts."""
    schema = request.output_schema
    if schema is None:
        marker = request.user.find("JSON Schema:")
        if marker < 0:
            return "2"
        start = request.user.index("{", marker)
        schema, _ = json.JSONDecoder().raw_decode(request.user[start:])
    return json.dumps(_minimal(dict(schema), dict(schema).get("$defs", {})))


@pytest.fixture
def judge(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Any:
    monkeypatch.chdir(tmp_path)  # DeepEval writes a .deepeval cache folder into the cwd
    monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    monkeypatch.setenv("RAGAS_DO_NOT_TRACK", "true")
    if not BEDROCK_MODEL:
        return FakeJudge(schema_responder)
    from bnbagent_eval.judges.providers import create_judge
    from tests.conftest import _real_connect

    monkeypatch.setattr(socket.socket, "connect", _real_connect)
    return create_judge(JudgeConfig(provider="bedrock", model=BEDROCK_MODEL))


SESSION = Session(
    id="s",
    traces=(
        Trace(
            id="t",
            steps=(
                AgentStep(
                    id="a",
                    user_prompt="What is the weather in Paris?",
                    response="It is sunny in Paris.",
                    available_tools=(
                        {"name": "weather", "description": "Current weather for a city"},
                        {"name": "mcp__w__forecast", "description": "Forecast for a city"},
                    ),
                ),
                ToolStep(
                    id="1",
                    parent_id="a",
                    call=ToolCall(name="weather", arguments={"city": "Paris"}, id="c1"),
                    result=ToolResult(content="sunny", call_id="c1"),
                ),
                ToolStep(
                    id="2",
                    parent_id="a",
                    call=ToolCall(name="mcp__w__forecast", arguments={"city": "Paris"}, id="c2"),
                    result=ToolResult(content="sunny tomorrow", call_id="c2"),
                ),
            ),
        ),
    ),
)
CASE = EvalCase(
    id="c",
    input="What is the weather in Paris?",
    output="It is sunny in Paris.",
    expected={
        "reference": "It is sunny in Paris.",
        "tool_calls": [{"name": "weather", "arguments": {"city": "Paris"}}],
    },
    contexts=(Context(text="Paris weather today: sunny, 24 C."),),
    conversation=(
        Message(role="user", content="What is the weather in Paris?"),
        Message(
            role="assistant",
            tool_calls=(ToolCall(name="weather", arguments={"city": "Paris"}, id="c1"),),
        ),
        Message(role="tool", content="sunny", tool_call_id="c1"),
        Message(role="assistant", content="It is sunny in Paris."),
    ),
    trajectory=SESSION,
)
PARAMS: dict[str, dict[str, Any]] = {
    "conversation.topic_adherence": {"topics": ["weather"]},
    "conversation.role_adherence": {"role": "a weather assistant"},
    "safety.misuse": {"domain": "weather"},
    "safety.non_advice": {"advice_types": ["financial"]},
    "safety.role_violation": {"role": "a weather assistant"},
    "quality.instruction_following": {"instructions": ["Mention the city."]},
    "format.json_schema": {"schema": {"type": "object", "required": ["a"]}},
    "format.regex": {"pattern": ".*Paris.*"},
}
SPECIAL_CASES = {"format.json_schema": EvalCase(id="j", output='{"a": 1}')}


def _pairs(vendor: str) -> list[str]:
    return [s.id for s in registry.list_specs() if vendor in s.method_names]


async def _run(metric_id: str, method: str, judge: Any, case: EvalCase | None = None):
    cls = registry.resolve(metric_id)[0]
    params = PARAMS.get(metric_id, {})
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, method=method, params=params),
        method=registry.get_spec(metric_id).method(method),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(case or SPECIAL_CASES.get(metric_id, CASE), ctx)


@pytest.mark.skipif(not available("ragas"), reason="needs the [ragas] extra")
@pytest.mark.parametrize("metric_id", _pairs("ragas"))
async def test_ragas_real(metric_id: str, judge: Any) -> None:
    res = await _run(metric_id, "ragas", judge)
    assert res.status == "scored" and 0.0 <= res.score <= 1.0, res
    assert res.details["vendor"] == "ragas"


@pytest.mark.skipif(not available("deepeval"), reason="needs the [deepeval] extra")
@pytest.mark.parametrize("metric_id", _pairs("deepeval"))
async def test_deepeval_real(metric_id: str, judge: Any) -> None:
    res = await _run(metric_id, "deepeval", judge)
    assert res.status == "scored" and 0.0 <= res.score <= 1.0, res
    assert res.details["vendor"] == "deepeval"


@pytest.mark.skipif(not available("deepeval"), reason="needs the [deepeval] extra")
async def test_deepeval_single_turn_mcp_and_json_schema(judge: Any) -> None:
    single = EvalCase(id="m", input=CASE.input, output=CASE.output, trajectory=SESSION)
    res = await _run("agent.mcp_use", "deepeval", judge, single)
    assert res.status == "scored" and res.details["vendor_metric"] == "mcp_use", res
    bad = await _run("format.json_schema", "deepeval", judge, EvalCase(id="j", output='{"b": 1}'))
    assert bad.status == "scored" and bad.score == 0.0, bad
    good = await _run("format.json_schema", "deepeval", judge)
    assert good.score == 1.0, good
