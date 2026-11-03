"""``SimulatedUserTarget``: a judge-played user drives a multi-turn conversation.

Port of AgentSight ``agent_sim`` ``UserSimulator`` + ``generate_conversation``: each turn the user
judge writes the next user message from the persona's competency level, the scenario and the
history (``USER_MESSAGE_PROMPT``, verbatim); the inner target answers. The transcript goes into
``case.conversation``, the last answer into ``output``, persona / scenario / stop reason into
``metadata``.

Each inner call gets ``input`` = the user message and ``conversation`` = the turns before it.
The loop stops at ``max_turns``, when ``stop_when(conversation)`` is true, when the user judge
replies ``done: true`` (or an empty message), or when the inner target fails.
"""

from __future__ import annotations

import dataclasses
import inspect
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..exceptions import JudgeParseError
from ..hashing import hash_obj
from ..judges.base import JudgeModel, JudgeRequest
from ..judges.fencing import fence, fence_clause, make_nonce
from ..judges.prompts import PromptTemplate
from ..judges.structured import REPAIR_INSTRUCTION, parse
from ..types.case import AppMetrics, EvalCase, Message, content_text
from ..types.simulation import CompetencyLevel, Persona, Scenario
from ..types.trajectory import Session
from .targets import Target

StopWhen = Callable[[tuple[Message, ...]], Any]  # -> bool (sync or async)

USER_MESSAGE_PROMPT = """
Generate the next USER message in this customer support conversation.

COMPETENCY LEVEL:
{competency_level_description}

SCENARIO:
- Intent: {intent}
- Sub-intent: {sub_intent}
- Stress Axes: {stress_axes}
- Expected Failures: {expected_failures}
- Relevant Entities: {entities}

CONVERSATION HISTORY:
{history}

Generate ONLY the user's next message. Make it natural and consistent with:
- The competency level characteristics
- The scenario intent and sub-intent
- The conversation flow so far
- The stress conditions (ambiguity, incomplete info, etc.)

IMPORTANT: Keep the message under {utterance_length} words.

Return only the user message, no labels or formatting.
"""

USER_SYSTEM_V1 = """You play the USER in a simulated conversation with an AI agent under test.

Stay in the persona and the scenario you are given. Never reveal that you are simulated.

## Output Format

Return only a JSON object: {"message": "<the user's next message>", "done": false}.
Set "done" to true (with an empty message) only when the user's goal is fully resolved or the
user would end the conversation now."""

USER_MESSAGE_V1 = PromptTemplate(
    metric_id="simulation.user_message",
    version="v1",
    system=USER_SYSTEM_V1,
    parts={"simple": USER_MESSAGE_PROMPT},
    output_schema={
        "type": "object",
        "required": ["message"],
        "properties": {"message": {"type": "string"}, "done": {"type": "boolean"}},
    },
    source="agentsight:agent_sim.USER_MESSAGE_PROMPT",
)


async def ask_json(
    judge: JudgeModel,
    template: PromptTemplate,
    mode: str,
    fields: Mapping[str, Any],
    *,
    key: str,
    trusted: Sequence[str] = (),
) -> dict[str, Any]:
    """One judge call; untrusted fields are fenced; one repair re-ask on a parse error.

    ``trusted`` names fields that are our own instructions (persona, scenario) and stay unfenced.
    """
    nonce = make_nonce(hash_obj(dict(fields)), f"{template.metric_id}@{template.version}", 0, key)
    rendered = {k: str(v) if k in trusted else fence(k, str(v), nonce) for k, v in fields.items()}
    request = JudgeRequest(
        system=template.system + fence_clause(nonce),
        user=template.render(mode, rendered),
        output_schema=template.output_schema,
        metadata={"metric_id": template.metric_id, "prompt_version": template.version, "tag": key},
    )
    response = await judge.complete(request)
    try:
        return parse(response.text, response.parsed, template.output_schema)
    except JudgeParseError as first:
        repair = dataclasses.replace(
            request, user=request.user + "\n\n" + REPAIR_INSTRUCTION.format(problems=first)
        )
        response = await judge.complete(repair)
        return parse(response.text, response.parsed, template.output_schema)


def history_text(conversation: Sequence[Message]) -> str:
    """``agent_sim`` history format (``USER: ...`` / ``AGENT: ...``)."""
    if not conversation:
        return "No previous conversation"
    names = {"assistant": "AGENT"}
    return "\n".join(f"{names.get(m.role, m.role.upper())}: {m.content}" for m in conversation)


async def user_message(
    judge: JudgeModel,
    persona: Persona,
    scenario: Scenario,
    sub_intent: str,
    conversation: Sequence[Message],
    *,
    key: str = "",
) -> tuple[str, bool]:
    """The simulated user's next message and whether the user is done."""
    fields = {
        "competency_level_description": persona.description(),
        "intent": scenario.intent,
        "sub_intent": sub_intent,
        "stress_axes": [a.value for a in scenario.stress_axes],
        "expected_failures": [f.value for f in scenario.expected_failures],
        "entities": list(scenario.relevant_entities),
        "utterance_length": persona.utterance_length,
        "history": history_text(conversation),
    }
    trusted = [k for k in fields if k != "history"]
    parsed = await ask_json(
        judge, USER_MESSAGE_V1, "simple", fields, key=key or "user", trusted=trusted
    )
    message = str(parsed.get("message", "")).strip()
    return message, bool(parsed.get("done", False)) or not message


