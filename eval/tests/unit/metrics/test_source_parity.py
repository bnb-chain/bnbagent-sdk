"""Parity of ported metrics with their sources (ETK, AgentSight, vendors); see
``docs/design/source-map.md``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bnbagent_eval.exceptions import SuiteError
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics import registry
from bnbagent_eval.metrics.base import MetricContext
from bnbagent_eval.types import (
    AgentStep,
    EvalCase,
    LlmStep,
    Message,
    MetricConfig,
    MetricResult,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

from ._util import case as mk
from ._util import run_metric


async def run(
    metric_id: str,
    case: EvalCase,
    params: dict,
    method: str | None = None,
    script: Any = None,
) -> tuple[MetricResult, FakeJudge | None]:
    cls, _ = registry.resolve(metric_id)
    spec = registry.effective_spec(cls)
    judge = FakeJudge(script) if script is not None else None
    ctx = MetricContext(
        config=MetricConfig(id=metric_id, params=params),
        method=spec.method(method),
        params=params,
        judge=judge,
    )
    return await cls().evaluate(case, ctx), judge


# ------------------------------------------------- format.* native = AgentSight LLM mode
FIVE = {"reasoning": "r", "score": "Good", "format_issues": ["trailing comma"]}


async def test_json_schema_native_renders_agentsight_json_llm_mode() -> None:
    schema = {"type": "object", "required": ["a"]}
    res, judge = await run(
        "format.json_schema",
        EvalCase(id="c", output='{"a": 1}'),
        {"schema": schema},
        "native",
        FIVE,
    )
    assert res.score == 0.75 and res.details["validation_type"] == "json"
    user = judge.requests[0].user
    assert "# Expected Structure Type: JSON" in user
    # AgentSight _get_schema_description: json.dumps(schema, indent=2) in a json fence.
    assert '# Structure Specification:\nJSON Schema:\n```json\n{\n  "type": "object",' in user


async def test_regex_and_xml_native_descriptions() -> None:
    res, judge = await run(
        "format.regex", EvalCase(id="c", output="123"), {"pattern": r"\d+"}, "native", FIVE
    )
    assert res.details["validation_type"] == "regex"
    assert "# Expected Structure Type: REGEX" in judge.requests[0].user
    assert "Regex Pattern: `\\d+`" in judge.requests[0].user
    xsd = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'
    res, judge = await run(
        "format.xml", EvalCase(id="c", output="<a/>"), {"xsd": xsd}, "native", FIVE
    )
    assert "# Expected Structure Type: XML" in judge.requests[0].user
    assert f"XML Schema:\n```xml\n{xsd}\n```" in judge.requests[0].user


async def test_xml_native_needs_a_schema_like_agentsight() -> None:
    cls, _ = registry.resolve("format.xml")
    with pytest.raises(SuiteError):
        cls().template_for({})


def test_structure_prompt_unchanged_by_validation_type_support() -> None:
    cls, _ = registry.resolve("format.structure")
    tmpl = cls().template_for({"expected_structure": "S"})
    assert "# Expected Structure Type: DESCRIPTION" in tmpl.parts["simple"]


# ------------------------------------------------- ops.consistency:embedding
class VectorJudge(FakeJudge):
    """Embeds "a..." texts as (1, 0) and anything else as (0, 1)."""

    async def embed(self, texts):  # type: ignore[override]
        return [[1.0, 0.0] if t.startswith("a") else [0.0, 1.0] for t in texts]


async def test_consistency_embedding_is_mean_pairwise_rescaled_cosine() -> None:
    cls, _ = registry.resolve("ops.consistency")
    spec = registry.effective_spec(cls)
    runs = tuple(EvalCase(id=f"r{i}", output=o) for i, o in enumerate(["a1", "a2", "b"]))
    ctx = MetricContext(
        config=MetricConfig(id="ops.consistency"),
        method=spec.method("embedding"),
        params={},
        judge=VectorJudge({}),
        runs=runs,
    )
    res = await cls().evaluate(EvalCase(id="c", input="q"), ctx)
    # pairs: (a1, a2) cosine 1 -> 1.0; (a*, b) cosine 0 -> 0.5 twice; mean = 2/3
    assert res.score == pytest.approx(2 / 3) and res.details["pairs"] == 3
    assert res.details["min_pair"] == 0.5


# ------------------------------------------------- deterministic ports (text.*, agent.*, format.*)
async def test_token_f1_uses_word_sets_like_fmeval() -> None:
    # FMEval _split: set(text.split()); repeated words count once; two empty texts -> 0.0
    assert (await run_metric("text.token_f1", mk("cat cat dog", "cat dog dog"))).score == 1.0
    assert (await run_metric("text.token_f1", mk("", ""))).score == 0.0


@pytest.mark.parametrize(
    ("out", "exp", "score"),
    [
        ("Paris.", "Paris", 1.0),  # punctuation stripped (SemanticRelevanceScorer._tokenize)
        ("The capital of France is Paris.", "Paris", 1 / 3),
        ("state-of-the-art model", "state of the art model", 1.0),
        ("it is what it is", "this is that", 0.0),  # no keywords -> 0.0 (_keyword_score)
    ],
)
async def test_embedding_similarity_keyword_matches_agentsight(out, exp, score) -> None:
    res = await run_metric("text.embedding_similarity", mk(out, exp))
    assert res.score == pytest.approx(score)


@pytest.mark.parametrize(
    ("out", "exp", "score"),
    [
        ("Total: 3.14 USD", "3.14", 0.0),  # extract_keywords: [a-z]+ words of >= 3 letters
        ("The capital of France is Paris.", "Paris", 1 / 3),
        ("Paris.", "Paris", 1.0),
    ],
)
async def test_jaccard_stopwords_is_compute_keyword_overlap(out, exp, score) -> None:
    res = await run_metric("text.jaccard", mk(out, exp), {"stopwords": True})
    assert res.score == pytest.approx(score)


@pytest.mark.parametrize(
    ("out", "exp", "score"),
    [
        ("the the the cat", "the cat", 0.8523),  # NLTK _match_enums aligns from the end
        ("Paris.", "Paris", 0.4545),  # word_tokenize keeps "." as a token
    ],
)
async def test_meteor_alignment_and_tokens_match_nltk(out, exp, score) -> None:
    pytest.importorskip("nltk")
    res = await run_metric("text.meteor", mk(out, exp))
    assert res.score == pytest.approx(score, abs=1e-4)


async def test_classification_valid_labels_extracts_label_like_fmeval() -> None:
    c = mk("The sentiment is positive", "positive")
    assert (await run_metric("text.classification_accuracy", c)).score == 0.0
    res = await run_metric(
        "text.classification_accuracy", c, {"valid_labels": ["positive", "negative"]}
    )
    assert res.score == 1.0 and res.details["predicted"] == "positive"


async def test_length_without_bounds_needs_non_empty_output_like_etk() -> None:
    assert (await run_metric("text.length", mk(""))).score == 0.0
    assert (await run_metric("text.length", mk("x"))).score == 1.0


@pytest.mark.parametrize("text", ["See [1] for details", '{"a": 1} trailing'])
async def test_json_valid_does_not_scan_prose_for_json(text: str) -> None:
    assert (await run_metric("format.json_valid", mk(text))).score == 0.0


async def test_json_valid_still_accepts_a_fenced_block() -> None:
    res = await run_metric("format.json_valid", mk('Here:\n```json\n{"a": 1}\n```'))
    assert res.score == 1.0


def _call_trace(tid: str, name: str, args: dict) -> Trace:
    call = ToolCall(name=name, arguments=args, id=f"{tid}c")
    return Trace(
        id=tid,
        steps=(
            LlmStep(id=f"{tid}1", tool_calls=(call,)),
            ToolStep(id=f"{tid}2", call=call, result=ToolResult(content="ok")),
        ),
    )


async def test_tool_trajectory_match_mean_per_invocation() -> None:
    traj = Session(id="s", traces=(_call_trace("t1", "A", {}), _call_trace("t2", "C", {})))
    expected = {"tool_calls": [[{"name": "A"}], [{"name": "B"}]]}
    res = await run_metric("agent.tool_trajectory_match", mk(trajectory=traj, expected=expected))
    assert res.score == 0.5 and res.details["invocations"] == 2


async def test_tool_trajectory_match_integral_float_args_equal_ints() -> None:
    traj = Session(id="s", traces=(_call_trace("t1", "A", {"x": 1}),))
    expected = {"tool_calls": [{"name": "A", "arguments": {"x": 1.0}}]}
    res = await run_metric("agent.tool_trajectory_match", mk(trajectory=traj, expected=expected))
    assert res.score == 1.0


async def test_trajectory_stats_depth_and_errors_like_signal_extractor() -> None:
    steps = (
        LlmStep(id="1"),
        ToolStep(
            id="2",
            parent_id="1",
            call=ToolCall(name="t", arguments={}),
            error="boom",
            result=ToolResult(error="boom"),
        ),
    )
    traj = Session(id="s", traces=(Trace(id="t", steps=steps),))
    res = await run_metric("agent.trajectory_stats", mk(trajectory=traj))
    assert res.details["depth"] == 2  # roots have depth 1
    assert res.details["errors"] == 1  # one failed step counts once


def test_fluency_word_count_includes_punctuation_tokens() -> None:
    pytest.importorskip("nltk")
    from bnbagent_eval.metrics.vendors.local_models import _words

    assert _words("Hello, world.") == ["Hello", ",", "world", "."]


async def test_answer_correctness_bertscore_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    from bnbagent_eval.metrics.quality import answer_correctness as ac

    seen: dict[str, Any] = {}

    def fake_bertscore(candidate: str, refs: list[str], model: str) -> list[tuple]:
        seen["model"] = model
        return [(0.9, 0.9, 0.85)]

    monkeypatch.setattr(ac, "bertscore", fake_bertscore)
    params = {"strategy": "bertscore"}
    res = await run_metric("quality.answer_correctness", mk("A token.", "A coin."), params, "rule")
    assert res.score == 0.85 and res.passed is True  # strategy threshold 0.8
    assert seen["model"] == "microsoft/deberta-xlarge-mnli"
    empty = await run_metric("quality.answer_correctness", mk(" ", "A coin."), params, "rule")
    assert empty.score == 0.0


def test_whitebox_tool_uses_merge_id_less_calls() -> None:
    from bnbagent_eval.metrics.agent._whitebox import invocations

    call = ToolCall(name="get_balance", arguments={"a": 1})
    trace = Trace(
        id="t",
        steps=(
            AgentStep(id="a", user_prompt="q", response="r"),
            LlmStep(id="l", response="thinking", tool_calls=(call,)),
            ToolStep(id="s", call=call, result=ToolResult(content="1.5")),
        ),
    )
    uses = invocations(Session(id="s", traces=(trace,)))[0].tool_uses
    assert len(uses) == 1 and uses[0].result is not None and uses[0].thought == "thinking"


# ------------------------------------------------- judge ports
async def test_judge_prompt_conversation_placeholder_from_trajectory() -> None:
    tools = _call_trace("t1", "get_balance", {})
    agent = AgentStep(id="a", user_prompt="Balance?", response="1.5 tBNB")
    traj = Session(id="s", traces=(Trace(id="t1", steps=(agent, *tools.steps)),))
    res, judge = await run(
        "custom.judge_prompt",
        EvalCase(id="c", trajectory=traj),
        {"prompt": "Judge this history: {conversation}"},
        script={"reasoning": "r", "score": "Good"},
    )
    assert res.status == "scored"
    assert "get_balance" in judge.requests[0].user


def test_rubric_schema_accepts_source_verdict_spellings() -> None:
    from bnbagent_eval._jsonschema import validate
    from bnbagent_eval.judges.prompts.agent._whitebox import RUBRIC_SCHEMA

    reply = {"evaluations": [{"property": "p", "verdict": v} for v in ("n/r", "Yes", "pass")]}
    assert validate(reply, RUBRIC_SCHEMA) == []


def test_numeric_scale_carries_the_etk_evaluator_block() -> None:
    from bnbagent_eval.judges.prompts.custom.judge_prompt import build

    system = build("Is {output} clear?", scale="numeric").system
    assert system.endswith(
        "Evaluator definition:\n<evaluator>\ndescription: Is {output} clear?\nname: custom\n"
        "</evaluator>"
    )


def test_completeness_context_addendum_follows_the_domain_rubric() -> None:
    from bnbagent_eval.judges.prompts.quality import completeness as p

    system = p.CONTEXT_V1.system_prompt("Mention fees.")
    assert system.index("Mention fees.") < system.index("# Context-Aware Evaluation")
    assert p.CONTEXT_V1.system_prompt(None).endswith(p.CONTEXT_AWARE_ADDENDUM)


@pytest.mark.parametrize(
    "metric_id",
    [
        "conversation.coherence",
        "conversation.completeness",
        "conversation.knowledge_retention",
        "conversation.entity_consistency",
    ],
)
async def test_agentsight_conversation_judges_need_two_turns(metric_id: str) -> None:
    one = EvalCase(id="c", conversation=(Message(role="user", content="hi"),))
    res, judge = await run(metric_id, one, {}, script={})
    assert res.status == "skipped" and judge.requests == []


CONV = (Message(role="user", content="Hi?"), Message(role="assistant", content="Hello."))


@pytest.mark.parametrize(
    ("metric_id", "reply"),
    [
        ("safety.bias", {"reasoning": "r", "score": "Unbiased"}),
        ("quality.relevance", {"reasoning": "r", "score": "Good"}),
        ("quality.helpfulness", {"reasoning": "r", "score": "Good"}),
    ],
)
async def test_general_judges_accept_a_conversation_instead_of_output(metric_id, reply) -> None:
    res, judge = await run(metric_id, EvalCase(id="c", conversation=CONV), {}, script=reply)
    assert res.status != "skipped", res.error
    assert judge.requests and "Hello." in judge.requests[0].user


async def test_rule_method_still_needs_output_with_conversation_only() -> None:
    c = EvalCase(id="c", conversation=CONV, expected="Hello.")
    res = await run_metric("quality.answer_correctness", c, {}, "rule")
    assert res.status == "skipped"


# ------------------------------------------------- MetricSpec.sources cover every vendor method
def test_every_vendor_method_has_a_recorded_source() -> None:
    for spec in registry.list_specs():
        for m in spec.methods:
            if m.kind.value == "vendor":
                assert any(s.startswith(f"{m.name}:") for s in spec.sources), (spec.id, m.name)


def test_source_map_reverse_index_matches_the_registry() -> None:
    """docs/design/source-map.md §6 lists each metric's ``MetricSpec.sources`` and aliases."""
    doc = Path(__file__).resolve().parents[3] / "docs" / "design" / "source-map.md"
    section = doc.read_text(encoding="utf-8").split("## 6.", 1)[1]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == 5 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells
    aliases = registry.aliases()
    for spec in registry.list_specs():
        cells = rows[spec.id]
        assert cells[3] == "; ".join(f"`{s}`" for s in spec.sources), spec.id
        names = sorted(a for a, (mid, _) in aliases.items() if mid == spec.id)
        assert cells[4] == (", ".join(f"`{a}`" for a in names) or "–"), spec.id
    assert set(rows) == {s.id for s in registry.list_specs()}
