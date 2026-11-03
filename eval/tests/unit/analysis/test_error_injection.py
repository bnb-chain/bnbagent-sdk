"""generation.error_injection: balanced split, text and whitebox faults, judge edits."""

from __future__ import annotations

import pytest

from bnbagent_eval.generation.error_injection import (
    FAULT_TYPES,
    TEXT_FAULTS,
    TOOL_FAULTS,
    inject_errors,
    inject_fault,
    make_balanced_dataset,
)
from bnbagent_eval.judges.providers.fake import FakeJudge
from bnbagent_eval.types import (
    AgentStep,
    EvalCase,
    Message,
    Session,
    ToolCall,
    ToolResult,
    ToolStep,
    Trace,
)

SELECT = "generation.error_injection.select"
EDIT = "generation.error_injection.edit"
ANSWER = "Your balance is 12 BNB. The transfer can be sent today. Fees are 0.5 BNB."


def _cases(n: int = 6) -> list[EvalCase]:
    return [EvalCase(id=f"c{i}", input="What is my balance?", output=ANSWER) for i in range(n)]


def _traced() -> EvalCase:
    steps = (
        AgentStep(id="a", user_prompt="What is my balance?", response=ANSWER),
        ToolStep(
            id="t",
            call=ToolCall(name="get_balance", arguments={"address": "0xabc", "chain": 56}),
            result=ToolResult(content="12"),
        ),
    )
    session = Session(id="s", traces=(Trace(id="tr", steps=steps),))
    return EvalCase(id="w", input="What is my balance?", output=ANSWER, trajectory=session)


def test_balanced_split_is_deterministic() -> None:
    a = make_balanced_dataset(_cases(), evaluators=("quality.helpfulness", "x"), seed=7)
    b = make_balanced_dataset(_cases(), evaluators=("quality.helpfulness", "x"), seed=7)
    assert a == b
    assert len(a.clean) == len(a.faulty) == 6 and not a.errors
    assert all(c.metadata["label"] == "pass" for c in a.clean)
    assert all(c.metadata["label"] == "fail" for c in a.faulty)
    assert a.clean[0].id.startswith("quality.helpfulness_pass_c")
    assert all(c.output != ANSWER for c in a.faulty) and all(c.output == ANSWER for c in a.clean)
    assert set(a.labels().values()) == {0.0, 1.0}
    other = make_balanced_dataset(_cases(), evaluators=("quality.helpfulness",), seed=8)
    assert [c.id for c in other.faulty] != [c.id for c in a.faulty[:3]] or (
        [c.output for c in other.faulty] != [c.output for c in a.faulty[:3]]
    )
    odd = make_balanced_dataset(_cases(5), seed=1)
    assert (len(odd.clean), len(odd.faulty)) == (2, 3)  # source: floor half pass
    with pytest.raises(ValueError):
        make_balanced_dataset(_cases(), fail_ratio=1.5)


@pytest.mark.parametrize("fault", TEXT_FAULTS)
def test_each_text_fault(fault: str) -> None:
    case = _cases(1)[0]
    mutated, info = inject_fault(case, fault, seed=3)
    assert info.fault == fault and info.target_role == "agent" and info.injection_turn == 1
    assert info.original_text == ANSWER and mutated.output == info.modified_text != ANSWER
    assert mutated.input == case.input and info.error_description
    if fault == "wrong_number":
        assert any(n not in mutated.output for n in ("12", "0.5"))
    if fault == "negate":
        assert "is not" in mutated.output
    if fault == "truncate":
        assert len(mutated.output) < len(ANSWER)
    assert inject_fault(case, fault, seed=3) == (mutated, info)


def test_fault_fallback_and_errors() -> None:
    short = EvalCase(id="s", input="q", output="Hi there")
    mutated, info = inject_fault(short, "truncate", seed=0)
    assert info.fault != "truncate" and mutated.output != "Hi there"
    with pytest.raises(ValueError):
        inject_fault(short, "no_such_fault")
    with pytest.raises(ValueError):
        inject_fault(EvalCase(id="e", input="q"))
    ds = make_balanced_dataset([EvalCase(id="e", input="q"), EvalCase(id="f", input="q")])
    assert len(ds.faulty) == 0 and len(ds.errors) == 1