def _sum(values: Sequence[float | int | None]) -> Any:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _merge_metrics(runs: Sequence[AppMetrics | None], latency_ms: float, error: str | None):
    apps = [a for a in runs if a is not None]
    return AppMetrics(
        latency_ms=latency_ms,
        ttft_ms=apps[0].ttft_ms if apps else None,
        input_tokens=_sum([a.input_tokens for a in apps]),
        output_tokens=_sum([a.output_tokens for a in apps]),
        cache_read_tokens=_sum([a.cache_read_tokens for a in apps]),
        cache_write_tokens=_sum([a.cache_write_tokens for a in apps]),
        cost_usd=_sum([a.cost_usd for a in apps]),
        error=error,
    )


def _merge_sessions(sessions: Sequence[Session | None]) -> Session | None:
    present = [s for s in sessions if s is not None]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return Session(
        id=present[0].id,
        traces=tuple(t for s in present for t in s.traces),
        warnings=tuple(w for s in present for w in s.warnings),
    )


class SimulatedUserTarget:
    """A :class:`Target` whose user side is simulated by ``user_judge``.

    ``persona`` / ``scenario`` / ``sub_intent`` default to ``case.metadata`` (keys ``persona``,
    ``scenario``, ``sub_intent``); the scenario falls back to the case input as the intent.
    """

    def __init__(
        self,
        inner_target: Target,
        persona: Persona | CompetencyLevel | str | Mapping[str, Any] | None = None,
        scenario: Scenario | Mapping[str, Any] | str | None = None,
        user_judge: JudgeModel | None = None,
        max_turns: int = 5,
        *,
        sub_intent: str | None = None,
        stop_when: StopWhen | None = None,
    ):
        if user_judge is None:
            raise ValueError("SimulatedUserTarget needs a user_judge")
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self.inner = inner_target
        self.persona = None if persona is None else Persona.coerce(persona)
        self.scenario = None if scenario is None else Scenario.coerce(scenario)
        self.user_judge = user_judge
        self.max_turns = max_turns
        self.sub_intent = sub_intent
        self.stop_when = stop_when

    def _setup(self, case: EvalCase) -> tuple[Persona, Scenario, str]:
        persona = self.persona or Persona.coerce(case.metadata.get("persona"))
        scenario = self.scenario
        if scenario is None:
            raw = case.metadata.get("scenario")
            if raw is None:
                intent = content_text(case.input)
                if not intent:
                    raise ValueError("no scenario: pass one or set case.metadata['scenario']")
                raw = intent
            scenario = Scenario.coerce(raw)
        sub = self.sub_intent or case.metadata.get("sub_intent")
        if not sub:
            sub = scenario.sub_intent[0] if scenario.sub_intent else scenario.intent
        return persona, scenario, str(sub)

    async def _stop(self, conversation: tuple[Message, ...]) -> bool:
        if self.stop_when is None:
            return False
        out = self.stop_when(conversation)
        if inspect.isawaitable(out):
            out = await out
        return bool(out)

    async def invoke(self, case: EvalCase) -> EvalCase:
        start = time.perf_counter()
        history: list[Message] = list(case.conversation)
        apps: list[AppMetrics | None] = []
        sessions: list[Session | None] = []
        output: Any = None
        error: str | None = None
        stop_reason = "max_turns"
        turns = 0
        persona = scenario = sub = None
        try:
            persona, scenario, sub = self._setup(case)
            for turn in range(self.max_turns):
                message, done = await user_message(
                    self.user_judge, persona, scenario, sub, history, key=f"{case.id}:{turn}"
                )
                if done:
                    stop_reason = "user_done"
                    break
                run = await self.inner.invoke(
                    dataclasses.replace(
                        case, input=message, output=None, conversation=tuple(history)
                    )
                )
                history.append(Message(role="user", content=message))
                apps.append(run.app_metrics)
                sessions.append(run.trajectory)
                if run.app_metrics is not None and run.app_metrics.error:
                    error, stop_reason = run.app_metrics.error, "target_error"
                    break
                output = run.output
                reply = content_text(run.output) or ""
                history.append(Message(role="assistant", content=reply))
                turns += 1
                if await self._stop(tuple(history)):
                    stop_reason = "stop_when"
                    break
        except Exception as exc:  # noqa: BLE001 - failures are recorded, not raised
            error, stop_reason = f"{type(exc).__name__}: {exc}", "error"
        metadata = dict(case.metadata)
        if persona is not None and scenario is not None:
            metadata.update(persona=persona.to_dict(), scenario=scenario.to_dict(), sub_intent=sub)
        metadata["simulation"] = {
            "turns": turns,
            "max_turns": self.max_turns,
            "stop_reason": stop_reason,
        }
        first_user = next((m.content for m in history if m.role == "user"), None)
        return dataclasses.replace(
            case,
            input=case.input if case.input is not None else first_user,
            output=output,
            conversation=tuple(history),
            trajectory=_merge_sessions(sessions) or case.trajectory,
            app_metrics=_merge_metrics(apps, (time.perf_counter() - start) * 1000, error),
            metadata=metadata,
        )


__all__ = ["USER_MESSAGE_V1", "SimulatedUserTarget", "ask_json", "history_text", "user_message"]
