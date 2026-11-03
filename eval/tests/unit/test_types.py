from __future__ import annotations

import pytest

from bnbagent_eval.exceptions import SchemaError
from bnbagent_eval.types import (
    AgentSubject,
    ContentPart,
    EvalCase,
    EvalRequest,
    JobSubject,
    Message,
    RunOptions,
    Session,
    ToolCall,
    ToolStep,
    Trace,
    TxStep,
)
from bnbagent_eval.types.migrate import upgrade


def _session() -> Session:
    return Session(
        id="s",
        traces=(
            Trace(
                id="t",
                steps=(
                    ToolStep(
                        id="1",
                        call=ToolCall(name="search", arguments={"q": "bnb"}),
                        start_ms=0,
                        end_ms=5,
                    ),
                    TxStep(id="2", chain_id=97, to="0xabc", value=10),
                ),
            ),
        ),
    )


def test_case_roundtrip_with_polymorphic_steps() -> None:
    case = EvalCase(
        id="c",
        input=(ContentPart(type="text", text="hi"),),
        output={"a": 1},
        trajectory=_session(),
        conversation=(Message(role="user", content="hi"),),
    )
    again = EvalCase.from_dict(case.to_dict())
    assert again == case
    assert isinstance(again.trajectory.traces[0].steps[1], TxStep)
    assert again.trajectory.tool_steps()[0].duration_ms == 5


def test_case_has() -> None:
    case = EvalCase(id="c", output="", trajectory=_session())
    assert case.has("output") and not case.has("contexts") and not case.has("input")
    assert case.has("trajectory") and case.has("tx")


def test_subject_union_discriminator() -> None:
    job = JobSubject(chain_id=97, commerce="0x1", job_id=7, provider="0x2", output_hash="0x3")
    agent = AgentSubject(chain_id=97, registry="0x4", agent_id=9)
    for subject in (job, agent):
        req = EvalRequest.from_dict(EvalRequest(subject=subject).to_dict())
        assert req.subject == subject


def test_unknown_fields() -> None:
    with pytest.raises(SchemaError, match="unknown field"):
        EvalCase.from_dict({"id": "x", "outptu": "typo"})
    req = EvalRequest.from_dict({"stage": "dev", "future": {"x": 1}})
    assert req.extensions == {"future": {"x": 1}}
    assert req.to_dict()["future"] == {"x": 1}


def test_type_errors_are_schema_errors() -> None:
    with pytest.raises(SchemaError):
        EvalRequest.from_dict({"stage": "not-a-stage"})
    with pytest.raises(SchemaError):
        EvalCase.from_dict({"id": 3})
    with pytest.raises(SchemaError, match="missing required"):
        EvalCase.from_dict({})


def test_run_options_validation() -> None:
    with pytest.raises(ValueError):
        RunOptions(runs=0)


def test_schema_versions() -> None:
    assert upgrade({"schema_version": "bnbagent-eval/v1"})["schema_version"] == "bnbagent-eval/v1"
    with pytest.raises(SchemaError, match="newer"):
        upgrade({"schema_version": "bnbagent-eval/v2"})
    with pytest.raises(SchemaError):
        upgrade({"schema_version": "other/v1"})