@pytest.mark.parametrize("fault", TOOL_FAULTS)
def test_whitebox_tool_faults(fault: str) -> None:
    case = _traced()
    mutated, info = inject_fault(case, fault, seed=1)
    assert info.target_role == "tool" and mutated.output == ANSWER
    assert mutated.trajectory is not None
    tools = mutated.trajectory.tool_steps()
    if fault == "tool_error":
        assert tools[0].result is not None and tools[0].result.error
    elif fault == "wrong_tool_args":
        assert tools[0].call.arguments != {"address": "0xabc", "chain": 56}
    else:
        assert tools == []
    # Text faults on a traced case keep the trajectory's final response in sync.
    text_case, _ = inject_fault(case, "hallucinate", seed=1)
    assert text_case.trajectory is not None
    assert text_case.trajectory.final_response() == text_case.output != ANSWER
    # Tool faults are only drawn for whitebox cases.
    assert inject_fault(case, "tool_error", whitebox=False)[1].fault in TEXT_FAULTS
    assert set(FAULT_TYPES) == set(TEXT_FAULTS) | set(TOOL_FAULTS)


def test_conversation_turn_is_edited_in_place() -> None:
    conv = (
        Message(role="user", content="Send 1 BNB to Bob."),
        Message(role="assistant", content="I can do that. Please confirm the address."),
        Message(role="user", content="Confirmed."),
        Message(role="assistant", content="Done. The transfer is complete."),
    )
    case = EvalCase(id="conv", conversation=conv, output="Done. The transfer is complete.")
    mutated, info = inject_fault(case, "hallucinate", seed=0)
    # Middle agent turn (AgentSight fallback): index of agent turns [1, 3] -> 3.
    assert info.injection_turn == 3
    assert mutated.conversation[:3] == conv[:3]
    assert mutated.output == mutated.conversation[3].content != conv[3].content


async def test_judge_assisted_injection() -> None:
    script = {
        SELECT: {"turn_index": 1, "role": "agent", "edit_suggestion": "Change the amount."},
        EDIT: {"rewritten_message": "Your balance is 21 BNB.", "error_description": "amount"},
    }
    judge = FakeJudge(script)
    ds = await inject_errors(
        _cases(4),
        judge=judge,
        evaluators=("quality.correctness",),
        criteria={"quality.correctness": "Is every amount correct?"},
        seed=2,
    )
    assert len(ds.clean) == len(ds.faulty) == 2
    edited = ds.faulty[0]
    assert edited.output == "Your balance is 21 BNB."
    assert edited.metadata["injection_source"] == "judge"
    assert edited.metadata["edit_suggestion"] == "Change the amount."
    select = judge.requests[0]
    assert "Is every amount correct?" in select.system and "<untrusted-" in select.user
    assert "only sees the conversation text" in select.system  # blackbox visibility
    # Same seed and no judge: same split as the deterministic builder.
    plain = await inject_errors(_cases(4), evaluators=("quality.correctness",), seed=2)
    assert [c.id for c in plain.clean] == [c.id for c in ds.clean]


async def test_judge_failure_falls_back_to_rules() -> None:
    judge = FakeJudge({SELECT: "not json", EDIT: {"rewritten_message": ANSWER}})
    ds = await inject_errors(_cases(2), judge=judge, seed=0)
    assert len(ds.faulty) == 1
    meta = ds.faulty[0].metadata
    assert meta["injection_source"] == "rules" and "fallback_reason" in meta
    unchanged = FakeJudge(
        {SELECT: {"turn_index": 9, "edit_suggestion": "x"}, EDIT: {"rewritten_message": ANSWER}}
    )
    ds = await inject_errors(_cases(2), judge=unchanged, seed=0)
    assert "unchanged" in ds.faulty[0].metadata["fallback_reason"]
