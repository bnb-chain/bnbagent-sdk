"""Error injection: balanced clean / faulty datasets for judge validation.

Ported from AgentSight ``agent_cost_reduction/error_injector.py``: per evaluator the cases are
shuffled and split in half; one half is cloned unchanged (``label="pass"``), the other half gets
one injected error (``label="fail"``). Clones get the id ``<evaluator>_<label>_<case id>`` and
the injection metadata (target role, turn, edit suggestion, error description, original and
modified text). Only one message changes, so the rest of the conversation stays as it was.

The source always asks an LLM to pick and rewrite the message. Here the default is a set of
deterministic fault types (ours), seeded per case so a dataset is reproducible; the source's
two-step LLM edit (select-and-plan, then execute) is available with ``judge=`` in
:func:`inject_errors` and falls back to a deterministic fault when the judge reply is unusable.
Whitebox faults (tool errors, wrong arguments, dropped tool calls) edit the trajectory.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..exceptions import EvalError
from ..hashing import hash_obj
from ..judges.base import JudgeModel, JudgeRequest
from ..judges.fencing import fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.prompts.analysis.error_injection import (
    BLACKBOX_VISIBILITY,
    EDIT_V1,
    SELECT_V1,
    WHITEBOX_VISIBILITY,
)
from ..judges.structured import parse
from ..types.case import EvalCase, content_text
from ..types.trajectory import AgentStep, LlmStep, Session, ToolResult, ToolStep

__all__ = [
    "FAULT_TYPES",
    "TEXT_FAULTS",
    "TOOL_FAULTS",
    "InjectionDataset",
    "InjectionInfo",
    "inject_errors",
    "inject_fault",
    "make_balanced_dataset",
]

TEXT_FAULTS = (
    "truncate",  # incomplete answer
    "negate",  # the answer states the opposite
    "wrong_number",  # a number / amount / date is wrong
    "drop_sentence",  # a key piece of information is missing
    "hallucinate",  # an unsupported claim is added
    "off_topic",  # the answer does not address the request
)
TOOL_FAULTS = (
    "tool_error",  # a tool call failed and the failure is in the trace
    "wrong_tool_args",  # a tool was called with a wrong argument value
    "drop_tool_call",  # a needed tool call is missing from the trace
)
FAULT_TYPES = TEXT_FAULTS + TOOL_FAULTS

_NEGATIONS = (
    (r"\bis not\b", "is"),
    (r"\bisn't\b", "is"),
    (r"\bcannot\b", "can"),
    (r"\bcan't\b", "can"),
    (r"\bdoes not\b", "does"),
    (r"\bdo not\b", "do"),
    (r"\bwill not\b", "will"),
    (r"\bis\b", "is not"),
    (r"\bare\b", "are not"),
    (r"\bcan\b", "cannot"),
    (r"\bwill\b", "will not"),
    (r"\bshould\b", "should not"),
    (r"\byes\b", "no"),
    (r"\bsucceeded\b", "failed"),
    (r"\bsuccessful\b", "unsuccessful"),
)
_HALLUCINATIONS = (
    "This was independently confirmed by an external audit last week.",
    "Note that this also qualifies you for a 20% refund on all previous transactions.",
    "The official documentation guarantees this result in every case.",
    "This has already been completed on your behalf, so no further action is needed.",
)
_OFF_TOPIC = (
    "Thanks for reaching out! Our office hours are Monday to Friday, 9am to 5pm.",
    "Here is a fun fact: the first computer bug was an actual moth found in a relay.",
    "I recommend checking our newsletter for the latest product announcements.",
)
_DESCRIPTIONS = {
    "truncate": "The response was cut short and no longer completes the answer.",
    "negate": "A statement in the response was inverted so it contradicts the correct answer.",
    "wrong_number": "A number in the response was changed to an incorrect value.",
    "drop_sentence": "A sentence carrying key information was removed from the response.",
    "hallucinate": "An unsupported, fabricated claim was added to the response.",
    "off_topic": "The response was replaced with one that does not address the request.",
    "tool_error": "A tool call now fails in the trace and the agent ignores the failure.",
    "wrong_tool_args": "A tool was called with a wrong argument value.",
    "drop_tool_call": "A tool call the answer relies on was removed from the trace.",
}


@dataclass(frozen=True)
class InjectionInfo:
    fault: str
    target_role: str
    injection_turn: int | None
    original_text: str
    modified_text: str
    error_description: str
    edit_suggestion: str = ""
    source: str = "rules"  # "rules" | "judge"
    fallback_reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if d["fallback_reason"] is None:
            del d["fallback_reason"]
        d["injection_fault"] = d.pop("fault")
        d["injection_source"] = d.pop("source")
        return d


@dataclass(frozen=True)
class InjectionDataset:
    """Clean (``label="pass"``) and faulty (``label="fail"``) clones, clean first."""

    clean: tuple[EvalCase, ...]
    faulty: tuple[EvalCase, ...]
    errors: tuple[str, ...] = field(default=())  # cases that could not be injected

    @property
    def cases(self) -> tuple[EvalCase, ...]:
        return self.clean + self.faulty

    def labels(self) -> dict[str, float]:
        """Reference scores for calibration: pass -> 1.0, fail -> 0.0."""
        return {c.id: 1.0 if c.metadata.get("label") == "pass" else 0.0 for c in self.cases}


# --------------------------------------------------------------------------------- helpers
def _rng(seed: int, *parts: str) -> random.Random:
    digest = hashlib.sha256("|".join((str(seed), *parts)).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _turns(case: EvalCase) -> list[tuple[str, str]]:
    """Editable (role, text) turns: the conversation, else input / output (AgentSight)."""
    if case.conversation:
        return [
            ("agent" if m.role == "assistant" else m.role, m.content) for m in case.conversation
        ]
    out = []
    if case.input is not None:
        out.append(("user", content_text(case.input) or ""))
    output = content_text(case.output)
    if output is None and case.trajectory is not None:
        output = case.trajectory.final_response()
    if output is not None:
        out.append(("agent", output))
    return out


def _agent_turn(turns: Sequence[tuple[str, str]]) -> int | None:
    agent = [i for i, (role, text) in enumerate(turns) if role == "agent" and text.strip()]
    return agent[len(agent) // 2] if agent else None  # AgentSight fallback: the middle turn


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _mutate_text(fault: str, text: str, rng: random.Random) -> str | None:
    """Apply a text fault, or return None if it does not apply to ``text``."""
    if fault == "truncate":
        words = text.split()
        if len(words) < 4:
            return None
        return " ".join(words[: max(2, int(len(words) * 0.4))])
    if fault == "negate":
        for pattern, repl in _NEGATIONS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return re.sub(pattern, repl, text, count=1, flags=re.IGNORECASE)
        return None
    if fault == "wrong_number":
        numbers = list(re.finditer(r"\d+(?:\.\d+)?", text))
        if not numbers:
            return None
        m = rng.choice(numbers)
        value = m.group()
        if "." in value:
            new = f"{float(value) * rng.choice((2, 3, 10)):.{len(value.split('.')[1])}f}"
        else:
            new = str(int(value) + rng.randint(1, 9) * (10 ** max(0, len(value) - 1)))
        return text[: m.start()] + new + text[m.end() :]
    if fault == "drop_sentence":
        sentences = _sentences(text)
        if len(sentences) < 2:
            return None
        del sentences[rng.randrange(len(sentences))]
        return " ".join(sentences)
    if fault == "hallucinate":
        return f"{text.rstrip()} {rng.choice(_HALLUCINATIONS)}"
    if fault == "off_topic":
        return rng.choice(_OFF_TOPIC)
    raise ValueError(f"unknown text fault {fault!r}")


def _replace_text(case: EvalCase, turn: int, old: str, new: str) -> EvalCase:
    """Set turn ``turn`` to ``new``; keep output and trajectory responses in sync."""
    changes: dict[str, Any] = {}
    if case.conversation:
        msgs = list(case.conversation)
        msgs[turn] = dataclasses.replace(msgs[turn], content=new)
        changes["conversation"] = tuple(msgs)
        if content_text(case.output) == old:
            changes["output"] = new
    else:
        user_turn = case.input is not None and turn == 0
        changes["input" if user_turn else "output"] = new
    if case.trajectory is not None:
        changes["trajectory"] = _patch_session(case.trajectory, old, new)
    return dataclasses.replace(case, **changes)


def _patch_session(session: Session, old: str, new: str) -> Session:
    def patch(step: Any) -> Any:
        if isinstance(step, AgentStep):
            if step.response == old:
                return dataclasses.replace(step, response=new)
            if step.user_prompt == old:
                return dataclasses.replace(step, user_prompt=new)
        if isinstance(step, LlmStep) and step.response == old:
            return dataclasses.replace(step, response=new)
        return step

    traces = tuple(
        dataclasses.replace(t, steps=tuple(patch(s) for s in t.steps)) for t in session.traces
    )
    return dataclasses.replace(session, traces=traces)


def _tool_fault(case: EvalCase, fault: str, rng: random.Random) -> tuple[EvalCase, str, str]:
    session = case.trajectory
    assert session is not None
    tools = [
        (ti, si)
        for ti, t in enumerate(session.traces)
        for si, s in enumerate(t.steps)
        if isinstance(s, ToolStep)
    ]
    ti, si = rng.choice(tools)
    step = session.traces[ti].steps[si]
    assert isinstance(step, ToolStep)
    steps = list(session.traces[ti].steps)
    original = f"{step.call.name}({dict(step.call.arguments)})"
    if fault == "tool_error":
        error = "upstream service returned 500 Internal Server Error"
        steps[si] = dataclasses.replace(
            step, result=ToolResult(content=None, error=error, call_id=step.call.id), error=error
        )
        modified = f"{original} -> error: {error}"
    elif fault == "wrong_tool_args":
        args = dict(step.call.arguments)
        if args:
            key = rng.choice(sorted(args))
            args[key] = _wrong_value(args[key], rng)
        else:
            args["unexpected"] = "wrong"
        steps[si] = dataclasses.replace(step, call=dataclasses.replace(step.call, arguments=args))
        modified = f"{step.call.name}({args})"
    elif fault == "drop_tool_call":
        del steps[si]
        modified = ""
    else:
        raise ValueError(f"unknown tool fault {fault!r}")
    traces = list(session.traces)
    traces[ti] = dataclasses.replace(traces[ti], steps=tuple(steps))
    new_session = dataclasses.replace(session, traces=tuple(traces))
    return dataclasses.replace(case, trajectory=new_session), original, modified


def _wrong_value(value: Any, rng: random.Random) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value * rng.choice((2, 10)) + 1
    if isinstance(value, str):
        return value[::-1] if len(value) > 1 else value + "x"
    return None


def _applicable(case: EvalCase, whitebox: bool) -> list[str]:
    faults = []
    turns = _turns(case)
    idx = _agent_turn(turns)
    if idx is not None:
        rng = random.Random(0)
        faults = [f for f in TEXT_FAULTS if _mutate_text(f, turns[idx][1], rng) is not None]
    if whitebox and case.trajectory is not None and case.trajectory.tool_steps():
        faults += list(TOOL_FAULTS)
    return faults


def inject_fault(
    case: EvalCase,
    fault: str | None = None,
    *,
    seed: int = 0,
    faults: Sequence[str] | None = None,
    whitebox: bool | None = None,
) -> tuple[EvalCase, InjectionInfo]:
    """Inject one deterministic fault into ``case`` (same seed and case: same result).

    ``fault`` forces a type (falling back to another applicable one if it does not apply);
    otherwise one of ``faults`` (default: all) is drawn. ``whitebox`` (default: the case has a
    trajectory) enables trajectory faults.
    """
    wb = case.trajectory is not None if whitebox is None else whitebox
    pool = list(faults or FAULT_TYPES)
    unknown = (set(pool) | ({fault} if fault else set())) - set(FAULT_TYPES)
    if unknown:
        raise ValueError(f"unknown fault types {sorted(unknown)}")
    rng = _rng(seed, case.id, fault or "")
    applicable = _applicable(case, wb)
    choices = [f for f in pool if f in applicable]
    if fault is not None and fault in applicable:
        chosen = fault
    elif choices:
        chosen = rng.choice(choices)
    elif applicable:
        chosen = applicable[0]
    else:
        raise ValueError(f"case {case.id!r} has no text or tool step to inject an error into")
    if chosen in TOOL_FAULTS:
        new_case, original, modified = _tool_fault(case, chosen, rng)
        return new_case, InjectionInfo(
            fault=chosen,
            target_role="tool",
            injection_turn=None,
            original_text=original,
            modified_text=modified,
            error_description=_DESCRIPTIONS[chosen],
        )
    turns = _turns(case)
    idx = _agent_turn(turns)
    assert idx is not None
    original = turns[idx][1]
    modified = _mutate_text(chosen, original, rng)
    assert modified is not None
    return _replace_text(case, idx, original, modified), InjectionInfo(
        fault=chosen,
        target_role="agent",
        injection_turn=idx,
        original_text=original,
        modified_text=modified,
        error_description=_DESCRIPTIONS[chosen],
    )


def _clone(
    case: EvalCase, label: str, evaluator: str, info: InjectionInfo | None = None
) -> EvalCase:
    meta = dict(case.metadata)
    meta.update({"label": label, "evaluator": evaluator, "source_case_id": case.id})
    if info is not None:
        meta.update(info.to_metadata())
    return dataclasses.replace(case, id=f"{evaluator}_{label}_{case.id}", metadata=meta)


def _split(cases: Sequence[EvalCase], seed: int, evaluator: str, fail_ratio: float):
    order = list(range(len(cases)))
    _rng(seed, "split", evaluator).shuffle(order)
    n_pass = int(len(order) * (1.0 - fail_ratio))  # 0.5: floor half pass (source)
    return order[:n_pass], order[n_pass:]


def make_balanced_dataset(
    cases: Sequence[EvalCase],
    *,
    evaluators: Sequence[str] = ("default",),
    seed: int = 0,
    faults: Sequence[str] | None = None,
    fail_ratio: float = 0.5,
    whitebox: bool | None = None,
) -> InjectionDataset:
    """Deterministic balanced dataset: per evaluator, clean and fault-injected clones."""
    if not 0.0 <= fail_ratio <= 1.0:
        raise ValueError("fail_ratio must be within [0, 1]")
    clean, faulty, errors = [], [], []
    for evaluator in evaluators:
        pass_idx, fail_idx = _split(cases, seed, evaluator, fail_ratio)
        clean += [_clone(cases[i], "pass", evaluator) for i in pass_idx]
        for i in fail_idx:
            try:
                mutated, info = inject_fault(cases[i], seed=seed, faults=faults, whitebox=whitebox)
            except ValueError as exc:
                errors.append(f"{evaluator}/{cases[i].id}: {exc}")
                continue
            faulty.append(_clone(mutated, "fail", evaluator, info))
    return InjectionDataset(tuple(clean), tuple(faulty), tuple(errors))


# ------------------------------------------------------------------------- judge-assisted
def _conversation_text(turns: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"[Turn {i}] {role.upper()}: {text}" for i, (role, text) in enumerate(turns))


async def _judge_json(
    judge: JudgeModel,
    template: PromptTemplate,
    system_extra: str,
    fields: Mapping[str, str],
    key: str,
) -> dict[str, Any]:
    nonce = make_nonce(key, f"{template.metric_id}@{template.version}", 0)
    trusted = ("role", "turn_index")
    fenced = {k: v if k in trusted else fence(k, v, nonce) for k, v in fields.items()}
    request = JudgeRequest(
        system=template.system + system_extra + fence_clause(nonce),
        user=template.render("simple", fenced),
        output_schema=template.output_schema,
        metadata={"metric_id": template.metric_id, "prompt_version": template.version},
    )
    response = await judge.complete(request)
    return parse(response.text, response.parsed, template.output_schema)


async def _judge_inject(
    judge: JudgeModel, case: EvalCase, criteria: str, whitebox: bool
) -> tuple[EvalCase, InjectionInfo]:
    turns = _turns(case)
    if not turns:
        raise ValueError(f"case {case.id!r} has no messages to edit")
    visibility = WHITEBOX_VISIBILITY if whitebox else BLACKBOX_VISIBILITY
    extra = f"\n\n<evaluation_criteria>\n{criteria}\n</evaluation_criteria>\n\n{visibility}"
    conversation = _conversation_text(turns)
    key = hash_obj(case.to_dict())
    selection = await _judge_json(judge, SELECT_V1, extra, {"conversation": conversation}, key)
    idx = int(selection["turn_index"])
    suggestion = selection["edit_suggestion"]
    if not 0 <= idx < len(turns):  # AgentSight: invalid selection -> the middle agent turn
        idx = _agent_turn(turns) if _agent_turn(turns) is not None else len(turns) - 1
        suggestion = "Make this message score as poorly as possible on the evaluation criteria."
    role, original = turns[idx]
    edit = await _judge_json(
        judge,
        EDIT_V1,
        extra,
        {
            "role": role,
            "turn_index": str(idx),
            "original_text": original,
            "edit_suggestion": suggestion,
            "conversation": conversation,
        },
        key,
    )
    modified = edit["rewritten_message"]
    if modified.strip() == original.strip():
        raise ValueError("the judge returned the message unchanged")
    return _replace_text(case, idx, original, modified), InjectionInfo(
        fault="judge_edit",
        target_role=role,
        injection_turn=idx,
        original_text=original,
        modified_text=modified,
        error_description=edit.get("error_description", ""),
        edit_suggestion=suggestion,
        source="judge",
    )


async def inject_errors(
    cases: Sequence[EvalCase],
    *,
    judge: JudgeModel | None = None,
    evaluators: Sequence[str] = ("default",),
    criteria: Mapping[str, str] | None = None,
    seed: int = 0,
    faults: Sequence[str] | None = None,
    fail_ratio: float = 0.5,
    whitebox: bool | None = None,
    concurrency: int = 4,
) -> InjectionDataset:
    """Balanced dataset with optional judge-assisted edits (AgentSight ``inject_errors``).

    ``criteria`` maps an evaluator to the rubric the judge will be scored on (default: the
    source's "Evaluate the quality of the response based on <name> criteria."). Without a judge
    this equals :func:`make_balanced_dataset`. A failed judge edit falls back to a
    deterministic fault (``fallback_reason`` in the metadata).
    """
    if judge is None:
        return make_balanced_dataset(
            cases,
            evaluators=evaluators,
            seed=seed,
            faults=faults,
            fail_ratio=fail_ratio,
            whitebox=whitebox,
        )
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(evaluator: str, case: EvalCase) -> EvalCase | str:
        wb = case.trajectory is not None if whitebox is None else whitebox
        rubric = (criteria or {}).get(
            evaluator, f"Evaluate the quality of the response based on {evaluator} criteria."
        )
        async with sem:
            try:
                mutated, info = await _judge_inject(judge, case, rubric, wb)
            except (EvalError, ValueError, KeyError) as exc:
                try:
                    mutated, info = inject_fault(case, seed=seed, faults=faults, whitebox=wb)
                except ValueError as inner:
                    return f"{evaluator}/{case.id}: {inner}"
                info = dataclasses.replace(info, fallback_reason=f"{type(exc).__name__}: {exc}")
        return _clone(mutated, "fail", evaluator, info)

    clean: list[EvalCase] = []
    tasks = []
    for evaluator in evaluators:
        pass_idx, fail_idx = _split(cases, seed, evaluator, fail_ratio)
        clean += [_clone(cases[i], "pass", evaluator) for i in pass_idx]
        tasks += [one(evaluator, cases[i]) for i in fail_idx]
    results = await asyncio.gather(*tasks)
    faulty = tuple(r for r in results if isinstance(r, EvalCase))
    errors = tuple(r for r in results if isinstance(r, str))
    return InjectionDataset(tuple(clean), faulty, errors)
