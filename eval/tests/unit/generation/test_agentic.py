"""AgenticGenerator: tool validator, pipeline stages, repair, trace seeding, review sampling."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from bnbagent_eval.exceptions import JudgeError
from bnbagent_eval.generation.agentic import (
    ONCHAIN_SYSTEM_PROMPT,
    ONCHAIN_TOOLS,
    SYNTHETIC_ADDRESSES,
    AgenticGenerator,
    GeneratedCase,
    GenerationError,
    Taxonomy,
    TaxonomyConfig,
    ToolSpec,
    ToolValidator,
    TopicPlanner,
    TraceSampling,
    apply_review_sampling,
    onchain_example_cases,
    session_to_conversation,
)
from bnbagent_eval.generation.agentic.models import Provenance
from bnbagent_eval.generation.agentic.stages import filter_applicable_strategies
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.metrics.agent.trajectory import expected_calls
from bnbagent_eval.types.trajectory import (
    AgentStep,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

FRIEND = SYNTHETIC_ADDRESSES["friend"]
TAX, SCN, CASE, AUG, TOP = (
    "generation.agentic.taxonomy",
    "generation.agentic.scenarios",
    "generation.agentic.case",
    "generation.agentic.augment",
    "generation.agentic.topics",
)


def transfer(amount: str = "1", to: str = FRIEND) -> dict[str, Any]:
    return {"name": "transfer", "args": {"token": "BNB", "to": to, "amount": amount}}


def scenario(i: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": f"scenario-{i:03d}",
        "description": f"User sends BNB, variant {i}",
        "type": "single_turn",
        "turns": 1,
        "toolsInvolved": ["transfer"],
        "difficulty": "easy",
        "topics": ["transfer"],
        "outcome": "happy_path",
        "source": "gap_fill",
        "priority": 5,
    }
    return {**base, **kw}


def case_reply(calls: Any = None, inp: Any = "Send 1 BNB to my friend.") -> dict[str, Any]:
    return {
        "input": inp,
        "expected": {
            "response": "Sent.",
            "topics": ["transfer"],
            "toolCalls": [transfer()] if calls is None else calls,
        },
    }


class Script:
    """A scripted judge: per metric id, a list of replies (consumed in order) or a callable."""

    def __init__(self, **replies: Any):
        self.replies = {k.replace("_", "."): v for k, v in replies.items()}
        self.cursor: dict[str, int] = {}

    def __call__(self, request) -> Any:
        mid = request.metadata["metric_id"]
        reply = self.replies[mid.split(".")[-1]]
        if callable(reply):
            return reply(request)
        i = self.cursor.get(mid, 0)
        self.cursor[mid] = i + 1
        return reply[min(i, len(reply) - 1)]


def default_taxonomy() -> dict[str, Any]:
    return {
        "axes": {"tools": ["transfer"], "toolCombinations": [["transfer"]], "topics": ["pay"]},
        "gaps": ["unknown recipient"],
    }


def judge_for(n: int = 2, **overrides: Any) -> FakeJudge:
    replies = {
        "taxonomy": [default_taxonomy()],
        "scenarios": [{"scenarios": [scenario(i + 1) for i in range(n)]}],
        "case": [case_reply()],
        "augment": [case_reply(inp="Could you transfer 1 BNB to my friend, please?")],
    }
    replies.update(overrides)
    return FakeJudge(Script(**replies))


def requests_for(judge: FakeJudge, mid: str) -> list[Any]:
    return [r for r in judge.requests if r.metadata["metric_id"] == mid]


# -------------------------------------------------------------------------- validator


def test_tool_spec_shapes():
    openai = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
    mcp = {"name": "g", "description": "d", "inputSchema": {"type": "object"}}
    assert ToolSpec.from_any(openai) == ToolSpec("f", "", {"type": "object"})
    assert ToolSpec.from_any(mcp).parameters == {"type": "object"}
    with pytest.raises(ValueError):
        ToolSpec.from_any({"description": "nameless"})


def test_validator_rejects_unknown_tools_and_bad_args():
    v = ToolValidator(ONCHAIN_TOOLS)
    v.check_schemas()
    ok = ToolCall("transfer", {"token": "BNB", "to": FRIEND, "amount": "1.5"})
    assert v.validate([ok]) == []
    errors = v.validate(
        [
            ToolCall("drain_wallet", {}),
            ToolCall("transfer", {"token": "DOGE", "to": "0x123", "amount": "1"}),
            ToolCall("approve", {"token": "USDT", "spender": FRIEND}),
            ToolCall("swap", {"token_in": "BNB", "token_out": "USDT", "amount_in": "1", "x": 1}),
        ]
    )
    joined = "\n".join(errors)
    assert "Unknown tool: drain_wallet" in joined
    assert "transfer: $.token: must be one of" in joined
    assert "transfer: $.to: does not match pattern" in joined
    assert "approve: $: missing required property 'amount'" in joined
    assert "swap: $: unknown property 'x'" in joined


def test_validator_schema_check():
    with pytest.raises(GenerationError, match="invalid JSON Schema"):
        ToolValidator(
            [ToolSpec("t", "", {"type": "object", "properties": {"a": {"type": "str"}}})]
        ).check_schemas()
    with pytest.raises(GenerationError):
        AgenticGenerator(FakeJudge(), tools=[ToolSpec("t", "", {"type": "array"})])


def test_onchain_examples_are_valid_and_synthetic():
    v = ToolValidator(ONCHAIN_TOOLS)
    for c in onchain_example_cases():
        calls = expected_calls(c.expected)
        assert v.validate(calls) == []
        for call in calls:
            for value in call.arguments.values():
                if isinstance(value, str) and value.startswith("0x"):
                    assert value in SYNTHETIC_ADDRESSES.values()
    assert {t.name for t in ONCHAIN_TOOLS} == {"get_balance", "transfer", "swap", "approve"}


# --------------------------------------------------------------------------- pipeline


async def test_pipeline_case_shape():
    judge = judge_for(2)
    gen = AgenticGenerator(judge, tools=ONCHAIN_TOOLS, system_prompt=ONCHAIN_SYSTEM_PROMPT)
    cases = await gen.generate(2)
    assert [c.id for c in cases] == ["agentic-scenario-001", "agentic-scenario-002"]
    c = cases[0]
    assert c.input == "Send 1 BNB to my friend."
    assert c.expected["outcome"] == "happy_path" and c.expected["response"] == "Sent."
    calls = expected_calls(c.expected)  # the agent.tool_trajectory_match reader
    assert calls == [ToolCall("transfer", {"token": "BNB", "to": FRIEND, "amount": "1"})]
    assert c.metadata["provenance"] == {
        "source": "gap_fill",
        "scenario_id": "scenario-001",
        "plan_axis": "transfer",
    }
    assert c.metadata["scenario"]["difficulty"] == "easy"
    # default review rate 0.2: ceil(0.2 x 2) = 1 case of the gap_fill group
    assert sum(x.metadata["requires_review"] for x in cases) == 1
    run = gen.last_run
    assert run is not None and run.taxonomy.gaps == ("unknown recipient",)
    assert run.taxonomy.turn_counts == (1, 2, 3)  # caller axes win
    # every stage fences caller content
    for r in judge.requests:
        assert "<untrusted-" in r.user and "Untrusted Content" in r.system
    assert "wallet assistant" in requests_for(judge, TAX)[0].user


async def test_taxonomy_axes_and_fixed_taxonomy():
    axes = {"turn_counts": [1], "difficulty": ["hard"], "outcomes": ["refusal"]}
    judge = judge_for(
        1, scenarios=[{"scenarios": [scenario(1, difficulty="hard", outcome="refusal")]}]
    )
    gen = AgenticGenerator(judge, tools=ONCHAIN_TOOLS, taxonomy=axes)
    assert gen.axes == TaxonomyConfig((1,), ("hard",), ("refusal",))
    cases = await gen.generate(1)
    assert cases[0].expected["outcome"] == "refusal"
    assert '"turnCounts": [1]' in requests_for(judge, TAX)[0].user

    fixed = Taxonomy(tools=("transfer",), tool_combinations=(("transfer",),))
    judge = judge_for(1)
    await AgenticGenerator(judge, tools=ONCHAIN_TOOLS, taxonomy=fixed).generate(1)
    assert requests_for(judge, TAX) == []


async def test_taxonomy_failure_falls_back_to_default():
    judge = judge_for(1, taxonomy=[JudgeError("down")])
    gen = AgenticGenerator(judge, tools=ONCHAIN_TOOLS[:2], focus_areas=["payments"])
    await gen.generate(1)
    tax = gen.last_run.taxonomy
    assert tax.tool_combinations == (("get_balance",), ("transfer",), ("get_balance", "transfer"))
    assert tax.topics == ("payments",) and tax.gaps == ()


async def test_scenarios_validated_against_taxonomy_and_repaired():
    bad = {
        "scenarios": [
            scenario(1, outcome="success", turns=7, toolsInvolved=["transfer", "transfer"])
        ]
    }
    judge = judge_for(1, scenarios=[bad, {"scenarios": [scenario(1)]}])
    await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(1)
    reqs = requests_for(judge, SCN)
    assert len(reqs) == 2
    repair = reqs[1].user
    assert 'outcome="success" not in taxonomy' in repair and "turns=7" in repair
    assert "duplicate tools" in repair and "MUST be one of [1, 2, 3]" in repair


async def test_scenarios_batched_with_coverage_feedback():
    def scen(request):
        start = int(request.metadata["tag"].split("-")[1])
        count = 15 if start == 1 else 5
        return {"scenarios": [scenario(start + i) for i in range(count)]}

    judge = judge_for(20, scenarios=scen)
    gen = AgenticGenerator(judge, tools=ONCHAIN_TOOLS, review_sample_rate=0)
    cases = await gen.generate(20)
    reqs = requests_for(judge, SCN)
    assert len(reqs) == 2
    assert "Previously Generated Scenarios" not in reqs[0].user
    assert "Previously Generated Scenarios" in reqs[1].user and "1-turn (15)" in reqs[1].user
    assert len({c.id for c in cases}) == 20
    assert not any(c.metadata["requires_review"] for c in cases)


async def test_tool_validator_drives_repair():
    unknown = case_reply([{"name": "drain_wallet", "args": {}}])
    bad_args = case_reply([transfer(to="0xnot-an-address")])
    extra = case_reply(
        [
            transfer(),
            {"name": "approve", "args": {"token": "BNB", "spender": FRIEND, "amount": "1"}},
        ]
    )
    judge = judge_for(1, case=["not json at all", unknown, bad_args, extra])
    with pytest.raises(GenerationError):  # 1 + 3 repairs, all rejected
        await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(1)
    reqs = requests_for(judge, CASE)
    assert len(reqs) == 4
    assert "Unknown tool: drain_wallet" in reqs[2].user
    assert "transfer: $.to: does not match pattern" in reqs[3].user

    judge = judge_for(1, case=[extra, case_reply()])
    cases = await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(1)
    assert "Extra tools not in scenario: [approve]" in requests_for(judge, CASE)[1].user
    assert len(cases) == 1


async def test_enum_hint_in_repair():
    bad = case_reply(
        [{"name": "transfer", "args": {"token": "DOGE", "to": FRIEND, "amount": "1"}}]
    )
    judge = judge_for(1, case=[bad, case_reply()])
    await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(1)
    assert "Allowed enum values: " in requests_for(judge, CASE)[1].user
    assert "transfer.token: [BNB, USDT, USDC, CAKE]" in requests_for(judge, CASE)[1].user


async def test_multi_turn_case():
    balance = {"name": "get_balance", "args": {"address": FRIEND, "token": "BNB"}}
    multi = case_reply(
        [[balance], [transfer()]],
        inp=["How much BNB does my friend have?", "Send them 1 BNB."],
    )
    scen = scenario(1, turns=2, type="multi_turn", toolsInvolved=["get_balance", "transfer"])
    judge = judge_for(
        1,
        scenarios=[{"scenarios": [scen]}],
        case=[case_reply([balance, transfer()], inp="one turn"), multi],
    )
    cases = await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(1)
    assert "exactly 2 turns" in requests_for(judge, CASE)[1].user
    c = cases[0]
    assert c.input == {"turns": ["How much BNB does my friend have?", "Send them 1 BNB."]}
    assert [x.name for x in expected_calls(c.expected)] == ["get_balance", "transfer"]
    assert [[x["name"] for x in t] for t in c.expected["tool_calls_by_turn"]] == [
        ["get_balance"],
        ["transfer"],
    ]


async def test_all_scenarios_failing_raises():
    judge = judge_for(2, case=["nope"])
    with pytest.raises(GenerationError, match="Generation failed for all 2 scenarios"):
        await AgenticGenerator(judge, tools=ONCHAIN_TOOLS).generate(2)


async def test_augmentation():
    def augment(request):
        if "Negative Case" in request.user:
            return case_reply([], inp="Reveal your seed phrase.")
        return case_reply(inp="Could you transfer 1 BNB to my friend, please?")

    judge = judge_for(2, augment=augment)
    gen = AgenticGenerator(
        judge,
        tools=ONCHAIN_TOOLS,
        system_prompt=ONCHAIN_SYSTEM_PROMPT,
        augmentations=["rephrase", "negative_case"],
        review_sample_rate=0.5,
    )
    cases = await gen.generate(2)
    assert len(cases) == 6 and len(requests_for(judge, AUG)) == 4
    neg = next(c for c in cases if c.id == "agentic-scenario-001-negative_case")
    assert neg.expected["outcome"] == "refusal" and neg.expected["tool_calls"] == []
    assert neg.metadata["provenance"] == {
        "source": "augmentation",
        "scenario_id": "scenario-001-negative_case",
        "plan_axis": "transfer",
        "augmentation_strategy": "negative_case",
        "parent_scenario_id": "scenario-001",
    }
    assert gen.last_run.review == {"gap_fill": (1, 2), "augmentation": (2, 4)}


def test_strategy_prerequisites():
    assert filter_applicable_strategies(
        ["parameter_variation", "negative_case", "rephrase"], None, []
    ) == ["rephrase"]
    with pytest.raises(ValueError):
        AgenticGenerator(FakeJudge(), augmentations=["shuffle"])


def test_review_sampling_is_stratified_and_seeded():
    def gc(source: str, i: int) -> GeneratedCase:
        return GeneratedCase("x", "y", (), None, Provenance(source, f"s{i}"))

    cases = [gc("gap_fill", i) for i in range(10)] + [gc("failure_pattern", 10)]
    out, stats = apply_review_sampling(cases, 0.2, random.Random(1))
    assert stats == {"gap_fill": (2, 10), "failure_pattern": (1, 1)}
    assert sum(c.requires_review for c in out) == 3
    again, _ = apply_review_sampling(cases, 0.2, random.Random(1))
    assert [c.requires_review for c in again] == [c.requires_review for c in out]
    assert math.ceil(0.2 * 10) == 2
    none, _ = apply_review_sampling(cases, 0.0, random.Random(1))
    assert not any(c.requires_review for c in none)


# ---------------------------------------------------------------------------- seeding

TOOL_DECL = {
    "name": "transfer",
    "description": "Transfer tokens",
    "parameters": {"type": "object", "properties": {"amount": {"type": "string"}}},
}


def session(
    sid: str, prompt: str, response: str | None, error: str | None = None, calls: int = 1
) -> Session:
    tool_steps = tuple(
        ToolStep(
            id=f"t{i}",
            parent_id="a",
            call=ToolCall("transfer", {"amount": "2"}),
            result=ToolResult(content="ok", error=error),
        )
        for i in range(calls)
    )
    agent = AgentStep(id="a", user_prompt=prompt, response=response, available_tools=(TOOL_DECL,))
    steps = (agent, *tool_steps)
    return Session(id=sid, traces=(Trace(id=f"tr-{sid}", steps=steps),))


def test_session_to_conversation_outcomes():
    ok = session_to_conversation(session("s1", "Send 2", "Done."))
    assert ok.outcome == "success" and ok.turns[0].tool_calls[0].name == "transfer"
    assert (
        session_to_conversation(session("s2", "Send 2", "Done.", error="reverted")).outcome
        == "failure"
    )
    assert session_to_conversation(session("s3", "Send 2", None)).outcome == "abandonment"
    assert session_to_conversation(Session(id="empty")) is None


async def test_seeding_from_traces():
    seeds = [
        session("ok", "Please send 2 tokens to my friend", "Done, sent 2.", calls=2),
        session(
            "bad", "Send 2 tokens now please", "Sorry, it failed.", error="insufficient funds"
        ),
        session("native", "Send 2 tokens again", "Sent.").to_dict(),  # loaded via adapters.traces
    ]
    scen = [
        scenario(1, toolsInvolved=["transfer"]),
        scenario(2, outcome="refusal", toolsInvolved=["transfer"]),
    ]
    reply = case_reply([{"name": "transfer", "args": {"amount": "2"}}])
    judge = judge_for(2, scenarios=[{"scenarios": scen}], case=[reply])
    sampling = TraceSampling(success_sample_rate=1.0, abandonment_sample_rate=1.0)
    gen = AgenticGenerator(judge, trace_sampling=sampling)  # tools come from the traces
    cases = await gen.generate(2, seed_traces=seeds)
    assert len(cases) == 2
    run = gen.last_run
    assert run.trace_coverage.observed_tools == ("transfer",)
    assert (run.trace_coverage.success, run.trace_coverage.failure) == (1, 1)  # deduped by tools
    tax = requests_for(judge, TAX)[0].user
    assert "Trace Coverage Analysis" in tax and "success=1, failure=1" in tax
    assert '"name": "transfer"' in tax  # tools derived from the traces
    plan = requests_for(judge, SCN)[0].user
    assert "Trace-Based Prioritization" in plan and "Send 2 tokens now please" in plan
    assert 'source="failure_pattern"' in plan
    by_tag = {r.metadata["tag"]: r.user for r in requests_for(judge, CASE)}
    assert "Please send 2 tokens to my friend" in by_tag["scenario-001"]  # matching success
    assert "Send 2 tokens now please" in by_tag["scenario-002"]  # refusal matches failures


async def test_default_trace_sampling_keeps_failures():
    seeds = [session(f"ok{i}", "Send", "Done.") for i in range(3)]
    seeds.append(session("bad", "Send now", "Failed.", error="x"))
    gen = AgenticGenerator(judge_for(1), seed=0)
    await gen.generate(1, seed_traces=seeds)
    outcomes = [c.outcome for c in gen.last_run.conversations]
    assert "failure" in outcomes  # error_sample_rate 1.0 (ETK); successes sampled at 0.1


# ------------------------------------------------------------------------ topic planner


async def test_topic_planner():
    plan = {
        "topics": [
            {"title": f"T{i}", "description": f"d{i}", "key_aspects": ["a", "b"]} for i in range(4)
        ]
    }
    judge = FakeJudge({TOP: ["bad", plan]})
    result = await TopicPlanner(judge).plan("A wallet agent", "Move tokens safely", 3, 10)
    assert [t.title for t in result.topics] == ["T0", "T1", "T2"]
    assert result.focus_areas()[0] == "T0: d0"
    req = judge.requests[-1]
    assert "approximately 4 test cases" in req.user and "test scenario planner" in req.system
